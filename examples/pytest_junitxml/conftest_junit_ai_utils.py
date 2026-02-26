"""Utility functions for JUnit XML AI analysis enrichment."""

import logging
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

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

    Reads the JUnit XML that pytest already generated, sends the raw XML
    content to the JJI server which extracts failures, runs AI analysis,
    and returns the enriched XML. The enriched XML is written back to the
    same file.

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

    raw_xml = xml_path.read_text()

    server_url = os.environ["JJI_SERVER_URL"]
    payload: dict[str, Any] = {
        "raw_xml": raw_xml,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
    }

    try:
        timeout = int(os.environ.get("JJI_TIMEOUT", "600"))
    except ValueError:
        timeout = 600

    try:
        response = requests.post(
            f"{server_url.rstrip('/')}/analyze-failures",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        error_detail = ""
        if isinstance(exc, requests.RequestException) and exc.response is not None:
            try:
                error_detail = f" Response: {exc.response.text}"
            except Exception as detail_exc:
                logger.debug("Could not extract response detail: %s", detail_exc)
        logger.error("Server request failed: %s%s", exc, error_detail)
        return

    enriched_xml = result.get("enriched_xml")
    if enriched_xml:
        xml_path.write_text(enriched_xml)
        logger.info("JUnit XML enriched with AI analysis: %s", xml_path)
    else:
        logger.info(
            "No enriched XML in server response (no failures found or analysis failed)"
        )

    html_report_url = result.get("html_report_url", "")
    if html_report_url:
        logger.info("HTML report: %s", html_report_url)
