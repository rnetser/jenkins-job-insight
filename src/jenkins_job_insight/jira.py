"""Jira integration for product bug deduplication.

Searches Jira for existing issues that match product bug failures,
helping teams avoid filing duplicate bug reports.
"""

import asyncio
import os
from collections.abc import Sequence

import httpx
from simple_logger.logger import get_logger

from jenkins_job_insight.config import Settings
from jenkins_job_insight.models import (
    AnalysisDetail,
    FailureAnalysis,
    JiraMatch,
    ProductBugReport,
)

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))


class JiraClient:
    """HTTP client for Jira REST API.

    Auto-detects Cloud (email + API token, REST API v3) vs
    Server/DC (PAT, REST API v2) based on provided credentials.
    """

    def __init__(self, settings: Settings) -> None:
        self._base_url = (settings.jira_url or "").rstrip("/")
        self._project_key = settings.jira_project_key
        self._max_results = settings.jira_max_results

        # Detect Cloud vs Server/DC
        self._auth: tuple[str, str] | None
        if settings.jira_email and settings.jira_api_token:
            # Cloud: Basic auth with email:token, API v3
            self._auth = (settings.jira_email, settings.jira_api_token)
            self._api_path = "/rest/api/3"
            self._headers: dict[str, str] = {}
        else:
            # Server/DC: PAT bearer token, API v2
            self._auth = None
            self._api_path = "/rest/api/2"
            self._headers = {"Authorization": f"Bearer {settings.jira_pat}"}

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            auth=self._auth,
            headers=self._headers,
            verify=settings.jira_ssl_verify,
            timeout=30.0,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "JiraClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def search(self, keywords: list[str]) -> list[JiraMatch]:
        """Search Jira for issues matching the given keywords.

        Builds a JQL query using ``text ~ "keyword"`` clauses joined
        with OR, optionally scoped to a project.

        Args:
            keywords: Search terms to look for in Jira issues.

        Returns:
            List of JiraMatch objects sorted by relevance score.
        """
        if not keywords:
            return []

        # Build JQL: text ~ "kw1" OR text ~ "kw2" ...
        text_clauses = " OR ".join(
            f'text ~ "{kw.replace(chr(34), "")}"' for kw in keywords
        )
        jql = f"({text_clauses})"
        if self._project_key:
            jql = f'project = "{self._project_key}" AND {jql}'

        jql += " ORDER BY updated DESC"

        params = {
            "jql": jql,
            "maxResults": self._max_results,
            "fields": "summary,status,priority",
        }

        response = await self._client.get(
            f"{self._api_path}/search",
            params=params,
        )
        response.raise_for_status()
        data = response.json()

        matches: list[JiraMatch] = []
        for issue in data.get("issues", []):
            fields = issue.get("fields", {})

            # Extract status name (handles both Cloud and Server response shapes)
            status_obj = fields.get("status") or {}
            status_name = (
                status_obj.get("name", "") if isinstance(status_obj, dict) else ""
            )

            # Extract priority name
            priority_obj = fields.get("priority") or {}
            priority_name = (
                priority_obj.get("name", "") if isinstance(priority_obj, dict) else ""
            )

            issue_key = issue.get("key", "")
            issue_summary = fields.get("summary", "")
            score = _compute_relevance(keywords, issue_key, issue_summary)

            matches.append(
                JiraMatch(
                    key=issue_key,
                    summary=issue_summary,
                    status=status_name,
                    priority=priority_name,
                    url=f"{self._base_url}/browse/{issue_key}",
                    score=score,
                )
            )

        # Sort by relevance score descending
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches


def _compute_relevance(keywords: list[str], key: str, summary: str) -> float:
    """Compute a simple keyword-in-text relevance score.

    Checks how many of the search keywords appear in the issue
    summary (case-insensitive). Returns a value between 0.0 and 1.0.

    Args:
        keywords: The search keywords used.
        key: The Jira issue key.
        summary: The Jira issue summary text.

    Returns:
        Float between 0.0 and 1.0 indicating relevance.
    """
    if not keywords:
        return 0.0

    searchable = f"{key} {summary}".lower()
    hits = sum(1 for kw in keywords if kw.lower() in searchable)
    return round(hits / len(keywords), 2)


def _collect_product_bug_reports(
    failures: Sequence[FailureAnalysis],
) -> list[ProductBugReport]:
    """Collect all ProductBugReport instances from a list of failures.

    Args:
        failures: List of failure analyses to scan.

    Returns:
        List of ProductBugReport objects found.
    """
    reports: list[ProductBugReport] = []
    for failure in failures:
        detail: AnalysisDetail = failure.analysis
        if isinstance(detail.product_bug_report, ProductBugReport):
            reports.append(detail.product_bug_report)
    return reports


async def enrich_with_jira_matches(
    failures: Sequence[FailureAnalysis],
    settings: Settings,
) -> None:
    """Search Jira for matching issues and attach results in-place.

    Collects PRODUCT BUG failures, deduplicates by keyword set,
    searches Jira in parallel, and attaches matches to each
    ``ProductBugReport.jira_matches``.

    This function never raises — all errors are logged and swallowed
    so the analysis pipeline is never interrupted.

    Args:
        failures: Failure analyses to enrich (modified in-place).
        settings: Application settings with Jira configuration.
    """
    if not settings.jira_enabled:
        return

    reports = _collect_product_bug_reports(failures)
    if not reports:
        return

    # Deduplicate by keyword set — same keywords = one Jira search
    keyword_to_reports: dict[tuple[str, ...], list[ProductBugReport]] = {}
    for report in reports:
        if not report.jira_search_keywords:
            continue
        key = tuple(sorted(report.jira_search_keywords))
        keyword_to_reports.setdefault(key, []).append(report)

    if not keyword_to_reports:
        logger.debug(
            "No PRODUCT BUG failures with jira_search_keywords, skipping Jira lookup"
        )
        return

    logger.info(
        "Searching Jira for %d unique keyword set(s) across %d PRODUCT BUG failure(s)",
        len(keyword_to_reports),
        len(reports),
    )

    async with JiraClient(settings) as client:
        try:
            # Search Jira for each unique keyword set in parallel
            async def _search_safe(keywords: list[str]) -> list[JiraMatch]:
                try:
                    return await client.search(keywords)
                except Exception:
                    logger.exception("Jira search failed for keywords: %s", keywords)
                    return []

            tasks = [_search_safe(list(kw_tuple)) for kw_tuple in keyword_to_reports]
            results = await asyncio.gather(*tasks)

            # Attach matches to all reports sharing the same keyword set
            for kw_tuple, matches in zip(keyword_to_reports, results):
                for report in keyword_to_reports[kw_tuple]:
                    report.jira_matches = matches

            total_matches = sum(len(r) for r in results)
            logger.info("Jira search complete: %d match(es) found", total_matches)

        except Exception:
            logger.exception("Jira enrichment failed unexpectedly")
