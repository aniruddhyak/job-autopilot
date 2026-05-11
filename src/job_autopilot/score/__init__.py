"""LLM scoring package: resume loader, scorer, rubric handling."""

from job_autopilot.score.llm import (
    LLMScoringError,
    build_client,
    compute_score_hash,
    score_one_job,
)
from job_autopilot.score.resume import (
    Resume,
    ResumeNotFoundError,
    estimate_tokens,
    load_resume,
)
from job_autopilot.score.scorer import ScoreSummary, score_all_jobs

__all__ = [
    "LLMScoringError",
    "Resume",
    "ResumeNotFoundError",
    "ScoreSummary",
    "build_client",
    "compute_score_hash",
    "estimate_tokens",
    "load_resume",
    "score_all_jobs",
    "score_one_job",
]