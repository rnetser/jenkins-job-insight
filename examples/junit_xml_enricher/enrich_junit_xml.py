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

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from dotenv import load_dotenv

# Add sibling example directory to path for shared utilities
_CONFTEST_DIR = Path(__file__).resolve().parent.parent / "pytest_junitxml"
sys.path.insert(0, str(_CONFTEST_DIR))

from conftest_junit_ai_utils import (  # noqa: E402
    _apply_analysis_to_xml,
    _extract_failures_from_xml,
    _fetch_analysis_from_server,
)

EXIT_SUCCESS = 0
EXIT_NO_FAILURES = 1
EXIT_SERVER_ERROR = 2
EXIT_INVALID_INPUT = 3

logger = logging.getLogger("jji-enricher")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _print_failures_summary(failures: list[dict[str, str]]) -> None:
    """Print a human-readable summary of extracted failures."""
    print(f"\nFound {len(failures)} failure(s):\n")
    for i, failure in enumerate(failures, 1):
        status = failure["status"]
        name = failure["test_name"]
        msg = failure["error_message"]
        # Truncate long messages for display
        if len(msg) > 120:
            msg = msg[:117] + "..."
        print(f"  {i}. [{status}] {name}")
        print(f"     {msg}")
    print()


def main() -> int:
    """CLI entry point for JUnit XML AI enrichment.

    Returns:
        Exit code (0=success, 1=no failures, 2=server error, 3=invalid input).
    """
    parser = argparse.ArgumentParser(
        description="Enrich JUnit XML files with AI failure analysis from a JJI server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s report.xml --server-url http://localhost:8000\n"
            "  %(prog)s report.xml --dry-run\n"
            "  %(prog)s report.xml --ai-provider gemini --ai-model gemini-2.5-pro\n"
            "  %(prog)s report.xml -v\n"
        ),
    )
    parser.add_argument(
        "xml_path",
        type=Path,
        help="Path to JUnit XML file to enrich",
    )
    parser.add_argument(
        "--server-url",
        default=None,
        help="JJI server URL (env: JJI_SERVER_URL, required unless --dry-run)",
    )
    parser.add_argument(
        "--ai-provider",
        default=None,
        help='AI provider: claude, gemini, or cursor (env: JJI_AI_PROVIDER, default: "claude")',
    )
    parser.add_argument(
        "--ai-model",
        default=None,
        help='AI model name (env: JJI_AI_MODEL, default: "claude-opus-4-6[1m]")',
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Request timeout in seconds (env: JJI_TIMEOUT, default: 600)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and show failures without sending to server",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load .env file
    load_dotenv()

    # Validate XML path
    xml_path: Path = args.xml_path
    if not xml_path.exists():
        logger.error("File not found: %s", xml_path)
        return EXIT_INVALID_INPUT

    if not xml_path.is_file():
        logger.error("Not a file: %s", xml_path)
        return EXIT_INVALID_INPUT

    # Parse XML
    try:
        failures = _extract_failures_from_xml(xml_path)
    except ET.ParseError as exc:
        logger.error("Failed to parse XML: %s", exc)
        return EXIT_INVALID_INPUT

    if not failures:
        print("No failures found in JUnit XML. Nothing to do.")
        return EXIT_NO_FAILURES

    _print_failures_summary(failures)

    # Dry run: just show failures and exit
    if args.dry_run:
        print("Dry run mode: skipping server analysis.")
        return EXIT_SUCCESS

    # Resolve configuration (CLI args > env vars > defaults)
    server_url = args.server_url or os.environ.get("JJI_SERVER_URL", "")
    if not server_url:
        logger.error(
            "JJI_SERVER_URL is required. Set via --server-url or JJI_SERVER_URL env var."
        )
        return EXIT_INVALID_INPUT

    ai_provider = args.ai_provider or os.environ.get("JJI_AI_PROVIDER", "claude")
    ai_model = args.ai_model or os.environ.get("JJI_AI_MODEL", "claude-opus-4-6[1m]")

    # Resolve timeout and set in environment for the shared utility function
    # (_fetch_analysis_from_server reads JJI_TIMEOUT from os.environ)
    timeout_raw = args.timeout
    if timeout_raw is None:
        try:
            timeout_raw = int(os.environ.get("JJI_TIMEOUT", "600"))
        except ValueError:
            logger.warning("Invalid JJI_TIMEOUT value, using default 600 seconds")
            timeout_raw = 600
    timeout: int = timeout_raw
    os.environ["JJI_TIMEOUT"] = str(timeout)

    logger.info("Server URL: %s", server_url)
    logger.info("AI provider: %s, model: %s", ai_provider, ai_model)
    logger.info("Timeout: %d seconds", timeout)
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
    )

    if not analysis_map:
        logger.error("Server returned no analysis results.")
        return EXIT_SERVER_ERROR

    logger.info("Received analysis for %d failure(s)", len(analysis_map))

    # Apply analysis to XML
    try:
        _apply_analysis_to_xml(
            xml_path=xml_path,
            analysis_map=analysis_map,
            html_report_url=html_report_url,
        )
    except Exception as exc:
        logger.error("Failed to apply analysis to XML: %s", exc)
        logger.error("Original XML has been restored from backup.")
        return EXIT_SERVER_ERROR

    # Print summary
    print(f"Successfully enriched {xml_path} with AI analysis.")
    if html_report_url:
        print(f"HTML report: {html_report_url}")

    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
