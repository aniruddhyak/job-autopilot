"""Pydantic models for application tracking."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ApplicationStatus = Literal[
    "interested",
    "applied",
    "interview",
    "offer",
    "rejected",
]
"""Status values for a tracked application."""

VALID_STATUSES: tuple[ApplicationStatus, ...] = (
    "interested", "applied", "interview", "offer", "rejected",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------------
# History entry
# ----------------------------------------------------------------------

class StatusHistoryEntry(BaseModel):
    """One row in the per-application status timeline."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: ApplicationStatus
    at: datetime = Field(default_factory=_utc_now)
    note: str = Field("", max_length=2000, description="Optional note for this transition.")


# ----------------------------------------------------------------------
# Application
# ----------------------------------------------------------------------

class Application(BaseModel):
    """A tracked application for one job posting.

    Keyed by ``job_id`` (matches RawJob.id). Stored as a list of these
    in data/applications.json.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    job_id: str = Field(..., description="Same ID as the corresponding RawJob.")
    status: ApplicationStatus = Field(
        ..., description="Current status (latest entry in history).",
    )
    applied_at: datetime | None = Field(
        None,
        description="When the user first set status to 'applied' (set once).",
    )
    notes: str = Field(
        "",
        max_length=4000,
        description="Free-text running notes for this application.",
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        description="Last time anything (status or notes) changed.",
    )
    status_history: list[StatusHistoryEntry] = Field(
        default_factory=list,
        description="Chronological list of status transitions.",
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def add_transition(
        self,
        new_status: ApplicationStatus,
        *,
        note: str = "",
        at: datetime | None = None,
    ) -> None:
        """Record a new transition. Updates current status, history, and
        applied_at if relevant. ``updated_at`` is bumped to ``at`` (or now).
        """
        when = at or _utc_now()
        entry = StatusHistoryEntry(status=new_status, at=when, note=note)
        self.status_history.append(entry)
        self.status = new_status
        self.updated_at = when
        if new_status == "applied" and self.applied_at is None:
            self.applied_at = when

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        status: ApplicationStatus,
        note: str = "",
    ) -> "Application":
        """Build a brand-new Application with one initial transition."""
        now = _utc_now()
        entry = StatusHistoryEntry(status=status, at=now, note=note)
        return cls(
            job_id=job_id,
            status=status,
            applied_at=now if status == "applied" else None,
            notes="",
            updated_at=now,
            status_history=[entry],
        )