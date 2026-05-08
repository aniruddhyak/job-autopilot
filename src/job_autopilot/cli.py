"""Command-line interface for Job Autopilot (Typer)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import structlog
import typer

from job_autopilot import __version__
from job_autopilot.discover import WorkdaySource
from job_autopilot.logging_config import configure_logging
from job_autopilot.models import RawJob, SourcesConfig
from job_autopilot.settings import settings
from job_autopilot.storage import read_json, upsert_models_by_id, write_json

app = typer.Typer(
    name="job-autopilot",
    help="AI-powered job discovery, scoring, and tracking.",
    no_args_is_help=True,
    add_completion=False,
)

logger = structlog.get_logger(__name__)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _load_sources_config() -> SourcesConfig:
    path = settings.sources_file
    if not path.exists():
        typer.secho(
            f"❌ sources.json not found at {path}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    raw = json.loads(path.read_text(encoding="utf-8"))
    return SourcesConfig.model_validate(raw)


def _record_run(
    *,
    sources: list[str],
    discovered: int,
    added: int,
    updated: int,
    duration_sec: float,
    ok: bool,
    error: str | None = None,
) -> None:
    """Append a run summary entry to data/runs.json."""
    runs = read_json(settings.runs_file, default=[])
    if not isinstance(runs, list):
        runs = []
    runs.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sources": sources,
            "discovered": discovered,
            "added": added,
            "updated": updated,
            "duration_sec": round(duration_sec, 2),
            "ok": ok,
            "error": error,
        }
    )
    write_json(settings.runs_file, runs)


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------

@app.command()
def version() -> None:
    """Print the installed Job Autopilot version."""
    typer.echo(f"job-autopilot {__version__}")


@app.command()
def discover(
    max_per_org: int = typer.Option(
        None,
        "--max-per-org",
        "-m",
        help="Cap jobs scraped per company (default: no cap).",
    ),
    fetch_details: bool = typer.Option(
        False,
        "--details/--no-details",
        help="Also fetch full job descriptions (slower).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Scrape but don't write to raw_jobs.json.",
    ),
) -> None:
    """Discover jobs from configured sources and update data/raw_jobs.json."""
    configure_logging(settings.log_level)

    cfg = _load_sources_config()
    workday_orgs = cfg.enabled_workday()

    if not workday_orgs:
        typer.secho(
            "⚠️  No enabled Workday orgs in config/sources.json. Nothing to do.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=0)

    typer.secho(
        f"🔍 Discovering from {len(workday_orgs)} Workday org(s): "
        + ", ".join(o.display_name for o in workday_orgs),
        fg=typer.colors.CYAN,
    )

    started = datetime.now(timezone.utc)

    source = WorkdaySource(
        orgs=workday_orgs,
        max_jobs_per_org=max_per_org,
        fetch_details=fetch_details,
    )

    try:
        jobs: list[RawJob] = asyncio.run(source.discover())
    except Exception as exc:  # pragma: no cover — defensive
        logger.error("discover_failed", error=str(exc), error_type=type(exc).__name__)
        typer.secho(f"❌ Discovery failed: {exc}", fg=typer.colors.RED, err=True)
        _record_run(
            sources=["workday"],
            discovered=0,
            added=0,
            updated=0,
            duration_sec=(datetime.now(timezone.utc) - started).total_seconds(),
            ok=False,
            error=str(exc),
        )
        raise typer.Exit(code=1) from exc

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    typer.secho(
        f"\n✓ Scraped {len(jobs)} job(s) in {duration:.1f}s.",
        fg=typer.colors.GREEN,
    )

    if dry_run:
        typer.secho("(dry run — not writing raw_jobs.json)", fg=typer.colors.YELLOW)
        _record_run(
            sources=["workday"],
            discovered=len(jobs),
            added=0,
            updated=0,
            duration_sec=duration,
            ok=True,
        )
        return

    # Persist
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    added, updated = upsert_models_by_id(
        settings.raw_jobs_file,
        jobs,
        model=RawJob,
    )

    typer.secho(
        f"💾 Saved to {settings.raw_jobs_file}: +{added} new, ~{updated} updated.",
        fg=typer.colors.GREEN,
    )

    _record_run(
        sources=["workday"],
        discovered=len(jobs),
        added=added,
        updated=updated,
        duration_sec=duration,
        ok=True,
    )

    # Pretty per-company breakdown
    by_company: dict[str, int] = {}
    for j in jobs:
        by_company[j.company_display] = by_company.get(j.company_display, 0) + 1
    typer.echo("\n📊 Per-company:")
    for name, count in sorted(by_company.items(), key=lambda kv: -kv[1]):
        typer.echo(f"   {name:20s} {count}")


# ----------------------------------------------------------------------
# Entry point: ``python -m job_autopilot.cli`` → runs Typer
# ----------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()