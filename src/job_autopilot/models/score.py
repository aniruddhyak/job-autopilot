"""Pydantic models for LLM-based job scoring."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Recommendation = Literal["apply", "consider", "skip"]


# ----------------------------------------------------------------------
# Sub-scores (rubric dimensions)
# ----------------------------------------------------------------------

class ScoreDimensions(BaseModel):
    """Per-dimension sub-scores (0-100). The LLM produces these directly."""

    model_config = ConfigDict(extra="forbid")

    skills_match: int = Field(
        ...,
        ge=0,
        le=100,
        description="Overlap between candidate's tech stack and JD requirements.",
    )
    experience_level: int = Field(
        ...,
        ge=0,
        le=100,
        description="Seniority fit (e.g., Staff vs Senior vs Junior).",
    )
    domain_match: int = Field(
        ...,
        ge=0,
        le=100,
        description="Industry / product area familiarity.",
    )
    role_fit: int = Field(
        ...,
        ge=0,
        le=100,
        description="Hands-on coding vs management vs hybrid.",
    )


# ----------------------------------------------------------------------
# Full scored job
# ----------------------------------------------------------------------

class ScoredJob(BaseModel):
    """A job that has been scored by the LLM against the candidate's resume."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    # --- Identity (matches RawJob.id for joining) ---
    id: str = Field(..., description="Same ID as the corresponding RawJob.")

    # --- LLM output ---
    overall_score: int = Field(
        ...,
        ge=0,
        le=100,
        description="Aggregated weighted score (0-100).",
    )
    dimensions: ScoreDimensions
    summary: str = Field(
        ...,
        max_length=400,
        description="1-2 sentence fit summary.",
    )
    strengths: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Up to 5 specific strengths matching the JD.",
    )
    gaps: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Up to 5 specific gaps where candidate is light vs JD.",
    )
    recommendation: Recommendation = Field(
        ...,
        description="apply | consider | skip — actionable verdict.",
    )

    # --- Provenance ---
    model: str = Field(..., description="LLM model used for scoring.")
    scored_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this score was produced (UTC).",
    )
    content_hash: str | None = Field(
        None,
        description="Hash of (resume + JD) used; re-score if hash differs.",
    )
    tokens_used: int | None = Field(
        None,
        ge=0,
        description="Total tokens consumed by the API call.",
    )

    # Optional: track scoring errors instead of dropping the job
    error: str | None = Field(
        None,
        description="If scoring failed, brief error message instead of scores.",
    )


# ----------------------------------------------------------------------
# Rubric config (loaded from config/rubric.json — optional)
# ----------------------------------------------------------------------

class RubricConfig(BaseModel):
    """Weights for combining sub-scores into an overall score."""

    model_config = ConfigDict(extra="ignore")

    skills_weight: float = Field(0.35, ge=0.0, le=1.0)
    experience_weight: float = Field(0.25, ge=0.0, le=1.0)
    domain_weight: float = Field(0.20, ge=0.0, le=1.0)
    role_fit_weight: float = Field(0.20, ge=0.0, le=1.0)

    # Free-form text that becomes part of the scoring prompt to nudge the LLM.
    # E.g., "Heavily weight AI/ML and cloud experience" or
    # "Prefer hands-on engineering over management roles."
    focus_areas: str = Field(
        default="",
        description="Optional natural-language emphasis appended to the prompt.",
    )

    def compute_overall(self, dims: ScoreDimensions) -> int:
        """Apply the weights to compute the overall score."""
        total = (
            dims.skills_match * self.skills_weight
            + dims.experience_level * self.experience_weight
            + dims.domain_match * self.domain_weight
            + dims.role_fit * self.role_fit_weight
        )
        # Normalize in case weights don't sum to 1
        weight_sum = (
            self.skills_weight
            + self.experience_weight
            + self.domain_weight
            + self.role_fit_weight
        )
        if weight_sum > 0:
            total = total / weight_sum
        return max(0, min(100, round(total)))

    @classmethod
    def default(cls) -> "RubricConfig":
        return cls()