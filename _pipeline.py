"""One-time pipeline diagnostic. Delete after running."""
import asyncio
import json
from collections import Counter
from pathlib import Path

from job_autopilot.discover import WorkdaySource
from job_autopilot.filters import (
    apply_scrape_filter,
    load_filters,
    parse_posted_age_days,
)
from job_autopilot.models import SourcesConfig


def main():
    cfg = SourcesConfig.model_validate(
        json.loads(Path("config/sources.json").read_text(encoding="utf-8"))
    )
    src = WorkdaySource(orgs=cfg.enabled_workday())
    filters_config = load_filters(Path("config"))

    print("Scraping...")
    jobs = asyncio.run(src.discover())
    print(f"TOTAL scraped: {len(jobs)}")
    print()

    sf = filters_config["scrape_filter"]

    # Stage 1 — location only
    cfg_loc_only = {
        **filters_config,
        "scrape_filter": {
            **sf,
            "title_includes": [],
            "title_excludes": [],
            "max_age_days": None,
        },
    }
    kept1, _ = apply_scrape_filter(jobs, cfg_loc_only)
    print(f"After LOCATION filter only:    {len(kept1)}")

    # Stage 2 — location + title
    cfg_no_age = {
        **filters_config,
        "scrape_filter": {**sf, "max_age_days": None},
    }
    kept2, _ = apply_scrape_filter(jobs, cfg_no_age)
    print(f"After LOCATION + TITLE:        {len(kept2)}")

    # Stage 3 — full filter
    kept3, stats = apply_scrape_filter(jobs, filters_config)
    print(f"After ALL filters (final):     {len(kept3)}")
    print()
    print(f"Final stats: {stats}")
    print()

    # Age distribution of jobs that survived loc+title
    print("Age distribution of LOCATION+TITLE matches:")
    ages = Counter()
    for j in kept2:
        age = parse_posted_age_days(j.posted_on)
        key = "unknown" if age is None else f"{age} days"
        ages[key] += 1
    for k, v in sorted(ages.items()):
        print(f"  {k:15s} {v}")
    print()

    # Sample of what survived location only (before title filter)
    print("Sample of 10 jobs that passed LOCATION but maybe failed TITLE:")
    survived_loc_ids = {j.id for j in kept1}
    survived_title_ids = {j.id for j in kept2}
    title_dropped = [
        j for j in kept1 if j.id not in survived_title_ids
    ][:10]
    for j in title_dropped:
        print(f"  [{j.company_display}] {j.title}  ({j.location})")


if __name__ == "__main__":
    main()