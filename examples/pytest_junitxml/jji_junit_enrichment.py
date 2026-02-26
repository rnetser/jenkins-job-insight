"""JUnit XML enrichment client for Jenkins Job Insight.

Sends raw JUnit XML to a JJI server for AI-powered failure analysis
and returns the enriched XML with analysis results injected.

This module can be used standalone or from the pytest conftest integration.

Usage:
    from jji_junit_enrichment import enrich_junit_xml_via_server

    result = enrich_junit_xml_via_server(
        server_url="http://localhost:8000",
        raw_xml=Path("report.xml").read_text(),
        ai_provider="claude",
        ai_model="claude-opus-4-6[1m]",
    )
    if result.get("enriched_xml"):
        Path("report.xml").write_text(result["enriched_xml"])
"""

import logging
from typing import Any

import requests

logger = logging.getLogger("jenkins-job-insight")


def enrich_junit_xml_via_server(
    server_url: str,
    raw_xml: str,
    ai_provider: str,
    ai_model: str,
    timeout: int = 600,
) -> dict[str, Any]:
    """Send raw JUnit XML to a JJI server for AI analysis and enrichment.

    Posts the XML content to the /analyze-failures endpoint. The server
    extracts failures, runs AI analysis, and returns the enriched XML
    with analysis results injected back into it.

    Args:
        server_url: Base URL of the JJI server (e.g., "http://localhost:8000").
        raw_xml: JUnit XML content as a string.
        ai_provider: AI provider to use (claude, gemini, or cursor).
        ai_model: AI model name.
        timeout: Request timeout in seconds (default: 600).

    Returns:
        Server response dict containing at minimum:
        - enriched_xml (str | None): Enriched XML content, or None if no failures found
        - html_report_url (str): URL to the HTML report
        - status (str): "completed" or "failed"
        - failures (list): Analyzed failure details

    Raises:
        requests.RequestException: If the HTTP request fails.
        ValueError: If the response is not valid JSON.
    """
    payload: dict[str, Any] = {
        "raw_xml": raw_xml,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
    }

    response = requests.post(
        f"{server_url.rstrip('/')}/analyze-failures",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()
