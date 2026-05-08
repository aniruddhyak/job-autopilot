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

# A module-level logger that all subclasses can use directly.
logger = structlog.get_logger(__name__)


# ----------------------------------------------------------------------
# Defaults shared across HTTP-based sources
# ----------------------------------------------------------------------

DEFAULT_TIMEOUT = 15.0
"""Per-request timeout in seconds."""

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
"""Pretend to be a regular browser — most ATS APIs accept this fine."""

DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": DEFAULT_USER_AGENT,
}


# ----------------------------------------------------------------------
# Abstract base class
# ----------------------------------------------------------------------

class DiscoverySource(ABC):
    """Abstract base for all discovery sources.

    Subclasses must implement ``discover()``. They may also override
    ``_default_headers`` if a particular ATS needs special headers.
    """

    #: Subclasses set this — used in ``RawJob.source`` and in logs.
    source_name: str = "base"

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_jobs_per_org: int | None = None,
        fetch_details: bool = False,
        polite_delay: float = 0.0,
    ) -> None:
        """Initialize the source.

        Args:
            timeout: Per-HTTP-request timeout in seconds.
            max_jobs_per_org: Cap on jobs per organization (``None`` = no cap).
            fetch_details: If ``True``, fetch full job descriptions (slower).
            polite_delay: Sleep time (s) between detail requests to avoid throttling.
        """
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
    # Shared helpers (subclasses can use these but don't have to)
    # ------------------------------------------------------------------

    def _default_headers(self) -> dict[str, str]:
        """Default HTTP headers. Override if a source needs special headers."""
        return dict(DEFAULT_HEADERS)

    def _build_client(self) -> httpx.AsyncClient:
        """Create a configured async HTTP client.

        Subclasses can call this from inside ``discover()`` like:

            async with self._build_client() as client:
                ...
        """
        return httpx.AsyncClient(
            timeout=self.timeout,
            headers=self._default_headers(),
            follow_redirects=True,
        )

    async def _polite_pause(self) -> None:
        """Sleep ``polite_delay`` seconds (no-op if 0)."""
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
        except Exception as exc:  # pragma: no cover — defensive
            src.log.error("source_failed", error=str(exc), error_type=type(exc).__name__)
            return []

    results = await asyncio.gather(*(_safe_run(s) for s in sources_list))
    merged: list[RawJob] = []
    for r in results:
        merged.extend(r)
    logger.info("run_sources_complete", total_jobs=len(merged), source_count=len(sources_list))
    return merged