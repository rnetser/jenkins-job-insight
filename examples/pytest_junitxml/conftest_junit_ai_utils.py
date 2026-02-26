"""Pytest utilities for JUnit XML AI analysis enrichment.

Thin glue between pytest session hooks and the JJI server.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from jenkins_job_insight.xml_enrichment import enrich_junit_xml_via_server

logger = logging.getLogger("jenkins-job-insight")


def is_dry_run(config) -> bool:
    """Check if pytest was invoked in dry-run mode (--collectonly or --setupplan)."""
    return config.option.setupplan or config.option.collectonly


def setup_ai_analysis(session) -> None:
    """Configure AI analysis for test failure reporting.

    Loads .env, validates JJI_SERVER_URL, and sets defaults for AI provider/model.
    Disables analysis if JJI_SERVER_URL is missing or if pytest was invoked
    with --collectonly or --setupplan.

    Args:
        session: The pytest session containing config options.
    """
    if is_dry_run(session.config):
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


def enrich_junit_xml(session) -> None:
    """Read JUnit XML, send to server for analysis, write enriched XML back.

    Args:
        session: The pytest session containing config options.
    """
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
        result = enrich_junit_xml_via_server(
            server_url=server_url,
            raw_xml=raw_xml,
            ai_provider=ai_provider,
            ai_model=ai_model,
            timeout=timeout,
        )
    except Exception as exc:
        logger.error("JJI server request failed: %s", exc)
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
