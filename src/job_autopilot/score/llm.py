"""OpenAI client wrapper for structured job scoring."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Any

import structlog
from openai import APIError, AsyncOpenAI, RateLimitError
from pydantic import ValidationError

from job_autopilot.models import RawJob, RubricConfig, ScoreDimensions, ScoredJob
from job_autopilot.score.prompts import SYSTEM_PROMPT, build_scoring_prompt
from job_autopilot.score.resume import Resume

logger = structlog.get_logger(__name__)


# ----------------------------------------------------------------------
# Pricing (USD per 1M tokens) — gpt-4o-mini as of 2026
# ----------------------------------------------------------------------

_PRICING: dict[str, tuple[float, float]] = {
    # model: (input $/1M, output $/1M)
    "gpt-4o-mini":      (0.15, 0.60),
    "gpt-4o":           (2.50, 10.00),
    "gpt-4.1-mini":     (0.40, 1.60),
}

_DEFAULT_MODEL = "gpt-4o-mini"


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_price, out_price = _PRICING.get(model, _PRICING[_DEFAULT_MODEL])
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


# ----------------------------------------------------------------------
# Structured-output schema (subset of ScoredJob that the LLM produces)
# ----------------------------------------------------------------------

# What we ask the LLM to return. The remaining ScoredJob fields (id, model,
# scored_at, tokens_used, content_hash) are filled by us, not the LLM.
_LLM_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "dimensions": {
            "type": "object",
            "properties": {
                "skills_match":     {"type": "integer", "minimum": 0, "maximum": 100},
                "experience_level": {"type": "integer", "minimum": 0, "maximum": 100},
                "domain_match":     {"type": "integer", "minimum": 0, "maximum": 100},
                "role_fit":         {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["skills_match", "experience_level", "domain_match", "role_fit"],
            "additionalProperties": False,
        },
        "summary":        {"type": "string", "maxLength": 400},
        "strengths":      {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "gaps":           {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "recommendation": {"type": "string", "enum": ["apply", "consider", "skip"]},
    },
    "required": ["dimensions", "summary", "strengths", "gaps", "recommendation"],
    "additionalProperties": False,
}


# ----------------------------------------------------------------------
# Hash helper — used to invalidate cached scores when resume or JD changes
# ----------------------------------------------------------------------

def compute_score_hash(resume_sha256: str, job_id: str, jd_text: str) -> str:
    """Hash of (resume + job + JD) — re-score when this changes."""
    h = hashlib.sha256()
    h.update(resume_sha256.encode("utf-8"))
    h.update(b"\n--\n")
    h.update(job_id.encode("utf-8"))
    h.update(b"\n--\n")
    h.update((jd_text or "").encode("utf-8"))
    return h.hexdigest()


# ----------------------------------------------------------------------
# Main scoring function
# ----------------------------------------------------------------------

class LLMScoringError(Exception):
    """Raised when scoring fails after all retries."""


async def score_one_job(
    *,
    job: RawJob,
    resume: Resume,
    rubric: RubricConfig,
    client: AsyncOpenAI,
    model: str = _DEFAULT_MODEL,
    max_retries: int = 3,
) -> ScoredJob:
    """Score one job using the LLM. Returns a fully validated ScoredJob.

    Raises LLMScoringError if the LLM cannot produce a valid response.
    """
    jd_text = job.description_text or job.description or ""
    if not jd_text.strip():
        raise LLMScoringError(f"Job {job.id} has no description to score.")

    user_prompt = build_scoring_prompt(
        resume_text=resume.text,
        job_title=job.title,
        job_company=job.company_display,
        job_location=job.location,
        job_description=jd_text,
        focus_areas=rubric.focus_areas,
    )

    log = logger.bind(job_id=job.id, title=job.title, company=job.company_display)

    backoff = 1.0
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "job_score",
                        "schema": _LLM_OUTPUT_SCHEMA,
                        "strict": True,
                    },
                },
                temperature=0.2,
                max_tokens=600,
            )

            raw_json = resp.choices[0].message.content or ""
            try:
                payload = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                raise LLMScoringError(
                    f"LLM returned non-JSON for {job.id}: {raw_json[:200]}"
                ) from exc

            # Validate sub-scores via Pydantic
            try:
                dims = ScoreDimensions.model_validate(payload["dimensions"])
            except ValidationError as exc:
                raise LLMScoringError(
                    f"LLM dimensions failed validation for {job.id}: {exc}"
                ) from exc

            overall = rubric.compute_overall(dims)

            content_hash = compute_score_hash(
                resume.sha256, job.id, jd_text
            )

            usage = resp.usage
            tokens_used = (usage.total_tokens if usage else None)

            log.info(
                "scored",
                score=overall,
                in_tokens=usage.prompt_tokens if usage else None,
                out_tokens=usage.completion_tokens if usage else None,
                cost=round(
                    _compute_cost(
                        model,
                        usage.prompt_tokens if usage else 0,
                        usage.completion_tokens if usage else 0,
                    ),
                    5,
                ),
            )

            return ScoredJob(
                id=job.id,
                overall_score=overall,
                dimensions=dims,
                summary=payload["summary"],
                strengths=payload.get("strengths", []),
                gaps=payload.get("gaps", []),
                recommendation=payload["recommendation"],
                model=model,
                content_hash=content_hash,
                tokens_used=tokens_used,
            )

        except RateLimitError as exc:
            last_error = exc
            log.warning("rate_limited", attempt=attempt, sleep=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 16.0)
        except APIError as exc:
            last_error = exc
            log.warning(
                "api_error",
                attempt=attempt,
                error=str(exc),
                sleep=backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 16.0)
        except LLMScoringError:
            raise
        except Exception as exc:  # pragma: no cover — defensive
            last_error = exc
            log.warning(
                "unexpected_error",
                attempt=attempt,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 16.0)

    raise LLMScoringError(
        f"Failed to score {job.id} after {max_retries} attempts. "
        f"Last error: {last_error}"
    )


# ----------------------------------------------------------------------
# Convenience: build a configured client from env
# ----------------------------------------------------------------------

def build_client() -> AsyncOpenAI:
    """Build an AsyncOpenAI client from OPENAI_API_KEY in env."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise LLMScoringError(
            "OPENAI_API_KEY not set. Add it to .env or your shell environment."
        )
    return AsyncOpenAI(api_key=key)