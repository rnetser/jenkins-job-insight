"""One-click bug creation from failure analysis.

Generates GitHub issue / Jira bug content using AI, searches for
duplicates, and creates issues via the respective REST APIs.
"""

import json
import os
import re

import httpx
from simple_logger.logger import get_logger

from ai_cli_runner import call_ai_cli
from jenkins_job_insight.analyzer import PROVIDER_CLI_FLAGS
from jenkins_job_insight.config import Settings
from jenkins_job_insight.models import (
    AnalysisDetail,
    CodeFix,
    FailureAnalysis,
    ProductBugReport,
)

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))


def _build_failure_context(failure: FailureAnalysis) -> dict:
    """Extract structured context from a FailureAnalysis for prompt building.

    Returns a dict with test_name, error, classification, details,
    code_fix (dict or None), and product_bug (dict or None).
    """
    analysis: AnalysisDetail = failure.analysis
    code_fix = None
    if isinstance(analysis.code_fix, CodeFix) and analysis.code_fix:
        code_fix = {
            "file": analysis.code_fix.file,
            "line": analysis.code_fix.line,
            "change": analysis.code_fix.change,
        }
    product_bug = None
    if (
        isinstance(analysis.product_bug_report, ProductBugReport)
        and analysis.product_bug_report
    ):
        product_bug = {
            "title": analysis.product_bug_report.title,
            "severity": analysis.product_bug_report.severity,
            "component": analysis.product_bug_report.component,
            "description": analysis.product_bug_report.description,
            "evidence": analysis.product_bug_report.evidence,
        }
    context: dict = {
        "test_name": failure.test_name,
        "error": failure.error,
        "classification": analysis.classification,
        "details": analysis.details,
        "code_fix": code_fix,
        "product_bug": product_bug,
    }
    if analysis.artifacts_evidence:
        context["artifacts_evidence"] = analysis.artifacts_evidence
    return context


def _build_fallback_github_content(
    ctx: dict, jenkins_url: str, report_url: str, include_links: bool = False
) -> dict:
    """Build GitHub issue content from structured data when AI fails."""
    test_short = (
        ctx["test_name"].rsplit(".", 1)[-1]
        if "." in ctx["test_name"]
        else ctx["test_name"]
    )
    title = f"Test failure: {test_short}"
    if ctx["product_bug"] and ctx["product_bug"].get("title"):
        title = ctx["product_bug"]["title"]

    body_parts = [
        "## Test Failure",
        f"**Test:** `{ctx['test_name']}`",
        f"**Classification:** {ctx['classification']}",
        "",
        "## Error",
        "```",
        ctx["error"] or "No error message",
        "```",
        "",
        "## AI Analysis",
        ctx["details"],
    ]
    if ctx.get("artifacts_evidence"):
        body_parts.extend(["", "## Artifacts Evidence", ctx["artifacts_evidence"]])
    if ctx["code_fix"]:
        body_parts.extend(
            [
                "",
                "## Suggested Fix",
                f"**File:** `{ctx['code_fix']['file']}`",
                f"**Line:** {ctx['code_fix']['line']}",
                f"**Change:** {ctx['code_fix']['change']}",
            ]
        )
    if include_links:
        if jenkins_url:
            body_parts.append(f"\n## Links\n- [Jenkins Build]({jenkins_url})")
        if report_url:
            body_parts.append(f"- [Analysis Report]({report_url})")
    else:
        if jenkins_url:
            body_parts.append(f"\n## References\n- Jenkins Build: {jenkins_url}")
        if report_url:
            body_parts.append(f"- Report: {report_url}")

    return {"title": title, "body": "\n".join(body_parts)}


