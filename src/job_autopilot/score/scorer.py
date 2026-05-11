"""Scorer orchestration: load jobs + resume + rubric, score in parallel, persist."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import structlog
from openai import AsyncOpenAI

from job_autopilot.models import RawJob, RubricConfig, ScoredJob
from job_autopilot.score.llm import (
    LLMScoringError,
    compute_score_hash,
    score_one_job,
)
from job_autopilot.score.resume import Resume

logger = structlog.get_logger(__name__)


# Pricing (USD per 1M tokens). Mirrors llm.py — duplicated to avoid import cycle.
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini":  (0.15, 0.60),
    "gpt-4o":       (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
}


class ScoreSummary:
    """Summary of a scoring run, returned to the caller (CLI / API)."""

    def __init__(self) -> None:
        self.total = 0
        self.scored = 0
        self.cached = 0
        self.failed = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.duration_sec = 0.0
        self.errors: list[str] = []

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def estimated_cost(self, model: str) -> float:
        in_price, out_price = _PRICING.get(model, _PRICING["gpt-4o-mini"])
        return (
            (self.input_tokens * in_price + self.output_tokens * out_price)
            / 1_000_000
        )


def _load_raw_jobs(path: Path) -> list[RawJob]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [RawJob.model_validate(j) for j in raw]


def _load_existing_scores(path: Path) -> dict[str, ScoredJob]:
    """Load scored_jobs.json as a dict keyed by job id."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out: dict[str, ScoredJob] = {}
    for item in raw:
        try:
            sj = ScoredJob.model_validate(item)
            out[sj.id] = sj
        except Exception:
            continue
    return out


def _write_scores(path: Path, scores: dict[str, ScoredJob]) -> None:
    payload = [s.model_dump(mode="json") for s in scores.values()]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    tmp.replace(path)


def _is_score_current(
    existing: ScoredJob | None,
    job: RawJob,
    resume: Resume,
    model: str,
) -> bool:
    """Check if an existing score is still valid for this job + resume + model."""
    if existing is None or existing.error:
        return False
    if existing.model != model:
        return False
    jd_text = job.description_text or job.description or ""
    expected_hash = compute_score_hash(resume.sha256, job.id, jd_text)
    return existing.content_hash == expected_hash


# ----------------------------------------------------------------------
# Main orchestrator
# ----------------------------------------------------------------------

async def score_all_jobs(
    *,
    raw_jobs_path: Path,
    scored_jobs_path: Path,
    resume: Resume,
    rubric: RubricConfig,
    client: AsyncOpenAI,
    model: str = "gpt-4o-mini",
    concurrency: int = 5,
    force: bool = False,
    limit: int | None = None,
) -> ScoreSummary:
    """Score all raw jobs in parallel and persist results.

    Args:
        raw_jobs_path: Path to data/raw_jobs.json.
        scored_jobs_path: Path to data/scored_jobs.json (will be created/updated).
        resume: Loaded Resume.
        rubric: RubricConfig for weighting + focus_areas.
        client: AsyncOpenAI instance.
        model: Model name (gpt-4o-mini default).
        concurrency: Max parallel scoring calls (default 5).
        force: If True, ignore cache and re-score every job.
        limit: If set, only score the first N jobs (useful for testing).

    Returns:
        ScoreSummary with counts, tokens used, duration, and errors.
    """
    summary = ScoreSummary()
    started = time.monotonic()

    # 1. Load inputs
    raw_jobs = _load_raw_jobs(raw_jobs_path)
    raw_jobs = [j for j in raw_jobs if (j.description_text or j.description)]
    if limit:
        raw_jobs = raw_jobs[:limit]
    summary.total = len(raw_jobs)

    if not raw_jobs:
        logger.warning("score_no_jobs_to_score", path=str(raw_jobs_path))
        summary.duration_sec = round(time.monotonic() - started, 2)
        return summary

    existing_scores = {} if force else _load_existing_scores(scored_jobs_path)

    # 2. Partition into cached vs to-score
    to_score: list[RawJob] = []
    cached_scores: dict[str, ScoredJob] = {}
    for job in raw_jobs:
        existing = existing_scores.get(job.id)
        if _is_score_current(existing, job, resume, model):
            cached_scores[job.id] = existing  # reuse
            summary.cached += 1
        else:
            to_score.append(job)

    logger.info(
        "score_plan",
        total=summary.total,
        cached=summary.cached,
        to_score=len(to_score),
        concurrency=concurrency,
        force=force,
    )

    # 3. Score the uncached ones in parallel
    sem = asyncio.Semaphore(concurrency)

    async def _score_one(job: RawJob) -> tuple[str, ScoredJob | None, str | None]:
        async with sem:
            try:
                scored = await score_one_job(
                    job=job, resume=resume, rubric=rubric,
                    client=client, model=model,
                )
                return job.id, scored, None
            except LLMScoringError as exc:
                return job.id, None, str(exc)
            except Exception as exc:  # pragma: no cover
                logger.error(
                    "score_unexpected",
                    job_id=job.id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return job.id, None, str(exc)

    if to_score:
        results = await asyncio.gather(*(_score_one(j) for j in to_score))
        for job_id, scored, err in results:
            if scored:
                cached_scores[job_id] = scored  # add freshly scored to merged
                summary.scored += 1
                if scored.tokens_used:
                    # crude split: we don't have separate in/out from ScoredJob;
                    # approximate 75/25 since outputs are constrained-small
                    summary.input_tokens += int(scored.tokens_used * 0.75)
                    summary.output_tokens += int(scored.tokens_used * 0.25)
            else:
                summary.failed += 1
                if err:
                    summary.errors.append(f"{job_id}: {err}")

    # 4. Persist
    _write_scores(scored_jobs_path, cached_scores)

    summary.duration_sec = round(time.monotonic() - started, 2)

    logger.info(
        "score_complete",
        scored=summary.scored,
        cached=summary.cached,
        failed=summary.failed,
        duration=summary.duration_sec,
        cost_usd=round(summary.estimated_cost(model), 4),
    )

    return summary