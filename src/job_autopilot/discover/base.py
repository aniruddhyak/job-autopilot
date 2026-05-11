"""Abstract base class for job discovery sources.

Every concrete source (Workday, Greenhouse, Lever, ...) inherits from
``DiscoverySource`` and implements ``discover()`` for a single org.

Why an abstract base class?
    - Forces every source to expose the same shape: ``async def discover() -> list[RawJob]``.
    - The pipeline (CLI, scheduler) can iterate over a list of sources without
      knowing which concrete type each one is — true polymorphism.
    - Adding a new ATS = subclass + 1 method. No edits to the rest of the app.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Iterable

import httpx
import structlog

from job_autopilot.models import RawJob

logger = structlog.get_logger(__name__)


# ----------------------------------------------------------------------
# Defaults shared across HTTP-based sources
# ----------------------------------------------------------------------

DEFAULT_TIMEOUT = 15.0

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": DEFAULT_USER_AGENT,
}


# ----------------------------------------------------------------------
# Abstract base class
# ----------------------------------------------------------------------

class DiscoverySource(ABC):
    """Abstract base for all discovery sources."""

    source_name: str = "base"

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_jobs_per_org: int | None = None,
        fetch_details: bool = False,
        polite_delay: float = 0.0,
    ) -> None:
        self.timeout = timeout
        self.max_jobs_per_org = max_jobs_per_org
        self.fetch_details = fetch_details
        self.polite_delay = polite_delay
        self.log = logger.bind(source=self.source_name)

    # ------------------------------------------------------------------
    # The contract every subclass must satisfy
    # ------------------------------------------------------------------

    @abstractmethod
    async def discover(self) -> list[RawJob]:
        """Discover all jobs for the configured orgs.

        Concrete subclasses receive their org list in ``__init__`` and use
        it inside ``discover()``. Returns a flat ``list[RawJob]`` across all
        orgs handled by this source instance.
        """
        ...

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _default_headers(self) -> dict[str, str]:
        return dict(DEFAULT_HEADERS)

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            headers=self._default_headers(),
            follow_redirects=True,
        )

    async def _polite_pause(self) -> None:
        if self.polite_delay > 0:
            await asyncio.sleep(self.polite_delay)


# ----------------------------------------------------------------------
# Aggregator helper — runs many sources concurrently
# ----------------------------------------------------------------------

async def run_sources(sources: Iterable[DiscoverySource]) -> list[RawJob]:
    """Run multiple sources concurrently and merge their results.

    Each source's failures are logged but do not stop the others — partial
    success is preferred over an all-or-nothing run.
    """
    sources_list = list(sources)
    if not sources_list:
        logger.warning("run_sources_no_sources_configured")
        return []

    async def _safe_run(src: DiscoverySource) -> list[RawJob]:
        try:
            jobs = await src.discover()
            src.log.info("source_done", count=len(jobs))
            return jobs
        except Exception as exc:  # pragma: no cover
            src.log.error(
                "source_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

    results = await asyncio.gather(*(_safe_run(s) for s in sources_list))
    merged: list[RawJob] = []
    for r in results:
        merged.extend(r)
    logger.info(
        "run_sources_complete",
        total_jobs=len(merged),
        source_count=len(sources_list),
    )
    return merged