"""Pydantic models for job postings (raw and scored)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


# ----------------------------------------------------------------------
# Type aliases
# ----------------------------------------------------------------------

SourceType = Literal["workday", "greenhouse", "lever"]
"""Discovery source type. Add new sources here as the project grows."""


# ----------------------------------------------------------------------
# RawJob — a job as scraped from a source, before any scoring
# ----------------------------------------------------------------------

class RawJob(BaseModel):
    """A single job posting discovered from a source.

    The `id` field is a deterministic, source-stable identifier of the form
    ``"{source}:{company}:{job_req_id}"`` — used for deduplication across runs.
    """

    model_config = ConfigDict(
        extra="forbid",          # reject unknown fields → catches typos early
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    # --- Identity ---
    id: str = Field(..., description="Unique ID: '{source}:{company}:{job_req_id}'")
    source: SourceType = Field(..., description="Which source discovered this job.")
    company: str = Field(..., description="Company slug (lowercase), e.g. 'cisco'.")
    company_display: str = Field(..., description="Human-readable name, e.g. 'Cisco'.")

    # --- Listing fields ---
    title: str = Field(..., description="Job title, e.g. 'Senior Software Engineer'.")
    location: str | None = Field(None, description="Free-text location, e.g. 'San Jose, CA'.")
    posted_on: str | None = Field(None, description="Source-provided posted date string.")
    job_req_id: str | None = Field(None, description="Source's internal requisition ID.")
    employment_type: str | None = Field(None, description="Full-time / Contract / etc.")
    job_family: str | None = Field(None, description="Source-provided job family/category.")

    # --- Detail fields (populated only when fetch_details=True) ---
    description: str | None = Field(None, description="Full job description (HTML or plain).")
    description_text: str | None = Field(None, description="Plain-text version of description.")
    qualifications: str | None = Field(None, description="Required qualifications.")
    responsibilities: str | None = Field(None, description="Job responsibilities.")
    remote_type: str | None = Field(None, description="Onsite / Hybrid / Remote.")

    # --- Linking + provenance ---
    url: HttpUrl = Field(..., description="Direct link to the job posting.")
    discovered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this job was first discovered (UTC).",
    )
    content_hash: str | None = Field(
        None,
        description="SHA-256 of title+description, used to detect content changes.",
    )

    # ------------------------------------------------------------------
    # Helper constructors
    # ------------------------------------------------------------------

    @staticmethod
    def make_id(source: str, company: str, job_req_id: str | None) -> str:
        """Build a stable job ID. Falls back to a hash if job_req_id is missing."""
        if job_req_id:
            return f"{source}:{company}:{job_req_id}"
        # Fallback (rare): hash some stable fields
        return f"{source}:{company}:nohid"

    @staticmethod
    def compute_content_hash(title: str, description: str | None) -> str:
        """Compute a SHA-256 over title + description for change detection."""
        payload = (title or "") + "\n" + (description or "")
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()