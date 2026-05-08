"""Workday job discovery source.

Calls the public Workday CXS JSON endpoints used by every
``*.myworkdayjobs.com`` careers site:

    POST /wday/cxs/{company}/{site}/jobs       — paginated listing
    GET  /wday/cxs/{company}/{site}{path}      — single job detail (optional)

We focus on the **listing** endpoint for Phase 1A — it gives us everything
the dashboard needs (title, location, posted date, URL). Detail fetching
is wired in but disabled by default (slow + we don't need full descriptions
until Phase 2 LLM scoring).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

import httpx
import structlog

from job_autopilot.discover.base import DiscoverySource
from job_autopilot.models import RawJob, WorkdayOrg

logger = structlog.get_logger(__name__)


# ----------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------

PAGE_SIZE = 20
"""Jobs requested per listing page. Workday max is typically ~20."""

MAX_PAGES_SAFETY = 200
"""Hard cap on pagination loops — guards against runaway loops."""

RETRY_ATTEMPTS = 3
"""How many times to retry a transient HTTP failure (429/5xx)."""

RETRY_BACKOFF_BASE = 0.5
"""Base seconds for exponential backoff (0.5, 1.0, 2.0, ...)."""


# ----------------------------------------------------------------------
# Source
# ----------------------------------------------------------------------

class WorkdaySource(DiscoverySource):
    """Discovery source for Workday-hosted careers sites."""

    source_name = "workday"

    def __init__(
        self,
        orgs: Iterable[WorkdayOrg],
        **kwargs: Any,
    ) -> None:
        """Build a Workday source for one or more configured orgs.

        Args:
            orgs: Iterable of ``WorkdayOrg`` configs (already filtered to enabled ones).
            **kwargs: Forwarded to ``DiscoverySource.__init__``
                      (timeout, fetch_details, polite_delay, max_jobs_per_org).
        """
        super().__init__(**kwargs)
        self.orgs: list[WorkdayOrg] = list(orgs)

    # ------------------------------------------------------------------
    # URL builders
    # ------------------------------------------------------------------

    @staticmethod
    def _list_url(org: WorkdayOrg) -> str:
        return (
            f"https://{org.company}.wd{org.num}.myworkdayjobs.com"
            f"/wday/cxs/{org.company}/{org.site}/jobs"
        )

    @staticmethod
    def _detail_url(org: WorkdayOrg, external_path: str) -> str:
        return (
            f"https://{org.company}.wd{org.num}.myworkdayjobs.com"
            f"/wday/cxs/{org.company}/{org.site}{external_path}"
        )

    @staticmethod
    def _public_job_url(org: WorkdayOrg, external_path: str) -> str:
        return (
            f"https://{org.company}.wd{org.num}.myworkdayjobs.com"
            f"/en-US/{org.site}{external_path}"
        )

    # ------------------------------------------------------------------
    # Public entrypoint (the abstract method we must implement)
    # ------------------------------------------------------------------

    async def discover(self) -> list[RawJob]:
        """Scrape all configured orgs concurrently and return all jobs."""
        if not self.orgs:
            self.log.warning("workday_no_orgs_configured")
            return []

        async with self._build_client() as client:
            results = await asyncio.gather(
                *(self._scrape_org(client, o) for o in self.orgs),
                return_exceptions=True,
            )

        merged: list[RawJob] = []
        for org, result in zip(self.orgs, results, strict=True):
            if isinstance(result, BaseException):
                self.log.error(
                    "workday_org_failed",
                    company=org.company,
                    error=str(result),
                    error_type=type(result).__name__,
                )
                continue
            merged.extend(result)

        self.log.info(
            "workday_discover_complete",
            orgs=len(self.orgs),
            jobs=len(merged),
        )
        return merged

    # ------------------------------------------------------------------
    # Per-org scrape
    # ------------------------------------------------------------------

    async def _scrape_org(
        self,
        client: httpx.AsyncClient,
        org: WorkdayOrg,
    ) -> list[RawJob]:
        """Paginate through all jobs for a single org and return RawJobs."""
        org_log = self.log.bind(company=org.company)
        url = self._list_url(org)
        jobs: list[RawJob] = []
        offset = 0

        for page in range(MAX_PAGES_SAFETY):
            payload = {
                "appliedFacets": {},
                "limit": PAGE_SIZE,
                "offset": offset,
                "searchText": "",
            }
            data = await self._post_json_with_retry(client, url, payload, org_log)
            if data is None:
                break

            batch = data.get("jobPostings") or []
            total = int(data.get("total") or 0)

            if not batch:
                break

            for jp in batch:
                try:
                    job = self._parse_job(org, jp)
                except Exception as exc:
                    org_log.warning(
                        "workday_parse_skip",
                        error=str(exc),
                        title=jp.get("title"),
                    )
                    continue

                jobs.append(job)
                if (
                    self.max_jobs_per_org is not None
                    and len(jobs) >= self.max_jobs_per_org
                ):
                    break

            org_log.debug(
                "workday_page_done",
                page=page,
                offset=offset,
                got=len(batch),
                so_far=len(jobs),
                total_advertised=total,
            )

            if (
                self.max_jobs_per_org is not None
                and len(jobs) >= self.max_jobs_per_org
            ):
                break

            offset += PAGE_SIZE
            if total and offset >= total:
                break

        # Optional detail enrichment (off by default in Phase 1A)
        if self.fetch_details:
            jobs = await self._enrich_with_details(client, org, jobs)

        org_log.info("workday_org_done", jobs=len(jobs))
        return jobs

    # ------------------------------------------------------------------
    # Parsing — Workday JSON → RawJob
    # ------------------------------------------------------------------

    def _parse_job(self, org: WorkdayOrg, jp: dict[str, Any]) -> RawJob:
        """Convert a Workday ``jobPosting`` dict into a validated RawJob."""
        title = (jp.get("title") or "").strip()
        if not title:
            raise ValueError("missing title")

        external_path: str = jp.get("externalPath") or ""
        job_req_id = jp.get("jobReqId") or None
        location = (jp.get("locationsText") or None)
        posted_on = jp.get("postedOn") or None
        job_family = jp.get("jobFamilyGroup") or None

        bullets = jp.get("bulletFields") or []
        employment_type = bullets[0] if bullets else None

        url = self._public_job_url(org, external_path) if external_path else self._list_url(org)

        return RawJob(
            id=RawJob.make_id(self.source_name, org.company, job_req_id),
            source=self.source_name,
            company=org.company,
            company_display=org.display_name,
            title=title,
            location=location,
            posted_on=posted_on,
            job_req_id=job_req_id,
            employment_type=employment_type,
            job_family=job_family,
            url=url,
        )

    # ------------------------------------------------------------------
    # Detail fetching (optional, used only when fetch_details=True)
    # ------------------------------------------------------------------

    async def _enrich_with_details(
        self,
        client: httpx.AsyncClient,
        org: WorkdayOrg,
        jobs: list[RawJob],
    ) -> list[RawJob]:
        """Fetch full description for each job. Sequential + polite — slow on purpose."""
        out: list[RawJob] = []
        for j in jobs:
            external_path = self._extract_external_path(j.url, org)
            if not external_path:
                out.append(j)
                continue

            details = await self._get_json_with_retry(
                client,
                self._detail_url(org, external_path),
                self.log.bind(company=org.company, job_id=j.id),
            )
            if details:
                info = details.get("jobPostingInfo") or {}
                description = info.get("jobDescription") or None
                qualifications = info.get("qualifications") or None
                responsibilities = info.get("responsibilities") or None
                remote_type = info.get("workplaceType") or None

                j = j.model_copy(
                    update={
                        "description": description,
                        "qualifications": qualifications,
                        "responsibilities": responsibilities,
                        "remote_type": remote_type,
                        "content_hash": RawJob.compute_content_hash(j.title, description),
                    }
                )
            out.append(j)
            await self._polite_pause()
        return out

    @staticmethod
    def _extract_external_path(public_url: str, org: WorkdayOrg) -> str | None:
        """Recover the externalPath from a public job URL."""
        marker = f"/en-US/{org.site}"
        idx = public_url.find(marker)
        if idx < 0:
            return None
        return public_url[idx + len(marker):]

    # ------------------------------------------------------------------
    # HTTP helpers — retry on 429/5xx with exponential backoff
    # ------------------------------------------------------------------

    async def _post_json_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any],
        log: Any,
    ) -> dict[str, Any] | None:
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (429, 500, 502, 503, 504):
                    await self._backoff(attempt)
                    log.warning("workday_http_retry", status=resp.status_code, attempt=attempt)
                    continue
                log.error("workday_http_error", status=resp.status_code, url=url)
                return None
            except (httpx.RequestError, ValueError) as exc:
                log.warning("workday_http_exception", error=str(exc), attempt=attempt)
                await self._backoff(attempt)
        log.error("workday_http_giveup", url=url)
        return None

    async def _get_json_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        log: Any,
    ) -> dict[str, Any] | None:
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (429, 500, 502, 503, 504):
                    await self._backoff(attempt)
                    continue
                return None
            except (httpx.RequestError, ValueError):
                await self._backoff(attempt)
        return None

    @staticmethod
    async def _backoff(attempt: int) -> None:
        """Exponential backoff: 0.5s, 1.0s, 2.0s, ..."""
        await asyncio.sleep(RETRY_BACKOFF_BASE * (2 ** (attempt - 1)))