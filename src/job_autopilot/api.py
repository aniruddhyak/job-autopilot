"""FastAPI application — read-only dashboard backend + application tracking."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from job_autopilot import __version__
from job_autopilot.discover import WorkdaySource
from job_autopilot.filters import (
    apply_location_filter_to_resolved,
    apply_scrape_filter,
    finalize_filter_report,
    load_filters,
)
from job_autopilot.logging_config import configure_logging
from job_autopilot.models import (
    Application,
    ApplicationStatus,
    RawJob,
    RubricConfig,
    ScoredJob,
    SourcesConfig,
)
from job_autopilot.score import build_client, load_resume, score_all_jobs
from job_autopilot.settings import PROJECT_ROOT, settings
from job_autopilot.storage import (
    compute_status_stats,
    delete_application,
    load_applications,
    read_json,
    read_json_list_as,
    save_application,
    upsert_models_by_id,
    write_json,
)

logger = structlog.get_logger(__name__)

FRONTEND_DIR = PROJECT_ROOT / "frontend"
SCORED_JOBS_FILE = settings.data_dir / "scored_jobs.json"
APPLICATIONS_FILE = settings.applications_file


# ----------------------------------------------------------------------
# Response models
# ----------------------------------------------------------------------

class CompanyStats(BaseModel):
    id: str
    name: str
    job_count: int
    last_updated: datetime | None = None
    top_score: int | None = None
    top_recommendation: str | None = None
    apply_count: int = 0
    consider_count: int = 0
    skip_count: int = 0
    scored_count: int = 0
    interested_count: int = 0
    applied_count: int = 0
    interview_count: int = 0
    offer_count: int = 0
    rejected_count: int = 0


class CompaniesResponse(BaseModel):
    total_jobs: int
    company_count: int
    last_refreshed: datetime | None = None
    companies: list[CompanyStats]


class JobItem(BaseModel):
    id: str
    title: str
    location: str | None = None
    posted_on: str | None = None
    url: str
    employment_type: str | None = None
    job_family: str | None = None
    discovered_at: datetime
    description_text: str | None = None
    score: int | None = None
    recommendation: str | None = None
    score_summary: str | None = None
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    dimensions: dict[str, int] | None = None
    scored_at: datetime | None = None
    application: Application | None = None


class CompanyJobsResponse(BaseModel):
    company: CompanyStats
    jobs: list[JobItem]
    avg_score: float | None = None
    apply_count: int = 0
    consider_count: int = 0
    skip_count: int = 0
    unscored_count: int = 0


class DiscoverResponse(BaseModel):
    ok: bool
    discovered: int = 0
    kept: int = 0
    added: int = 0
    updated: int = 0
    duration_sec: float = 0.0
    error: str | None = None


class ScoreResponse(BaseModel):
    ok: bool
    total: int = 0
    scored: int = 0
    cached: int = 0
    failed: int = 0
    tokens_used: int = 0
    est_cost_usd: float = 0.0
    duration_sec: float = 0.0
    error: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = Field(default_factory=lambda: __version__)


class ApplicationUpdateRequest(BaseModel):
    status: ApplicationStatus
    note: str = Field("", description="Note attached to this transition.")
    notes: str | None = Field(None, description="Replace running notes.")
    applied_at: datetime | None = Field(None, description="Optional override.")


class ApplicationStatsResponse(BaseModel):
    interested: int = 0
    applied: int = 0
    interview: int = 0
    offer: int = 0
    rejected: int = 0
    total: int = 0


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _load_jobs() -> list[RawJob]:
    path = settings.raw_jobs_file
    if not path.exists():
        return []
    return read_json_list_as(path, RawJob)


def _load_scored_jobs() -> dict[str, ScoredJob]:
    if not SCORED_JOBS_FILE.exists():
        return {}
    try:
        raw = json.loads(SCORED_JOBS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, ScoredJob] = {}
    for item in raw:
        try:
            sj = ScoredJob.model_validate(item)
            out[sj.id] = sj
        except Exception:
            continue
    return out


def _load_sources_config() -> SourcesConfig:
    path = settings.sources_file
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"sources.json not found at {path}",
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return SourcesConfig.model_validate(raw)


def _load_filters_config() -> dict[str, Any]:
    path = settings.config_dir / "filters.json"
    if not path.exists():
        return {"locations": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _last_successful_run_time() -> datetime | None:
    runs = read_json(settings.runs_file, default=[])
    if not isinstance(runs, list):
        return None
    for entry in reversed(runs):
        if isinstance(entry, dict) and entry.get("ok") and entry.get("timestamp"):
            try:
                return datetime.fromisoformat(entry["timestamp"])
            except (TypeError, ValueError):
                continue
    return None


def _aggregate_companies(
    jobs: list[RawJob],
    scores: dict[str, ScoredJob],
    apps: dict[str, Application],
) -> list[CompanyStats]:
    by_company: dict[str, dict[str, Any]] = {}
    for j in jobs:
        slot = by_company.setdefault(
            j.company,
            {
                "id": j.company,
                "name": j.company_display,
                "job_count": 0,
                "last_updated": None,
                "top_score": None,
                "top_recommendation": None,
                "apply_count": 0,
                "consider_count": 0,
                "skip_count": 0,
                "scored_count": 0,
                "interested_count": 0,
                "applied_count": 0,
                "interview_count": 0,
                "offer_count": 0,
                "rejected_count": 0,
            },
        )
        slot["job_count"] += 1
        last = slot["last_updated"]
        if last is None or j.discovered_at > last:
            slot["last_updated"] = j.discovered_at

        scored = scores.get(j.id)
        if scored and not scored.error:
            slot["scored_count"] += 1
            if (
                slot["top_score"] is None
                or scored.overall_score > slot["top_score"]
            ):
                slot["top_score"] = scored.overall_score
                slot["top_recommendation"] = scored.recommendation
            if scored.recommendation == "apply":
                slot["apply_count"] += 1
            elif scored.recommendation == "consider":
                slot["consider_count"] += 1
            elif scored.recommendation == "skip":
                slot["skip_count"] += 1

        app = apps.get(j.id)
        if app:
            status = app.status
            if status == "interested":
                slot["interested_count"] += 1
            elif status == "applied":
                slot["applied_count"] += 1
            elif status == "interview":
                slot["interview_count"] += 1
                slot["applied_count"] += 1
            elif status == "offer":
                slot["offer_count"] += 1
                slot["applied_count"] += 1
            elif status == "rejected":
                slot["rejected_count"] += 1

    companies = [CompanyStats(**v) for v in by_company.values()]
    companies.sort(key=lambda c: (-(c.top_score or -1), -c.job_count))
    return companies


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


# ----------------------------------------------------------------------
# App factory
# ----------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    logger.info("api_startup", version=__version__, port=settings.dashboard_port)
    yield
    logger.info("api_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Job Autopilot",
        description="AI-powered job discovery, scoring, and tracking.",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/filters", tags=["meta"])
    async def get_filters() -> dict[str, Any]:
        return _load_filters_config()

    @app.get(
        "/api/companies",
        response_model=CompaniesResponse,
        tags=["companies"],
    )
    async def list_companies() -> CompaniesResponse:
        jobs = _load_jobs()
        scores = _load_scored_jobs()
        apps = load_applications(APPLICATIONS_FILE)
        companies = _aggregate_companies(jobs, scores, apps)
        return CompaniesResponse(
            total_jobs=len(jobs),
            company_count=len(companies),
            last_refreshed=_last_successful_run_time(),
            companies=companies,
        )

    @app.get(
        "/api/companies/{company_id}/jobs",
        response_model=CompanyJobsResponse,
        tags=["companies"],
    )
    async def jobs_for_company(company_id: str) -> CompanyJobsResponse:
        company_id = company_id.lower()
        all_jobs = _load_jobs()
        scores = _load_scored_jobs()
        apps = load_applications(APPLICATIONS_FILE)
        company_jobs = [j for j in all_jobs if j.company == company_id]

        if not company_jobs:
            raise HTTPException(
                status_code=404,
                detail=f"No jobs found for company '{company_id}'.",
            )

        company_name = company_jobs[0].company_display
        last_updated = max(j.discovered_at for j in company_jobs)

        items: list[JobItem] = []
        apply_count = consider_count = skip_count = unscored_count = 0
        score_sum = 0
        scored_count = 0
        top_score: int | None = None
        top_rec: str | None = None

        interested_count = 0
        applied_count = 0
        interview_count = 0
        offer_count = 0
        rejected_count = 0

        for j in company_jobs:
            s = scores.get(j.id)
            score_val = None
            rec = None
            summary_text = None
            strengths: list[str] = []
            gaps: list[str] = []
            dims_dict: dict[str, int] | None = None
            scored_at = None

            if s and not s.error:
                score_val = s.overall_score
                rec = s.recommendation
                summary_text = s.summary
                strengths = list(s.strengths)
                gaps = list(s.gaps)
                dims_dict = {
                    "skills_match": s.dimensions.skills_match,
                    "experience_level": s.dimensions.experience_level,
                    "domain_match": s.dimensions.domain_match,
                    "role_fit": s.dimensions.role_fit,
                }
                scored_at = s.scored_at
                score_sum += score_val
                scored_count += 1
                if top_score is None or score_val > top_score:
                    top_score = score_val
                    top_rec = rec
                if rec == "apply":
                    apply_count += 1
                elif rec == "consider":
                    consider_count += 1
                elif rec == "skip":
                    skip_count += 1
            else:
                unscored_count += 1

            app_record = apps.get(j.id)
            if app_record:
                if app_record.status == "interested":
                    interested_count += 1
                elif app_record.status == "applied":
                    applied_count += 1
                elif app_record.status == "interview":
                    interview_count += 1
                    applied_count += 1
                elif app_record.status == "offer":
                    offer_count += 1
                    applied_count += 1
                elif app_record.status == "rejected":
                    rejected_count += 1

            items.append(
                JobItem(
                    id=j.id,
                    title=j.title,
                    location=j.location,
                    posted_on=j.posted_on,
                    url=str(j.url),
                    employment_type=j.employment_type,
                    job_family=j.job_family,
                    discovered_at=j.discovered_at,
                    description_text=j.description_text,
                    score=score_val,
                    recommendation=rec,
                    score_summary=summary_text,
                    strengths=strengths,
                    gaps=gaps,
                    dimensions=dims_dict,
                    scored_at=scored_at,
                    application=app_record,
                )
            )

        def _sort_key(item: JobItem) -> tuple[int, int, str]:
            has_score = item.score is not None
            return (
                0 if has_score else 1,
                -(item.score or 0),
                item.discovered_at.isoformat(),
            )

        items.sort(key=_sort_key)

        avg_score = round(score_sum / scored_count, 1) if scored_count else None

        return CompanyJobsResponse(
            company=CompanyStats(
                id=company_id,
                name=company_name,
                job_count=len(company_jobs),
                last_updated=last_updated,
                top_score=top_score,
                top_recommendation=top_rec,
                apply_count=apply_count,
                consider_count=consider_count,
                skip_count=skip_count,
                scored_count=scored_count,
                interested_count=interested_count,
                applied_count=applied_count,
                interview_count=interview_count,
                offer_count=offer_count,
                rejected_count=rejected_count,
            ),
            jobs=items,
            avg_score=avg_score,
            apply_count=apply_count,
            consider_count=consider_count,
            skip_count=skip_count,
            unscored_count=unscored_count,
        )

    @app.post("/api/discover", response_model=DiscoverResponse, tags=["actions"])
    async def trigger_discover() -> DiscoverResponse:
        cfg = _load_sources_config()
        workday_orgs = cfg.enabled_workday()

        if not workday_orgs:
            return DiscoverResponse(
                ok=True,
                error="No enabled Workday orgs in sources.json.",
            )

        started = datetime.now(timezone.utc)
        source = WorkdaySource(orgs=workday_orgs)

        try:
            jobs = await source.discover()
        except Exception as exc:
            duration = (datetime.now(timezone.utc) - started).total_seconds()
            logger.error("api_discover_failed", error=str(exc))
            _record_run(
                sources=["workday"], discovered=0, added=0, updated=0,
                duration_sec=duration, ok=False, error=str(exc),
            )
            return DiscoverResponse(ok=False, duration_sec=duration, error=str(exc))

        filters_config = load_filters(settings.config_dir)
        kept_jobs, pending_jobs, report_state = apply_scrape_filter(
            jobs, filters_config
        )

        if pending_jobs:
            try:
                resolved = await source.resolve_pending_locations(
                    pending_jobs, concurrency=3
                )
                extra_kept = apply_location_filter_to_resolved(
                    resolved, report_state
                )
                kept_jobs = kept_jobs + extra_kept
            except Exception as exc:
                logger.warning("api_location_resolve_failed", error=str(exc))

        filter_stats = finalize_filter_report(report_state)

        if kept_jobs:
            try:
                kept_jobs = await source.enrich_details(kept_jobs)
            except Exception as exc:
                logger.warning("api_enrich_failed", error=str(exc))

        duration = (datetime.now(timezone.utc) - started).total_seconds()
        settings.data_dir.mkdir(parents=True, exist_ok=True)

        added, updated = await asyncio.to_thread(
            upsert_models_by_id,
            settings.raw_jobs_file,
            kept_jobs,
            RawJob,
        )

        _record_run(
            sources=["workday"], discovered=len(jobs),
            added=added, updated=updated,
            duration_sec=duration, ok=True,
            filter_report=filter_stats,
        )

        return DiscoverResponse(
            ok=True,
            discovered=len(jobs),
            kept=len(kept_jobs),
            added=added,
            updated=updated,
            duration_sec=round(duration, 2),
        )

    @app.post("/api/score", response_model=ScoreResponse, tags=["actions"])
    async def trigger_score(
        force: bool = False,
        concurrency: int = 5,
    ) -> ScoreResponse:
        from dotenv import load_dotenv
        load_dotenv()

        try:
            resume = load_resume(settings.resume_file)
        except Exception as exc:
            return ScoreResponse(ok=False, error=f"Resume error: {exc}")

        rubric_path = settings.config_dir / "rubric.json"
        if rubric_path.exists():
            rubric = RubricConfig.model_validate(
                json.loads(rubric_path.read_text(encoding="utf-8"))
            )
        else:
            rubric = RubricConfig.default()

        try:
            client = build_client()
        except Exception as exc:
            return ScoreResponse(ok=False, error=str(exc))

        try:
            summary = await score_all_jobs(
                raw_jobs_path=settings.raw_jobs_file,
                scored_jobs_path=SCORED_JOBS_FILE,
                resume=resume,
                rubric=rubric,
                client=client,
                concurrency=concurrency,
                force=force,
            )
        except Exception as exc:
            logger.error("api_score_failed", error=str(exc))
            return ScoreResponse(ok=False, error=str(exc))

        return ScoreResponse(
            ok=True,
            total=summary.total,
            scored=summary.scored,
            cached=summary.cached,
            failed=summary.failed,
            tokens_used=summary.total_tokens,
            est_cost_usd=round(summary.estimated_cost("gpt-4o-mini"), 4),
            duration_sec=summary.duration_sec,
        )

    @app.get(
        "/api/applications",
        tags=["applications"],
    )
    async def list_applications() -> dict[str, Application]:
        return load_applications(APPLICATIONS_FILE)

    @app.get(
        "/api/applications/stats",
        response_model=ApplicationStatsResponse,
        tags=["applications"],
    )
    async def application_stats() -> ApplicationStatsResponse:
        apps = load_applications(APPLICATIONS_FILE)
        stats = compute_status_stats(apps)
        return ApplicationStatsResponse(**stats)

    @app.put(
        "/api/applications/{job_id}",
        response_model=Application,
        tags=["applications"],
    )
    async def upsert_application(
        job_id: str,
        body: ApplicationUpdateRequest,
    ) -> Application:
        apps = load_applications(APPLICATIONS_FILE)
        existing = apps.get(job_id)

        if existing is None:
            app_record = Application.create(
                job_id=job_id,
                status=body.status,
                note=body.note,
            )
            if body.applied_at is not None:
                app_record.applied_at = body.applied_at
            if body.notes is not None:
                app_record.notes = body.notes
        else:
            app_record = existing
            if app_record.status != body.status:
                app_record.add_transition(
                    body.status,
                    note=body.note,
                    at=body.applied_at if body.applied_at else None,
                )
            if body.applied_at is not None:
                app_record.applied_at = body.applied_at
            if body.notes is not None:
                app_record.notes = body.notes
                app_record.updated_at = datetime.now(timezone.utc)

        await asyncio.to_thread(save_application, APPLICATIONS_FILE, app_record)
        return app_record

    @app.delete(
        "/api/applications/{job_id}",
        tags=["applications"],
    )
    async def remove_application(job_id: str) -> dict[str, Any]:
        removed = await asyncio.to_thread(
            delete_application, APPLICATIONS_FILE, job_id
        )
        if not removed:
            raise HTTPException(
                status_code=404,
                detail=f"No application tracked for {job_id}",
            )
        return {"ok": True, "job_id": job_id}

    @app.get("/", include_in_schema=False)
    async def index():
        index_html = FRONTEND_DIR / "index.html"
        if not index_html.exists():
            return {
                "message": "Frontend not built yet. Visit /docs for the API.",
                "version": __version__,
            }
        return FileResponse(index_html)

    if FRONTEND_DIR.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(FRONTEND_DIR)),
            name="static",
        )

    return app


app = create_app()