def _build_fallback_jira_content(
    ctx: dict, jenkins_url: str, report_url: str, include_links: bool = False
) -> dict:
    """Build Jira bug content from structured data when AI fails."""
    test_short = (
        ctx["test_name"].rsplit(".", 1)[-1]
        if "." in ctx["test_name"]
        else ctx["test_name"]
    )

    if ctx["product_bug"] and ctx["product_bug"].get("title"):
        title = ctx["product_bug"]["title"]
        body_parts = [
            "h2. Test Failure",
            f"*Test:* {{{{code}}}}{ctx['test_name']}{{{{code}}}}",
            f"*Severity:* {ctx['product_bug'].get('severity', 'Unknown')}",
            f"*Component:* {ctx['product_bug'].get('component', 'Unknown')}",
            "",
            "h2. Error",
            "{code}",
            ctx["error"] or "No error message",
            "{code}",
            "",
            "h2. AI Analysis",
            ctx["details"],
            "",
            "h2. Evidence",
            ctx["product_bug"].get("evidence", ""),
        ]
    else:
        title = f"Test failure: {test_short}"
        body_parts = [
            "h2. Test Failure",
            f"*Test:* {{{{code}}}}{ctx['test_name']}{{{{code}}}}",
            "",
            "h2. Error",
            "{code}",
            ctx["error"] or "No error message",
            "{code}",
            "",
            "h2. Analysis",
            ctx["details"],
        ]

    if ctx.get("artifacts_evidence"):
        body_parts.extend(["", "h2. Artifacts Evidence", ctx["artifacts_evidence"]])
    if include_links:
        if jenkins_url:
            body_parts.append(f"\nh2. Links\n* [Jenkins Build|{jenkins_url}]")
        if report_url:
            body_parts.append(f"* [Analysis Report|{report_url}]")
    else:
        if jenkins_url:
            body_parts.append(f"\nh2. References\n* Jenkins Build: {jenkins_url}")
        if report_url:
            body_parts.append(f"* Report: {report_url}")

    return {"title": title, "body": "\n".join(body_parts)}


def _parse_ai_issue_response(output: str) -> dict | None:
    """Parse AI response expecting {"title": ..., "body": ...}.

    Returns dict with title and body, or None if parsing fails.
    """
    text = output.strip()
    # Strip markdown code blocks
    if "```json" in text:
        start = text.index("```json") + len("```json")
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + len("```")
        end = text.index("```", start)
        text = text[start:end].strip()

    # Find JSON object
    json_start = text.find("{")
    json_end = text.rfind("}")
    if json_start != -1 and json_end != -1:
        text = text[json_start : json_end + 1]

    try:
        data = json.loads(text)
        if isinstance(data, dict) and "title" in data and "body" in data:
            return {"title": data["title"], "body": data["body"]}
    except (json.JSONDecodeError, ValueError):
        pass
    return None


# NOTE: The content generation functions below intentionally pass failure data
# directly into the AI prompt. This is NOT the same as the analysis pipeline
# where the AI should be given tools to explore data autonomously (per
# CLAUDE.md "AI Tool Access"). Here the AI is generating formatted text from
# *already-analyzed* data, not performing new analysis. The input is fully
# known and the output is a structured document -- tool access would add
# latency and complexity with no benefit.
async def generate_github_issue_content(
    failure: FailureAnalysis,
    report_url: str = "",
    ai_provider: str = "",
    ai_model: str = "",
    jenkins_url: str = "",
    ai_cli_timeout: int | None = None,
    include_links: bool = False,
) -> dict:
    """Generate GitHub issue title and body from a failure analysis using AI.

    Falls back to template-based content if AI fails.

    Args:
        failure: The failure analysis to generate content from.
        report_url: URL or reference text for the analysis report.
        ai_provider: AI provider to use.
        ai_model: AI model to use.
        jenkins_url: URL or reference text for the Jenkins build.
        ai_cli_timeout: AI CLI timeout in minutes.
        include_links: When True, include full URLs as clickable links.
            When False, include plain-text references only.

    Returns dict with "title" and "body" keys.
    """
    ctx = _build_failure_context(failure)

    code_fix_section = ""
    if ctx["code_fix"]:
        code_fix_section = (
            f"\nCode fix suggestion:\n"
            f"  File: {ctx['code_fix']['file']}\n"
            f"  Line: {ctx['code_fix']['line']}\n"
            f"  Change: {ctx['code_fix']['change']}"
        )

    artifacts_section = ""
    if ctx.get("artifacts_evidence"):
        artifacts_section = f"\nArtifacts evidence:\n{ctx['artifacts_evidence']}"

    product_bug_section = ""
    if ctx["product_bug"]:
        product_bug_section = (
            f"\nProduct bug report:\n"
            f"  Title: {ctx['product_bug']['title']}\n"
            f"  Severity: {ctx['product_bug']['severity']}\n"
            f"  Component: {ctx['product_bug']['component']}\n"
            f"  Description: {ctx['product_bug']['description']}\n"
            f"  Evidence: {ctx['product_bug']['evidence']}"
        )

    if include_links:
        links_instruction = (
            f"Jenkins build: {jenkins_url}\n"
            f"Analysis report: {report_url}\n"
            "Include clickable links to the Jenkins build and analysis report."
        )
    else:
        links_instruction = (
            f"Jenkins build: {jenkins_url}\n"
            f"Analysis report: {report_url}\n"
            "Include these as plain-text references (not clickable links)."
        )

    prompt = f"""You are generating a GitHub issue from a test failure analysis.

Test: {ctx["test_name"]}
Error: {ctx["error"]}
Classification: {ctx["classification"]}
Analysis: {ctx["details"]}
{code_fix_section}{product_bug_section}{artifacts_section}

{links_instruction}

Generate a JSON object with exactly two keys:
- "title": A concise, descriptive title (max 120 chars)
- "body": Well-formatted markdown with sections:
  - Summary of the problem
  - Error details (error message and relevant stack trace)
  - AI analysis findings
  - Artifacts evidence (if available)
  - Suggested fix (if available)
  - References to Jenkins build and analysis report

Respond with ONLY the JSON object, no other text."""

    success, output = await call_ai_cli(
        prompt,
        ai_provider=ai_provider,
        ai_model=ai_model,
        ai_cli_timeout=ai_cli_timeout,
        cli_flags=PROVIDER_CLI_FLAGS.get(ai_provider, []),
    )

    if success:
        parsed = _parse_ai_issue_response(output)
        if parsed:
            return parsed

    logger.warning(
        "AI content generation failed for GitHub issue, using fallback template"
    )
    return _build_fallback_github_content(ctx, jenkins_url, report_url, include_links)


