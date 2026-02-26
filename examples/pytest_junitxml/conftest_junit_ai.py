"""
Standalone conftest.py for enriching JUnit XML with AI failure analysis.

Sends the raw JUnit XML to a jenkins-job-insight server for AI analysis
and writes the enriched XML back to the same file.

Usage:
    1. Copy conftest_junit_ai.py to your project root and rename to conftest.py
    2. Install dependencies: pip install requests python-dotenv
    3. Create a .env file or set environment variables:
       - JJI_SERVER_URL: jenkins-job-insight server URL (required)
       - JJI_AI_PROVIDER: AI provider - claude, gemini, or cursor (default: claude)
       - JJI_AI_MODEL: AI model (default: claude-opus-4-6[1m])
       - JJI_TIMEOUT: request timeout in seconds (default: 600)
    4. Run: pytest --junitxml=report.xml --analyze-with-ai

Requirements:
    - requests
    - python-dotenv
    - A running jenkins-job-insight server
"""

import logging
import os
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

logger = logging.getLogger("jenkins-job-insight")


def pytest_addoption(parser):
    """Add --analyze-with-ai CLI option."""
    group = parser.getgroup("jenkins-job-insight", "AI-powered failure analysis")
    group.addoption(
        "--analyze-with-ai",
        action="store_true",
        default=False,
        help="Enrich JUnit XML with AI-powered failure analysis from jenkins-job-insight",
    )


def pytest_sessionstart(session):
    """Set up AI analysis if --analyze-with-ai is passed."""
    if not session.config.option.analyze_with_ai:
        return

    if session.config.option.setupplan or session.config.option.collectonly:
        session.config.option.analyze_with_ai = False
        return

    load_dotenv()

    logger.info("Setting up AI-powered test failure analysis")

    if not os.environ.get("JJI_SERVER_URL"):
        logger.warning(
            "JJI_SERVER_URL is not set. Analyze with AI features will be disabled."
        )
        session.config.option.analyze_with_ai = False
    else:
        if not os.environ.get("JJI_AI_PROVIDER"):
            os.environ["JJI_AI_PROVIDER"] = "claude"

        if not os.environ.get("JJI_AI_MODEL"):
            os.environ["JJI_AI_MODEL"] = "claude-opus-4-6[1m]"


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Enrich JUnit XML with AI analysis after all tests complete.

    Reads the raw XML, POSTs it to the JJI server's /analyze-failures
    endpoint, and writes the enriched XML back to the same file.
    """
    if not session.config.option.analyze_with_ai:
        return

    xml_path_raw = getattr(session.config.option, "xmlpath", None)
    if not xml_path_raw or not Path(xml_path_raw).exists():
        return

    xml_path = Path(xml_path_raw)

    ai_provider = os.environ.get("JJI_AI_PROVIDER")
    ai_model = os.environ.get("JJI_AI_MODEL")
    if not ai_provider or not ai_model:
        logger.warning(
            "JJI_AI_PROVIDER and JJI_AI_MODEL must be set, skipping AI analysis enrichment"
        )
        return

    server_url = os.environ["JJI_SERVER_URL"]
    raw_xml = xml_path.read_text()

    try:
        timeout = int(os.environ.get("JJI_TIMEOUT", "600"))
    except ValueError:
        timeout = 600

    try:
        response = requests.post(
            f"{server_url.rstrip('/')}/analyze-failures",
            json={
                "raw_xml": raw_xml,
                "ai_provider": ai_provider,
                "ai_model": ai_model,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()
    except Exception:
        logger.exception("Failed to enrich JUnit XML, original preserved")
        return

    enriched_xml = result.get("enriched_xml")
    if enriched_xml:
        xml_path.write_text(enriched_xml)
        logger.info("JUnit XML enriched with AI analysis: %s", xml_path)
    else:
        logger.info("No enriched XML returned (no failures or analysis failed)")

    html_report_url = result.get("html_report_url", "")
    if html_report_url:
        logger.info("HTML report: %s", html_report_url)
