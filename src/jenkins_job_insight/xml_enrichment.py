"""XML extraction and enrichment functions for JUnit XML processing."""

import logging
from typing import Any
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import fromstring as safe_fromstring

logger = logging.getLogger(__name__)


def extract_failures_from_xml(raw_xml: str) -> list[dict[str, str]]:
    """Extract test failures and errors from a JUnit XML string.

    Parses the XML and finds all testcase elements with failure or error
    child elements, extracting test name, error message, and stack trace.

    Args:
        raw_xml: JUnit XML content as a string.

    Returns:
        List of failure dicts with test_name, error_message, stack_trace, and status.

    Raises:
        ET.ParseError: If the XML is malformed.
    """
    root = safe_fromstring(raw_xml)
    failures: list[dict[str, str]] = []

    for testcase in root.iter("testcase"):
        failure_elem = testcase.find("failure")
        error_elem = testcase.find("error")
        result_elem = failure_elem if failure_elem is not None else error_elem

        if result_elem is None:
            continue

        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        test_name = f"{classname}.{name}" if classname else name

        if not test_name:
            logger.warning("Skipping testcase with empty name attribute")
            continue

        failures.append(
            {
                "test_name": test_name,
                "error_message": result_elem.get("message", "")
                or ((result_elem.text or "").split("\n")[0].strip()),
                "stack_trace": result_elem.text or "",
                "status": "ERROR"
                if error_elem is not None and failure_elem is None
                else "FAILED",
            }
        )

    return failures


def apply_analysis_to_xml(
    raw_xml: str,
    analysis_map: dict[tuple[str, str], dict[str, Any]],
    html_report_url: str = "",
) -> str:
    """Apply AI analysis results and HTML report URL to JUnit XML string.

    Uses exact (classname, name) matching since failures are extracted from
    the same XML content, guaranteeing identical attribute values.

    Args:
        raw_xml: Original JUnit XML content as a string.
        analysis_map: Mapping of (classname, test_name) to analysis results.
        html_report_url: URL to the HTML report, added as a testsuite-level property.

    Returns:
        Enriched JUnit XML as a string.
    """
    root = safe_fromstring(raw_xml)
    matched_keys: set[tuple[str, str]] = set()

    for testcase in root.iter("testcase"):
        key = (testcase.get("classname", ""), testcase.get("name", ""))
        analysis = analysis_map.get(key)
        if analysis:
            _inject_analysis(testcase, analysis)
            matched_keys.add(key)

    unmatched = set(analysis_map.keys()) - matched_keys
    if unmatched:
        logger.warning(
            "%d analysis results did not match any testcase: %s",
            len(unmatched),
            unmatched,
        )

    # Add html_report_url to the first testsuite only
    if html_report_url:
        first_testsuite = next(root.iter("testsuite"), None)
        # If root itself is a testsuite, use it
        if first_testsuite is None and root.tag == "testsuite":
            first_testsuite = root
        if first_testsuite is not None:
            ts_props = first_testsuite.find("properties")
            if ts_props is None:
                ts_props = ET.Element("properties")
                first_testsuite.insert(0, ts_props)
            _add_property(ts_props, "html_report_url", html_report_url)
        else:
            logger.warning(
                "Could not add html_report_url: no testsuite element found in XML"
            )

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _inject_analysis(testcase: Element, analysis: dict[str, Any]) -> None:
    """Inject AI analysis into a JUnit XML testcase element.

    Adds structured properties (classification, code fix, bug report) and a
    human-readable summary to the testcase's system-out section.

    Args:
        testcase: The XML testcase element to enrich.
        analysis: Analysis dict with classification, details, affected_tests, etc.
    """
    # This is the same logic as in conftest_junit_ai_utils.py
    properties = testcase.find("properties")
    if properties is None:
        properties = ET.SubElement(testcase, "properties")

    _add_property(properties, "ai_classification", analysis.get("classification", ""))
    _add_property(properties, "ai_details", analysis.get("details", ""))

    affected = analysis.get("affected_tests", [])
    if affected:
        _add_property(properties, "ai_affected_tests", ", ".join(affected))

    code_fix = analysis.get("code_fix")
    if code_fix and isinstance(code_fix, dict):
        _add_property(properties, "ai_code_fix_file", code_fix.get("file", ""))
        _add_property(properties, "ai_code_fix_line", str(code_fix.get("line", "")))
        _add_property(properties, "ai_code_fix_change", code_fix.get("change", ""))

    bug_report = analysis.get("product_bug_report")
    if bug_report and isinstance(bug_report, dict):
        _add_property(properties, "ai_bug_title", bug_report.get("title", ""))
        _add_property(properties, "ai_bug_severity", bug_report.get("severity", ""))
        _add_property(properties, "ai_bug_component", bug_report.get("component", ""))
        _add_property(
            properties, "ai_bug_description", bug_report.get("description", "")
        )

        jira_matches = bug_report.get("jira_matches", [])
        for idx, match in enumerate(jira_matches):
            if isinstance(match, dict):
                _add_property(
                    properties, f"ai_jira_match_{idx}_key", match.get("key", "")
                )
                _add_property(
                    properties, f"ai_jira_match_{idx}_summary", match.get("summary", "")
                )
                _add_property(
                    properties, f"ai_jira_match_{idx}_status", match.get("status", "")
                )
                _add_property(
                    properties, f"ai_jira_match_{idx}_url", match.get("url", "")
                )
                _add_property(
                    properties,
                    f"ai_jira_match_{idx}_priority",
                    match.get("priority", ""),
                )
                score = match.get("score")
                if score is not None:
                    _add_property(properties, f"ai_jira_match_{idx}_score", str(score))

    text = _format_analysis_text(analysis)
    if text:
        system_out = testcase.find("system-out")
        if system_out is None:
            system_out = ET.SubElement(testcase, "system-out")
            system_out.text = text
        else:
            existing = system_out.text or ""
            system_out.text = (
                f"{existing}\n\n--- AI Analysis ---\n{text}" if existing else text
            )


