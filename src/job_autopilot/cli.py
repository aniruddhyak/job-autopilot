"""Command-line interface for Job Autopilot (Typer)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog
import typer

from job_autopilot import __version__
from job_autopilot.discover import WorkdaySource
from job_autopilot.filters import apply_scrape_filter, load_filters
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
def status() -> None:
    """Show current data status (job count, paths)."""
    raw = settings.raw_jobs_file
    if raw.exists():
        try:
            data = json.loads(raw.read_text(encoding="utf-8"))
            print_total = f"  Jobs in raw_jobs.json: {len(data)}"
            with_jd = sum(1 for j in data if j.get("description"))
            typer.echo(print_total)
            typer.echo(f"  With JD:               {with_jd}")
        except Exception:
            typer.echo("  raw_jobs.json: present but unreadable")
    else:
        typer.echo("  raw_jobs.json: not found (run `discover` first)")
    typer.echo(f"  Data dir:  {settings.data_dir}")
    typer.echo(f"  Config:    {settings.config_dir}")


@app.command()
def discover(
    max_per_org: int = typer.Option(
        None, "--max-per-org", "-m",
        help="Cap jobs scraped per company (default: no cap).",
    ),
    list_only: bool = typer.Option(
        False, "--list-only",
        help="Skip JD enrichment (faster; no description/qualifications).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Scrape but don't write to raw_jobs.json.",
    ),
    location: list[str] = typer.Option(
        None, "--location", "-l",
        help="Override location filter (repeat flag). E.g., -l usa -l remote",
    ),
    no_filter: bool = typer.Option(
        False, "--no-filter",
        help="Bypass config/filters.json and keep every scraped job.",
    ),
    max_age: int = typer.Option(
        None, "--max-age", "-a",
        help="Override max age in days (e.g., -a 2 keeps last 2 days).",
    ),
    polite_delay: float = typer.Option(
        0.1, "--polite-delay",
        help="Seconds between detail fetches (default: 0.1).",
    ),
) -> None:
    """Discover jobs: list scrape -> filter -> JD enrichment -> save."""
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
        fetch_details=False,           # we'll enrich manually after filtering
        polite_delay=polite_delay,
    )

    # -------- 1. List scrape --------
    try:
        jobs: list[RawJob] = asyncio.run(source.discover())
    except Exception as exc:  # pragma: no cover
        logger.error(
            "discover_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        typer.secho(f"❌ Discovery failed: {exc}", fg=typer.colors.RED, err=True)
        _record_run(
            sources=["workday"],
            discovered=0, added=0, updated=0,
            duration_sec=(datetime.now(timezone.utc) - started).total_seconds(),
            ok=False, error=str(exc),
        )
        raise typer.Exit(code=1) from exc

    list_duration = (datetime.now(timezone.utc) - started).total_seconds()
    typer.secho(
        f"\n✓ Listed {len(jobs)} job(s) in {list_duration:.1f}s.",
        fg=typer.colors.GREEN,
    )

    # -------- 2. Apply filters --------
    if no_filter:
        kept_jobs = jobs
        typer.secho("⚙  Filters bypassed (--no-filter)", fg=typer.colors.YELLOW)
    else:
        filters_config = load_filters(settings.config_dir)
        override_locations = list(location) if location else None
        kept_jobs, stats = apply_scrape_filter(
            jobs,
            filters_config,
            override_location_ids=override_locations,
            override_max_age_days=max_age,
        )
        if stats["filters_off"]:
            typer.secho(
                "⚙  Filters disabled in config (scrape_filter.enabled=false)",
                fg=typer.colors.YELLOW,
            )
        else:
            typer.secho(
                f"🔎 Filtered: kept {stats['kept']}/{stats['input']} "
                f"(loc:{stats['dropped_loc']} title:{stats['dropped_title']} "
                f"age:{stats['dropped_age']})",
                fg=typer.colors.CYAN,
            )

    # -------- 3. Enrich with JD (default: yes) --------
    if list_only:
        typer.secho("📄 Skipping JD enrichment (--list-only)", fg=typer.colors.YELLOW)
        enriched_jobs = kept_jobs
    elif kept_jobs:
        typer.secho(
            f"📄 Fetching JDs for {len(kept_jobs)} job(s)...",
            fg=typer.colors.CYAN,
        )
        enrich_started = datetime.now(timezone.utc)
        try:
            enriched_jobs = asyncio.run(source.enrich_details(kept_jobs))
            enrich_duration = (
                datetime.now(timezone.utc) - enrich_started
            ).total_seconds()
            with_jd = sum(1 for j in enriched_jobs if j.description)
            typer.secho(
                f"✓ Enriched {with_jd}/{len(enriched_jobs)} with JD "
                f"in {enrich_duration:.1f}s.",
                fg=typer.colors.GREEN,
            )
        except Exception as exc:
            logger.error("enrich_failed", error=str(exc))
            typer.secho(
                f"⚠ Enrichment failed: {exc} — saving without JD",
                fg=typer.colors.YELLOW,
            )
            enriched_jobs = kept_jobs
    else:
        enriched_jobs = kept_jobs

    duration = (datetime.now(timezone.utc) - started).total_seconds()

    # -------- 4. Save --------
    if dry_run:
        typer.secho(
            "(dry run — not writing raw_jobs.json)",
            fg=typer.colors.YELLOW,
        )
        _record_run(
            sources=["workday"],
            discovered=len(jobs),
            added=0,
            updated=0,
            duration_sec=duration,
            ok=True,
        )
        return

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    added, updated = upsert_models_by_id(
        settings.raw_jobs_file,
        enriched_jobs,
        model=RawJob,
    )

    typer.secho(
        f"💾 Saved to {settings.raw_jobs_file}: "
        f"+{added} new, ~{updated} updated. (total {duration:.1f}s)",
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

    by_company: dict[str, int] = {}
    for j in enriched_jobs:
        by_company[j.company_display] = by_company.get(j.company_display, 0) + 1
    if by_company:
        typer.echo("\n📊 Per-company (after filtering):")
        for name, count in sorted(by_company.items(), key=lambda kv: -kv[1]):
            typer.echo(f"   {name:20s} {count}")


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()