async def generate_jira_bug_content(
    failure: FailureAnalysis,
    report_url: str = "",
    ai_provider: str = "",
    ai_model: str = "",
    jenkins_url: str = "",
    ai_cli_timeout: int | None = None,
    include_links: bool = False,
) -> dict:
    """Generate Jira bug summary and description from a failure analysis using AI.

    Falls back to template-based content if AI fails.

    Args:
        failure: The failure analysis to generate content from.
        report_url: URL or reference text for the analysis report.
        ai_provider: AI provider to use.
        ai_model: AI model to use.
        jenkins_url: URL or reference text for the Jenkins build.
        ai_cli_timeout: AI CLI timeout in minutes.
        include_links: When True, include full URLs as clickable links.
            When False, include plain-text references only.

    Returns dict with "title" and "body" keys.
    """
    ctx = _build_failure_context(failure)

    artifacts_section = ""
    if ctx.get("artifacts_evidence"):
        artifacts_section = f"\nArtifacts evidence:\n{ctx['artifacts_evidence']}"

    product_bug_section = ""
    if ctx["product_bug"]:
        product_bug_section = (
            f"\nProduct bug report:\n"
            f"  Title: {ctx['product_bug']['title']}\n"
            f"  Severity: {ctx['product_bug']['severity']}\n"
            f"  Component: {ctx['product_bug']['component']}\n"
            f"  Description: {ctx['product_bug']['description']}\n"
            f"  Evidence: {ctx['product_bug']['evidence']}"
        )

    if include_links:
        links_instruction = (
            f"Jenkins build: {jenkins_url}\n"
            f"Analysis report: {report_url}\n"
            "Include clickable links to the Jenkins build and analysis report."
        )
    else:
        links_instruction = (
            f"Jenkins build: {jenkins_url}\n"
            f"Analysis report: {report_url}\n"
            "Include these as plain-text references (not clickable links)."
        )

    prompt = f"""You are generating a Jira bug report from a test failure analysis.

Test: {ctx["test_name"]}
Error: {ctx["error"]}
Classification: {ctx["classification"]}
Analysis: {ctx["details"]}
{product_bug_section}{artifacts_section}

{links_instruction}

Generate a JSON object with exactly two keys:
- "title": A concise, descriptive bug summary (max 120 chars)
- "body": Well-formatted Jira wiki markup with sections:
  - h2. Summary
  - h2. Error (with {{{{code}}}} blocks)
  - h2. Analysis
  - h2. Artifacts Evidence (if available)
  - h2. Evidence (if available)
  - h2. References (Jenkins build and analysis report)

Respond with ONLY the JSON object, no other text."""

    success, output = await call_ai_cli(
        prompt,
        ai_provider=ai_provider,
        ai_model=ai_model,
        ai_cli_timeout=ai_cli_timeout,
        cli_flags=PROVIDER_CLI_FLAGS.get(ai_provider, []),
    )

    if success:
        parsed = _parse_ai_issue_response(output)
        if parsed:
            return parsed

    logger.warning("AI content generation failed for Jira bug, using fallback template")
    return _build_fallback_jira_content(ctx, jenkins_url, report_url, include_links)


def _parse_github_repo_url(repo_url: str) -> tuple[str, str]:
    """Extract owner and repo from a GitHub repository URL.

    Supports https://github.com/owner/repo and
    https://github.com/owner/repo.git formats.

    Returns (owner, repo) tuple.

    Raises ValueError if the URL cannot be parsed.
    """
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", repo_url)
    if not match:
        raise ValueError(f"Cannot parse GitHub repo URL: {repo_url}")
    return match.group(1), match.group(2)