def _add_property(properties_elem: Element, name: str, value: str) -> None:
    """Add a property sub-element if value is non-empty."""
    if value:
        prop = ET.SubElement(properties_elem, "property")
        prop.set("name", name)
        prop.set("value", value)


def _format_analysis_text(analysis: dict[str, Any]) -> str:
    """Format analysis dict as human-readable text for system-out."""
    parts = []

    classification = analysis.get("classification", "")
    if classification:
        parts.append(f"Classification: {classification}")

    details = analysis.get("details", "")
    if details:
        parts.append(f"\n{details}")

    code_fix = analysis.get("code_fix")
    if code_fix and isinstance(code_fix, dict):
        parts.append("\nCode Fix:")
        parts.append(f"  File: {code_fix.get('file', '')}")
        parts.append(f"  Line: {code_fix.get('line', '')}")
        parts.append(f"  Change: {code_fix.get('change', '')}")

    bug_report = analysis.get("product_bug_report")
    if bug_report and isinstance(bug_report, dict):
        parts.append("\nProduct Bug:")
        parts.append(f"  Title: {bug_report.get('title', '')}")
        parts.append(f"  Severity: {bug_report.get('severity', '')}")
        parts.append(f"  Component: {bug_report.get('component', '')}")
        parts.append(f"  Description: {bug_report.get('description', '')}")

        jira_matches = bug_report.get("jira_matches", [])
        if jira_matches:
            parts.append("\nPossible Jira Matches:")
            for match in jira_matches:
                if isinstance(match, dict):
                    key = match.get("key", "")
                    summary = match.get("summary", "")
                    status = match.get("status", "")
                    url = match.get("url", "")
                    parts.append(f"  {key}: {summary} [{status}] {url}")

    return "\n".join(parts) if parts else ""
