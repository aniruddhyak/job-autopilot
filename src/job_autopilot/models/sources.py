"""Pydantic models for source configuration (Workday, Greenhouse, etc.)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Priority = Literal["high", "medium", "low"]


# ----------------------------------------------------------------------
# Per-source organization configs
# ----------------------------------------------------------------------

class _OrgBase(BaseModel):
    """Fields shared across all source types."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company: str = Field(..., description="Slug used in the source's API URL.")
    display_name: str = Field(..., description="Human-readable company name.")
    enabled: bool = Field(True, description="Toggle without deleting the entry.")
    tags: list[str] = Field(default_factory=list, description="Free-form categorization.")
    priority: Priority = Field("medium", description="Scrape order + future scoring weight.")
    note: str = Field("", description="Free-text note for your own reference.")


class WorkdayOrg(_OrgBase):
    """A Workday tenant configured for discovery.

    URL pattern:
        https://{company}.wd{num}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs
    """

    site: str = Field(..., description="Workday careers site name, e.g. 'Cisco_Careers'.")
    num: int = Field(..., ge=1, le=99, description="Workday subdomain number (wd1..wd99).")


class GreenhouseOrg(_OrgBase):
    """A Greenhouse-hosted careers page.

    URL pattern:
        https://boards-api.greenhouse.io/v1/boards/{company}/jobs
    """


class LeverOrg(_OrgBase):
    """A Lever-hosted careers page.

    URL pattern:
        https://api.lever.co/v0/postings/{company}
    """


# ----------------------------------------------------------------------
# Top-level config (matches config/sources.json)
# ----------------------------------------------------------------------

class SourcesConfig(BaseModel):
    """The full sources.json document."""

    model_config = ConfigDict(extra="forbid")

    workday: list[WorkdayOrg] = Field(default_factory=list)
    greenhouse: list[GreenhouseOrg] = Field(default_factory=list)
    lever: list[LeverOrg] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def enabled_workday(self) -> list[WorkdayOrg]:
        """Return only the Workday orgs marked enabled."""
        return [o for o in self.workday if o.enabled]

    def enabled_greenhouse(self) -> list[GreenhouseOrg]:
        return [o for o in self.greenhouse if o.enabled]

    def enabled_lever(self) -> list[LeverOrg]:
        return [o for o in self.lever if o.enabled]