async def search_github_duplicates(
    title: str,
    repo_url: str,
    github_token: str = "",
) -> list[dict]:
    """Search GitHub for similar issues by title keywords.

    Returns list of dicts with number, title, url, status keys.
    Swallows all errors and returns [].
    """
    try:
        owner, repo = _parse_github_repo_url(repo_url)
    except ValueError:
        logger.warning(
            "Could not parse GitHub repo URL for duplicate search: %s", repo_url
        )
        return []

    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    # Use meaningful words from title as search query
    query_words = title.split()[:8]
    query = " ".join(query_words)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.github.com/search/issues",
                params={
                    "q": f"{query} repo:{owner}/{repo} is:issue",
                    "per_page": 5,
                },
                headers=headers,
            )
            if resp.status_code != 200:
                logger.debug("GitHub search returned status %d", resp.status_code)
                return []
            data = resp.json()
            return [
                {
                    "number": item["number"],
                    "title": item["title"],
                    "url": item["html_url"],
                    "status": item.get("state", ""),
                }
                for item in data.get("items", [])[:5]
            ]
    except Exception:
        logger.debug("GitHub duplicate search failed", exc_info=True)
        return []


async def search_jira_duplicates(
    title: str,
    settings: Settings,
) -> list[dict]:
    """Search Jira for similar bug issues by title keywords.

    Reuses the existing JiraClient pattern for auth and API detection.
    Returns list of dicts with key, title, url, status keys.
    Swallows all errors and returns [].
    """
    if not settings.jira_enabled:
        return []

    try:
        from jenkins_job_insight.jira import JiraClient

        # Extract meaningful keywords from title
        query_words = title.split()[:8]
        keywords = [w for w in query_words if len(w) > 2]
        if not keywords:
            return []

        async with JiraClient(settings) as client:
            candidates = await client.search(keywords)
            return [
                {
                    "key": c["key"],
                    "title": c["summary"],
                    "url": c["url"],
                    "status": c.get("status", ""),
                }
                for c in candidates[:5]
            ]
    except Exception:
        logger.debug("Jira duplicate search failed", exc_info=True)
        return []


async def create_github_issue(
    title: str,
    body: str,
    repo_url: str,
    github_token: str,
    labels: list[str] | None = None,
) -> dict:
    """Create a GitHub issue via the REST API.

    Returns dict with url, number, and title keys.

    Raises httpx.HTTPStatusError on failure.
    """
    owner, repo = _parse_github_repo_url(repo_url)

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {github_token}",
    }
    payload: dict = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"https://api.github.com/repos/{owner}/{repo}/issues",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "url": data["html_url"],
            "number": data["number"],
            "title": data["title"],
        }


async def create_jira_bug(
    title: str,
    body: str,
    settings: Settings,
    priority: str = "",
) -> dict:
    """Create a Jira Bug issue via the REST API.

    Reuses the existing JiraClient auth pattern (Cloud vs Server/DC).
    Returns dict with key, url, and title keys.

    Raises httpx.HTTPStatusError on failure.
    """
    base_url = (settings.jira_url or "").rstrip("/")
    project_key = settings.jira_project_key or ""

    # Resolve auth (same logic as JiraClient)
    token_value = ""
    if settings.jira_api_token:
        token_value = settings.jira_api_token.get_secret_value()
    elif settings.jira_pat:
        token_value = settings.jira_pat.get_secret_value()

    auth: tuple[str, str] | None = None
    headers: dict[str, str] = {"Content-Type": "application/json"}

    if settings.jira_email and token_value:
        # Cloud: Basic auth
        auth = (settings.jira_email, token_value)
        api_path = "/rest/api/2"
    elif token_value:
        # Server/DC: Bearer PAT
        headers["Authorization"] = f"Bearer {token_value}"
        api_path = "/rest/api/2"
    else:
        api_path = "/rest/api/2"

    payload: dict = {
        "fields": {
            "project": {"key": project_key},
            "summary": title,
            "description": body,
            "issuetype": {"name": "Bug"},
        }
    }
    if priority:
        payload["fields"]["priority"] = {"name": priority}

    async with httpx.AsyncClient(
        verify=settings.jira_ssl_verify, timeout=15, auth=auth
    ) as client:
        resp = await client.post(
            f"{base_url}{api_path}/issue",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        issue_key = data["key"]
        return {
            "key": issue_key,
            "url": f"{base_url}/browse/{issue_key}",
            "title": title,
        }
