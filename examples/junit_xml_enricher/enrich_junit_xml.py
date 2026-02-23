#!/usr/bin/env python3
"""Standalone CLI for enriching JUnit XML files with AI failure analysis.

Parses any JUnit XML file, extracts failed test cases, sends them to a
jenkins-job-insight (JJI) server for AI analysis, and injects the analysis
results back into the XML.

SAFETY: The original XML is backed up before modification and restored if
anything goes wrong. The backup is removed on success.

Usage examples:

    # Basic usage (server URL from environment):
    export JJI_SERVER_URL=http://localhost:8000
    uv run python examples/junit_xml_enricher/enrich_junit_xml.py report.xml

    # Explicit server URL:
    uv run python examples/junit_xml_enricher/enrich_junit_xml.py report.xml --server-url http://jji.example.com:8000

    # Dry run (show failures without sending to server):
    uv run python examples/junit_xml_enricher/enrich_junit_xml.py report.xml --dry-run

    # With custom AI provider and model:
    uv run python examples/junit_xml_enricher/enrich_junit_xml.py report.xml \\
        --server-url http://localhost:8000 \\
        --ai-provider gemini \\
        --ai-model gemini-2.5-pro

    # Verbose output:
    uv run python examples/junit_xml_enricher/enrich_junit_xml.py report.xml -v

    # Using .env file (auto-loaded):
    echo 'JJI_SERVER_URL=http://localhost:8000' > .env
    uv run python examples/junit_xml_enricher/enrich_junit_xml.py report.xml

Requirements:
    - requests
    - python-dotenv

Exit codes:
    0 - Success (XML enriched with analysis)
    1 - No failures found in XML (nothing to do)
    2 - Server error (request failed or bad response)
    3 - Invalid input (file not found, parse error, missing config)
"""

import os
import sys

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import click
from dotenv import load_dotenv
from simple_logger.logger import get_logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pytest_junitxml.conftest_junit_ai_utils import (
    _apply_analysis_to_xml,
    _extract_failures_from_xml,
    _fetch_analysis_from_server,
)

EXIT_SUCCESS = 0
EXIT_NO_FAILURES = 1
EXIT_SERVER_ERROR = 2
EXIT_INVALID_INPUT = 3

logger = get_logger(name="jji-enricher", level=os.environ.get("LOG_LEVEL", "INFO"))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _log_failures_summary(failures: list[dict[str, str]]) -> None:
    """Log a human-readable summary of extracted failures."""
    logger.info("Found %d failure(s):", len(failures))
    for i, failure in enumerate(failures, 1):
        status = failure["status"]
        name = failure["test_name"]
        msg = failure["error_message"]
        logger.info("  %d. [%s] %s", i, status, name)
        logger.info("     %s", msg)


@click.command(
    help="Enrich JUnit XML files with AI failure analysis from a JJI server."
)
@click.argument("xml_path", type=click.Path(exists=False, path_type=Path))
@click.option(
    "--server-url",
    default=None,
    help="JJI server URL (env: JJI_SERVER_URL, required unless --dry-run)",
)
@click.option(
    "--ai-provider",
    default=None,
    help='AI provider: claude, gemini, or cursor (env: JJI_AI_PROVIDER, default: "claude")',
)
@click.option(
    "--ai-model",
    default=None,
    help='AI model name (env: JJI_AI_MODEL, default: "claude-opus-4-6[1m]")',
)
@click.option(
    "--timeout",
    type=int,
    default=None,
    help="Request timeout in seconds (env: JJI_TIMEOUT, default: 600)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Extract and show failures without sending to server",
)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose (DEBUG) logging")
def main(
    xml_path: Path,
    server_url: str | None,
    ai_provider: str | None,
    ai_model: str | None,
    timeout: int | None,
    dry_run: bool,
    verbose: bool,
) -> int:
    """CLI entry point for JUnit XML AI enrichment.

    Returns:
        Exit code (0=success, 1=no failures, 2=server error, 3=invalid input).
    """
    # Load .env file first so LOG_LEVEL and other env vars are available
    load_dotenv()

    # Configure logging (--verbose overrides LOG_LEVEL env var)
    if verbose:
        import logging as _logging

        logger.setLevel(_logging.DEBUG)

    # Validate XML path
    if not xml_path.exists():
        logger.error("File not found: %s", xml_path)
        return EXIT_INVALID_INPUT

    if not xml_path.is_file():
        logger.error("Not a file: %s", xml_path)
        return EXIT_INVALID_INPUT

    # Parse XML
    try:
        failures = _extract_failures_from_xml(xml_path)
    except ET.ParseError:
        logger.exception("Failed to parse XML")
        return EXIT_INVALID_INPUT

    if not failures:
        logger.info("No failures found in JUnit XML. Nothing to do.")
        return EXIT_NO_FAILURES

    _log_failures_summary(failures)

    # Dry run: just show failures and exit
    if dry_run:
        logger.info("Dry run mode: skipping server analysis.")
        return EXIT_SUCCESS

    # Resolve configuration (CLI args > env vars > defaults)
    server_url = server_url or os.environ.get("JJI_SERVER_URL", "")
    if not server_url:
        logger.error(
            "JJI_SERVER_URL is required. Set via --server-url or JJI_SERVER_URL env var."
        )
        return EXIT_INVALID_INPUT

    ai_provider = ai_provider or os.environ.get("JJI_AI_PROVIDER", "claude")
    ai_model = ai_model or os.environ.get("JJI_AI_MODEL", "claude-opus-4-6[1m]")

    # Resolve timeout (CLI arg > env var > default)
    timeout_raw = timeout
    if timeout_raw is None:
        try:
            timeout_raw = int(os.environ.get("JJI_TIMEOUT", "600"))
        except ValueError:
            logger.warning("Invalid JJI_TIMEOUT value, using default 600 seconds")
            timeout_raw = 600
    resolved_timeout: int = timeout_raw

    logger.info("Server URL: %s", server_url)
    logger.info("AI provider: %s, model: %s", ai_provider, ai_model)
    logger.info("Timeout: %d seconds", resolved_timeout)
    logger.info("Sending %d failure(s) for analysis...", len(failures))

    # Build payload and send to server
    payload: dict[str, Any] = {
        "failures": failures,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
    }

    analysis_map, html_report_url = _fetch_analysis_from_server(
        server_url=server_url,
        payload=payload,
        timeout=resolved_timeout,
    )

    if not analysis_map and not html_report_url:
        logger.error("Server returned no analysis results.")
        return EXIT_SERVER_ERROR

    if analysis_map:
        logger.info("Received analysis for %d failure(s)", len(analysis_map))

    # Apply analysis to XML
    try:
        _apply_analysis_to_xml(
            xml_path=xml_path,
            analysis_map=analysis_map,
            html_report_url=html_report_url,
        )
    except Exception:
        logger.exception(
            "Failed to apply analysis to XML; original restored from backup."
        )
        return EXIT_SERVER_ERROR

    logger.info("Successfully enriched %s with AI analysis.", xml_path)
    if html_report_url:
        logger.info("HTML report: %s", html_report_url)

    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main(standalone_mode=False))
