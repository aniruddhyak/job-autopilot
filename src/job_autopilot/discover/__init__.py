"""Discovery sources — one subclass per ATS provider."""

from job_autopilot.discover.base import DiscoverySource, run_sources
from job_autopilot.discover.workday import WorkdaySource

__all__ = ["DiscoverySource", "WorkdaySource", "run_sources"]