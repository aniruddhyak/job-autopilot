"""Filter logic for job postings.

Reads ``config/filters.json`` and applies inclusion / exclusion rules to a
list of ``RawJob`` objects. Used at scrape time (CLI / API discover) to
keep ``raw_jobs.json`` focused on jobs you actually care about.

Filtering happens AFTER fetch, BEFORE storage.

Supported filters:
    - location_ids:    keep only jobs in these named locations
    - title_includes:  require the title to contain any of these (case-insensitive)
    - title_excludes:  drop the title if it contains any of these (case-insensitive)
    - max_age_days:    drop jobs posted more than N days ago
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from job_autopilot.models import RawJob

# Type aliases (avoid markdown-rendering issues in chat with `list[X]:` patterns)
RawJobList = list[RawJob]
StatsDict = dict[str, int]


def load_filters(config_dir: Path) -> dict[str, Any]:
    """Load filters.json. Returns a permissive default if missing."""
    path = config_dir / "filters.json"
    if not path.exists():
        return {"scrape_filter": {"enabled": False}, "locations": []}
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# Location matching
# ----------------------------------------------------------------------

def _location_matchers(
    filters_config: dict[str, Any],
    location_ids: list[str],
) -> list[list[str]]:
    out: list[list[str]] = []
    by_id = {loc["id"]: loc for loc in filters_config.get("locations", [])}
    for lid in location_ids:
        loc = by_id.get(lid)
        if loc and isinstance(loc.get("matchAny"), list):
            out.append([s.lower() for s in loc["matchAny"]])
    return out


def _job_matches_locations(job: RawJob, matchers: list[list[str]]) -> bool:
    if not matchers:
        return True
    haystack = (job.location or "").lower()
    for needles in matchers:
        if any(n in haystack for n in needles):
            return True
    return False


# ----------------------------------------------------------------------
# Title matching
# ----------------------------------------------------------------------

def _job_passes_title_rules(
    job: RawJob,
    includes: list[str],
    excludes: list[str],
) -> bool:
    title = (job.title or "").lower()
    if includes and not any(k.lower() in title for k in includes):
        return False
    if excludes and any(k.lower() in title for k in excludes):
        return False
    return True


# ----------------------------------------------------------------------
# Posted-date parsing (Workday's human-readable strings)
# ----------------------------------------------------------------------

# Captures things like:
#   "Posted Today"           -> 0
#   "Posted Yesterday"       -> 1
#   "Posted 5 Days Ago"      -> 5
#   "Posted 30+ Days Ago"    -> 30
_POSTED_NUMBER_RE = re.compile(r"(\d+)\s*\+?\s*days?\s*ago", re.IGNORECASE)


def parse_posted_age_days(posted_on: str | None) -> int | None:
    """Convert Workday's posted_on string into number of days ago.

    Returns:
        0 for "Today", 1 for "Yesterday", N for "Posted N Days Ago".
        None if the value is missing or unparseable.
    """
    if not posted_on:
        return None
    s = posted_on.strip().lower()
    if not s:
        return None

    if "today" in s and "yesterday" not in s:
        return 0
    if "yesterday" in s:
        return 1

    m = _POSTED_NUMBER_RE.search(s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None

    return None


def _job_passes_age_filter(job: RawJob, max_age_days: int | None) -> bool:
    """A job passes if its posted age is within max_age_days.

    Behaviour for unparseable / missing dates: treated as "unknown" -> KEPT.
    Better to over-include than silently drop.
    """
    if max_age_days is None:
        return True
    age = parse_posted_age_days(job.posted_on)
    if age is None:
        return True
    return age <= max_age_days


# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------

def apply_scrape_filter(
    jobs: RawJobList,
    filters_config: dict[str, Any],
    *,
    override_location_ids: list[str] | None = None,
    override_title_includes: list[str] | None = None,
    override_title_excludes: list[str] | None = None,
    override_max_age_days: int | None = None,
) -> tuple[RawJobList, StatsDict]:
    """Filter jobs based on the scrape_filter config.

    Returns a tuple (kept_jobs, stats) where stats keys are:
        input, kept, dropped_loc, dropped_title, dropped_age, filters_off
    """
    sf = filters_config.get("scrape_filter") or {}
    enabled = sf.get("enabled", False)

    stats: StatsDict = {
        "input": len(jobs),
        "kept": 0,
        "dropped_loc": 0,
        "dropped_title": 0,
        "dropped_age": 0,
        "filters_off": 0,
    }

    has_overrides = any(
        x is not None
        for x in (
            override_location_ids,
            override_title_includes,
            override_title_excludes,
            override_max_age_days,
        )
    )
    if not enabled and not has_overrides:
        stats["kept"] = len(jobs)
        stats["filters_off"] = 1
        return jobs, stats

    location_ids = (
        override_location_ids
        if override_location_ids is not None
        else sf.get("location_ids") or []
    )
    title_includes = (
        override_title_includes
        if override_title_includes is not None
        else sf.get("title_includes") or []
    )
    title_excludes = (
        override_title_excludes
        if override_title_excludes is not None
        else sf.get("title_excludes") or []
    )
    max_age_days = (
        override_max_age_days
        if override_max_age_days is not None
        else sf.get("max_age_days")
    )

    matchers = _location_matchers(filters_config, location_ids)
    kept: RawJobList = []

    for j in jobs:
        if not _job_matches_locations(j, matchers):
            stats["dropped_loc"] += 1
            continue
        if not _job_passes_title_rules(j, title_includes, title_excludes):
            stats["dropped_title"] += 1
            continue
        if not _job_passes_age_filter(j, max_age_days):
            stats["dropped_age"] += 1
            continue
        kept.append(j)

    stats["kept"] = len(kept)
    return kept, stats