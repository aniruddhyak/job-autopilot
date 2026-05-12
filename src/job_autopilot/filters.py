"""Filter logic for job postings with detailed audit stats.

Supports a two-pass mode: jobs with placeholder locations (e.g. '2 Locations')
are returned as 'pending' from the first pass so the caller can resolve their
real cities via the detail endpoint and re-apply the location filter.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from job_autopilot.models import RawJob

RawJobList = list[RawJob]


def load_filters(config_dir: Path) -> dict[str, Any]:
    path = config_dir / "filters.json"
    if not path.exists():
        return {"scrape_filter": {"enabled": False}, "locations": []}
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# Placeholder detection (Workday quirk)
# ----------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"^\s*\d+\s+locations?\s*$", re.IGNORECASE)


def is_placeholder_location(loc: str | None) -> bool:
    """True if `loc` looks like a Workday placeholder ('2 Locations' etc.)."""
    if not loc:
        return False
    return bool(_PLACEHOLDER_RE.match(loc))


# ----------------------------------------------------------------------
# Location grouping (for stats)
# ----------------------------------------------------------------------

_US_TERMS = {
    "us", "u.s.", "u.s", "usa",
    "united states", "united states of america",
}


def _normalize_location_group(loc: str | None) -> str:
    if not loc:
        return "(empty)"
    if is_placeholder_location(loc):
        return "(unresolved multi-city)"
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if not parts:
        return "(empty)"
    last = parts[-1].lower()
    if last in _US_TERMS:
        if len(parts) >= 2:
            return f"{parts[-2]}, US"
        return "US"
    return parts[-1]


# ----------------------------------------------------------------------
# Location matching
# ----------------------------------------------------------------------

def _location_matchers(
    filters_config: dict[str, Any], location_ids: list[str]
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
# Title rules
# ----------------------------------------------------------------------

def _check_title(
    job: RawJob, includes: list[str], excludes: list[str]
) -> tuple[str, str | None]:
    title = (job.title or "").lower()
    if excludes:
        matches = [k for k in excludes if k.lower() in title]
        if matches:
            longest = max(matches, key=len)
            return ("excluded", longest.lower())
    if includes and not any(k.lower() in title for k in includes):
        return ("no_include", None)
    return ("kept", None)


# ----------------------------------------------------------------------
# Age parsing
# ----------------------------------------------------------------------

_POSTED_NUMBER_RE = re.compile(r"(\d+)\s*\+?\s*days?\s*ago", re.IGNORECASE)


def parse_posted_age_days(posted_on: str | None) -> int | None:
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


def _age_bucket(age_days: int | None) -> str:
    if age_days is None:
        return "unknown"
    if age_days <= 2:
        return "0-2 days"
    if age_days <= 7:
        return "3-7 days"
    if age_days <= 14:
        return "8-14 days"
    if age_days <= 30:
        return "15-30 days"
    return "30+ days"


def _job_passes_age(
    job: RawJob, max_age_days: int | None
) -> tuple[bool, int | None]:
    if max_age_days is None:
        return True, None
    age = parse_posted_age_days(job.posted_on)
    if age is None:
        return True, age
    return age <= max_age_days, age


# ----------------------------------------------------------------------
# Stats scaffolding
# ----------------------------------------------------------------------

def _new_stats() -> dict[str, Any]:
    return {
        "input": 0,
        "kept": 0,
        "dropped_loc": 0,
        "dropped_title": 0,
        "dropped_age": 0,
        "pending_loc": 0,
        "recovered_after_resolve": 0,
        "filters_off": 0,
        "dropped_by_location": Counter(),
        "dropped_by_title_keyword": Counter(),
        "dropped_by_age_bucket": Counter(),
    }


def _finalize(stats: dict[str, Any]) -> dict[str, Any]:
    out = dict(stats)
    for k in ("dropped_by_location", "dropped_by_title_keyword",
              "dropped_by_age_bucket"):
        if isinstance(out.get(k), Counter):
            out[k] = dict(out[k].most_common())
    return out


# ----------------------------------------------------------------------
# Pass 1: title + age + (location OR pending)
# ----------------------------------------------------------------------

def apply_scrape_filter(
    jobs: RawJobList,
    filters_config: dict[str, Any],
    *,
    override_location_ids: list[str] | None = None,
    override_title_includes: list[str] | None = None,
    override_title_excludes: list[str] | None = None,
    override_max_age_days: int | None = None,
) -> tuple[RawJobList, RawJobList, dict[str, Any]]:
    """First-pass filter.

    Returns (kept_jobs, pending_jobs, report_state).

    pending_jobs are those that passed title+age but had a placeholder
    location like '2 Locations'. Caller should resolve their real locations
    via the detail endpoint and pass them to
    ``apply_location_filter_to_resolved``.

    After both passes, call ``finalize_filter_report(report_state)`` to get
    the JSON-serialisable report (suitable for runs.json).
    """
    sf = filters_config.get("scrape_filter") or {}
    enabled = sf.get("enabled", False)

    overall = _new_stats()
    overall["input"] = len(jobs)
    per_company: dict[str, dict[str, Any]] = {}
    for j in jobs:
        per_company.setdefault(j.company, _new_stats())
        per_company[j.company]["input"] += 1

    has_overrides = any(
        x is not None
        for x in (
            override_location_ids,
            override_title_includes,
            override_title_excludes,
            override_max_age_days,
        )
    )

    report_state: dict[str, Any] = {
        "_overall": overall,
        "_per_company": per_company,
        "_filters_config": filters_config,
        "_overrides": {"location_ids": override_location_ids},
    }

    if not enabled and not has_overrides:
        overall["kept"] = len(jobs)
        overall["filters_off"] = 1
        for c, s in per_company.items():
            s["kept"] = s["input"]
            s["filters_off"] = 1
        return jobs, [], report_state

    location_ids = (
        override_location_ids if override_location_ids is not None
        else sf.get("location_ids") or []
    )
    title_includes = (
        override_title_includes if override_title_includes is not None
        else sf.get("title_includes") or []
    )
    title_excludes = (
        override_title_excludes if override_title_excludes is not None
        else sf.get("title_excludes") or []
    )
    max_age_days = (
        override_max_age_days if override_max_age_days is not None
        else sf.get("max_age_days")
    )

    matchers = _location_matchers(filters_config, location_ids)
    kept: RawJobList = []
    pending: RawJobList = []

    for j in jobs:
        co_stats = per_company[j.company]

        # 1. Title
        action, kw = _check_title(j, title_includes, title_excludes)
        if action != "kept":
            key = kw or "(no include match)"
            overall["dropped_title"] += 1
            overall["dropped_by_title_keyword"][key] += 1
            co_stats["dropped_title"] += 1
            co_stats["dropped_by_title_keyword"][key] += 1
            continue

        # 2. Age
        age_ok, age = _job_passes_age(j, max_age_days)
        if not age_ok:
            bucket = _age_bucket(age)
            overall["dropped_age"] += 1
            overall["dropped_by_age_bucket"][bucket] += 1
            co_stats["dropped_age"] += 1
            co_stats["dropped_by_age_bucket"][bucket] += 1
            continue

        # 3. Location with placeholder awareness
        if matchers and is_placeholder_location(j.location):
            overall["pending_loc"] += 1
            co_stats["pending_loc"] += 1
            pending.append(j)
            continue

        if not _job_matches_locations(j, matchers):
            grp = _normalize_location_group(j.location)
            overall["dropped_loc"] += 1
            overall["dropped_by_location"][grp] += 1
            co_stats["dropped_loc"] += 1
            co_stats["dropped_by_location"][grp] += 1
            continue

        kept.append(j)
        co_stats["kept"] += 1

    overall["kept"] = len(kept)
    return kept, pending, report_state


# ----------------------------------------------------------------------
# Pass 2: re-check location after resolve
# ----------------------------------------------------------------------

def apply_location_filter_to_resolved(
    resolved_jobs: RawJobList,
    report_state: dict[str, Any],
) -> RawJobList:
    """Re-apply only the location filter to jobs whose real locations were
    just fetched. Updates the report state in place. Returns kept jobs.
    """
    overall = report_state["_overall"]
    per_company = report_state["_per_company"]
    filters_config = report_state["_filters_config"]
    override_loc = report_state["_overrides"]["location_ids"]

    sf = filters_config.get("scrape_filter") or {}
    location_ids = (
        override_loc if override_loc is not None
        else sf.get("location_ids") or []
    )
    matchers = _location_matchers(filters_config, location_ids)

    if not matchers:
        # No location filter active -> keep everything
        for j in resolved_jobs:
            overall["kept"] += 1
            overall["recovered_after_resolve"] += 1
            per_company[j.company]["kept"] += 1
            per_company[j.company]["recovered_after_resolve"] += 1
        return list(resolved_jobs)

    kept: RawJobList = []
    for j in resolved_jobs:
        co_stats = per_company.get(j.company)
        if not co_stats:
            continue
        if _job_matches_locations(j, matchers):
            kept.append(j)
            overall["kept"] += 1
            overall["recovered_after_resolve"] += 1
            co_stats["kept"] += 1
            co_stats["recovered_after_resolve"] += 1
        else:
            grp = _normalize_location_group(j.location)
            overall["dropped_loc"] += 1
            overall["dropped_by_location"][grp] += 1
            co_stats["dropped_loc"] += 1
            co_stats["dropped_by_location"][grp] += 1
    return kept


def finalize_filter_report(report_state: dict[str, Any]) -> dict[str, Any]:
    """Convert the internal report state to a JSON-serialisable dict."""
    overall = report_state["_overall"]
    per_company = report_state["_per_company"]
    return {
        **_finalize(overall),
        "per_company": {c: _finalize(s) for c, s in per_company.items()},
    }