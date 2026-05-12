"""Command-line interface for Job Autopilot (Typer)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog
import typer

from job_autopilot import __version__
from job_autopilot.discover import WorkdaySource
from job_autopilot.filters import (
    apply_location_filter_to_resolved,
    apply_scrape_filter,
    finalize_filter_report,
    load_filters,
)
from job_autopilot.logging_config import configure_logging
from job_autopilot.models import RawJob, RubricConfig, SourcesConfig
from job_autopilot.score import build_client, load_resume, score_all_jobs
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
        example = path.with_name("sources.example.json")
        typer.secho(
            f"❌ sources.json not found at {path}",
            fg=typer.colors.RED,
            err=True,
        )
        if example.exists():
            typer.secho(
                f"💡 Copy the example to get started:\n"
                f"   Copy-Item {example} {path}",
                fg=typer.colors.YELLOW,
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
    filter_report: dict | None = None,
) -> None:
    """Append a run summary entry to data/runs.json."""
    runs = read_json(settings.runs_file, default=[])
    if not isinstance(runs, list):
        runs = []
    entry: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "discovered": discovered,
        "added": added,
        "updated": updated,
        "duration_sec": round(duration_sec, 2),
        "ok": ok,
        "error": error,
    }
    if filter_report is not None:
        entry["filter_report"] = filter_report
    runs.append(entry)
    write_json(settings.runs_file, runs)


def _print_top(
    counts: dict,
    title: str,
    *,
    limit: int = 10,
    indent: str = "  ",
) -> None:
    """Pretty-print a count dict (already sorted desc)."""
    if not counts:
        return
    typer.echo("")
    typer.secho(f"{title}:", fg=typer.colors.YELLOW)
    for k, v in list(counts.items())[:limit]:
        typer.echo(f"{indent}{v:5d}  {k}")


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
            typer.echo(f"  Jobs in raw_jobs.json: {len(data)}")
            with_jd = sum(1 for j in data if j.get("description"))
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
        fetch_details=False,
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

    # -------- 2. Apply filters (two-pass: title+age, then location with resolve) --------
    filter_report: dict | None = None

    if no_filter:
        kept_jobs = jobs
        typer.secho("⚙  Filters bypassed (--no-filter)", fg=typer.colors.YELLOW)
    else:
        filters_config = load_filters(settings.config_dir)
        override_locations = list(location) if location else None

        kept_jobs, pending_jobs, report_state = apply_scrape_filter(
            jobs,
            filters_config,
            override_location_ids=override_locations,
            override_max_age_days=max_age,
        )
        overall = report_state["_overall"]

        if overall.get("filters_off"):
            typer.secho(
                "⚙  Filters disabled in config (scrape_filter.enabled=false)",
                fg=typer.colors.YELLOW,
            )
        else:
            typer.secho(
                f"🔎 Pass 1: kept {overall['kept']}/{overall['input']} "
                f"(title:{overall['dropped_title']} "
                f"age:{overall['dropped_age']} "
                f"loc:{overall['dropped_loc']} "
                f"pending:{overall['pending_loc']})",
                fg=typer.colors.CYAN,
            )

        # Pass 2: resolve real locations for placeholder jobs
        if pending_jobs:
            typer.secho(
                f"🔍 Resolving real locations for "
                f"{len(pending_jobs)} multi-city posting(s)...",
                fg=typer.colors.CYAN,
            )
            resolve_started = datetime.now(timezone.utc)
            try:
                resolved = asyncio.run(source.resolve_pending_locations(pending_jobs))
                extra_kept = apply_location_filter_to_resolved(resolved, report_state)
                kept_jobs = kept_jobs + extra_kept
                rdur = (datetime.now(timezone.utc) - resolve_started).total_seconds()
                typer.secho(
                    f"✓ Recovered {len(extra_kept)}/{len(pending_jobs)} "
                    f"in {rdur:.1f}s.",
                    fg=typer.colors.GREEN,
                )
            except Exception as exc:
                logger.warning("location_resolve_failed", error=str(exc))
                typer.secho(
                    f"⚠ Location resolve failed: {exc} — pending jobs dropped",
                    fg=typer.colors.YELLOW,
                )

        filter_report = finalize_filter_report(report_state)

    # -------- 3. Enrich with JD --------
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
            filter_report=filter_report,
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
        filter_report=filter_report,
    )

    by_company: dict[str, int] = {}
    for j in enriched_jobs:
        by_company[j.company_display] = by_company.get(j.company_display, 0) + 1
    if by_company:
        typer.echo("\n📊 Per-company (after filtering):")
        for name, count in sorted(by_company.items(), key=lambda kv: -kv[1]):
            typer.echo(f"   {name:20s} {count}")


@app.command()
def score(
    force: bool = typer.Option(
        False, "--force", "-f",
        help="Re-score every job, ignoring cached results.",
    ),
    limit: int = typer.Option(
        None, "--limit", "-n",
        help="Only score the first N jobs (for testing).",
    ),
    concurrency: int = typer.Option(
        5, "--concurrency", "-c",
        help="Max parallel LLM calls (default: 5).",
    ),
    model: str = typer.Option(
        "gpt-4o-mini", "--model",
        help="OpenAI model to use.",
    ),
) -> None:
    """Score every job in raw_jobs.json against your resume and save to scored_jobs.json."""
    from dotenv import load_dotenv
    load_dotenv()

    configure_logging(settings.log_level)

    try:
        resume = load_resume(settings.resume_file)
    except Exception as exc:
        typer.secho(f"❌ Resume error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    rubric_path = settings.config_dir / "rubric.json"
    if rubric_path.exists():
        rubric = RubricConfig.model_validate(
            json.loads(rubric_path.read_text(encoding="utf-8"))
        )
    else:
        rubric = RubricConfig.default()
        typer.secho(
            "ℹ️  No config/rubric.json found — using default weights.",
            fg=typer.colors.YELLOW,
        )

    scored_path = settings.data_dir / "scored_jobs.json"

    typer.secho(
        f"🤖 Scoring jobs from {settings.raw_jobs_file.name}",
        fg=typer.colors.CYAN,
    )
    typer.secho(
        f"   Resume:      {resume.path.name} ({resume.estimated_tokens} tokens)",
        fg=typer.colors.CYAN,
    )
    typer.secho(
        f"   Model:       {model}   |   Concurrency: {concurrency}   |   "
        f"Force: {force}   |   Limit: {limit or 'all'}",
        fg=typer.colors.CYAN,
    )
    typer.echo("")

    client = build_client()

    summary = asyncio.run(
        score_all_jobs(
            raw_jobs_path=settings.raw_jobs_file,
            scored_jobs_path=scored_path,
            resume=resume,
            rubric=rubric,
            client=client,
            model=model,
            concurrency=concurrency,
            force=force,
            limit=limit,
        )
    )

    typer.echo("")
    typer.secho("═══════════════════════════════════════════", fg=typer.colors.GREEN)
    typer.secho(f"  Total jobs:    {summary.total}", fg=typer.colors.GREEN)
    typer.secho(f"  Newly scored:  {summary.scored}", fg=typer.colors.GREEN)
    typer.secho(f"  Cached (skip): {summary.cached}",
                fg=typer.colors.YELLOW if summary.cached else typer.colors.GREEN)
    if summary.failed:
        typer.secho(f"  Failed:        {summary.failed}", fg=typer.colors.RED)
    typer.secho(f"  Duration:      {summary.duration_sec}s", fg=typer.colors.GREEN)
    if summary.scored:
        cost = summary.estimated_cost(model)
        typer.secho(f"  Tokens:        {summary.total_tokens:,}",
                    fg=typer.colors.GREEN)
        typer.secho(f"  Est. cost:     ${cost:.4f}",
                    fg=typer.colors.GREEN)
    typer.secho("═══════════════════════════════════════════", fg=typer.colors.GREEN)

    if summary.errors:
        typer.echo("")
        typer.secho(f"⚠ {len(summary.errors)} error(s):", fg=typer.colors.YELLOW)
        for e in summary.errors[:10]:
            typer.echo(f"   • {e}")

    if scored_path.exists():
        scored_data = json.loads(scored_path.read_text(encoding="utf-8"))
        scored_data.sort(key=lambda s: s.get("overall_score", 0), reverse=True)
        top = scored_data[:5]
        if top:
            typer.echo("")
            typer.secho("🏆 Top matches:", fg=typer.colors.CYAN)
            raw_lookup = {
                j["id"]: j
                for j in json.loads(
                    settings.raw_jobs_file.read_text(encoding="utf-8")
                )
            }
            for s in top:
                score_val = s.get("overall_score", 0)
                rec = s.get("recommendation", "?").upper()
                rj = raw_lookup.get(s["id"], {})
                title = rj.get("title", "(unknown)")[:50]
                company = rj.get("company_display", "")
                typer.echo(
                    f"   {score_val:3d}  [{rec:8s}]  [{company:12s}] {title}"
                )


@app.command()
def stats(
    last: int = typer.Option(
        1, "--last", "-n",
        help="Show the last N runs (default: 1).",
    ),
) -> None:
    """Show discovery + filter statistics from past runs."""
    history = read_json(settings.runs_file, default=[])
    if not isinstance(history, list) or not history:
        typer.echo("No runs recorded yet.")
        return

    last_n = history[-last:][::-1]

    for i, entry in enumerate(last_n):
        idx = len(history) - i
        typer.secho(
            f"\nRun #{idx}  ({entry.get('timestamp', '?')})",
            fg=typer.colors.CYAN, bold=True,
        )
        typer.echo("─" * 60)
        typer.echo(f"  Discovered:  {entry.get('discovered', 0)}")
        typer.echo(f"  Added:       {entry.get('added', 0)}")
        typer.echo(f"  Updated:     {entry.get('updated', 0)}")
        typer.echo(f"  Duration:    {entry.get('duration_sec', 0)}s")
        typer.echo(f"  Status:      {'OK' if entry.get('ok') else 'FAILED'}")
        if entry.get("error"):
            typer.echo(f"  Error:       {entry['error']}")

        report = entry.get("filter_report")
        if not report:
            typer.echo("  (No filter detail recorded for this run)")
            continue

        typer.echo("")
        typer.secho("Overall:", fg=typer.colors.YELLOW)
        typer.echo(f"  Input:                  {report.get('input', 0)}")
        typer.echo(f"  Kept:                   {report.get('kept', 0)}")
        typer.echo(f"  Dropped by location:    {report.get('dropped_loc', 0)}")
        typer.echo(f"  Dropped by title:       {report.get('dropped_title', 0)}")
        typer.echo(f"  Dropped by age:         {report.get('dropped_age', 0)}")

        _print_top(report.get("dropped_by_location", {}),
                   "Top dropped locations", limit=10)
        _print_top(report.get("dropped_by_title_keyword", {}),
                   "Top dropped title keywords", limit=10)
        _print_top(report.get("dropped_by_age_bucket", {}),
                   "Dropped by age bucket", limit=10)

        per_co = report.get("per_company", {})
        if per_co:
            typer.echo("")
            typer.secho("Per-company breakdown:", fg=typer.colors.YELLOW)
            sorted_co = sorted(
                per_co.items(), key=lambda x: -x[1].get("input", 0)
            )
            for company, cs in sorted_co:
                typer.echo("")
                typer.secho(f"  {company}", bold=True)
                typer.echo(
                    f"    Input: {cs.get('input', 0)}    "
                    f"Kept: {cs.get('kept', 0)}    "
                    f"Loc: {cs.get('dropped_loc', 0)}    "
                    f"Title: {cs.get('dropped_title', 0)}    "
                    f"Age: {cs.get('dropped_age', 0)}"
                )
                _print_top(
                    cs.get("dropped_by_location", {}),
                    "    Top locations dropped",
                    limit=5, indent="      ",
                )
                _print_top(
                    cs.get("dropped_by_title_keyword", {}),
                    "    Top title keywords dropped",
                    limit=5, indent="      ",
                )


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()