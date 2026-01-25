"""Output delivery for callbacks and Slack notifications."""

import httpx

from jenkins_job_insight.models import AnalysisResult, ChildJobAnalysis


async def send_callback(
    callback_url: str,
    result: AnalysisResult,
    headers: dict[str, str] | None = None,
) -> None:
    """Send analysis result to a callback webhook.

    Args:
        callback_url: URL to send the result to.
        result: Analysis result to deliver.
        headers: Optional headers to include in the request.
    """
    async with httpx.AsyncClient() as client:
        await client.post(
            callback_url,
            json=result.model_dump(mode="json"),
            headers=headers or {},
            timeout=30.0,
        )


def format_slack_message(result: AnalysisResult) -> dict:
    """Format analysis result as a Slack Block Kit message.

    Uses the same content as text output to ensure consistency.

    Args:
        result: Analysis result to format.

    Returns:
        Slack Block Kit message payload.
    """
    # Use the same text formatting as output=text
    text_content = format_result_as_text(result)

    # Split into chunks if needed (Slack has 3000 char limit per block)
    max_block_size = 2900  # Leave room for formatting

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Jenkins Analysis Complete"},
        },
    ]

    # Split text into chunks for Slack blocks
    chunks: list[str] = []
    current_chunk = ""
    for line in text_content.split("\n"):
        if len(current_chunk) + len(line) + 1 > max_block_size:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = line
        else:
            current_chunk = current_chunk + "\n" + line if current_chunk else line
    if current_chunk:
        chunks.append(current_chunk)

    # Add each chunk as a section block
    for chunk in chunks:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```{chunk}```"},
            }
        )

    return {"blocks": blocks}


async def send_slack(webhook_url: str, result: AnalysisResult) -> None:
    """Send analysis result to a Slack incoming webhook.

    Args:
        webhook_url: Slack webhook URL.
        result: Analysis result to send.
    """
    message = format_slack_message(result)
    async with httpx.AsyncClient() as client:
        await client.post(webhook_url, json=message, timeout=30.0)


def format_child_analysis_as_text(
    child: ChildJobAnalysis, indent: int = 0
) -> list[str]:
    """Format child job analysis as text lines.

    Args:
        child: Child job analysis to format.
        indent: Indentation level for nested children.

    Returns:
        List of formatted text lines.
    """
    prefix = "  " * indent
    lines = [
        f"{prefix}Job: {child.job_name} #{child.build_number}",
    ]
    if child.jenkins_url:
        lines.append(f"{prefix}URL: {child.jenkins_url}")
    if child.note:
        lines.append(f"{prefix}Note: {child.note}")
    if child.summary:
        lines.append(f"{prefix}Summary: {child.summary}")
    for f in child.failures:
        icon = "[BUG]" if f.classification == "product_bug" else "[CODE]"
        explanation_preview = f.explanation
        lines.append(f"{prefix}  {icon} {f.test_name}: {explanation_preview}")
    for nested in child.failed_children:
        lines.extend(format_child_analysis_as_text(nested, indent + 1))
    return lines


def format_result_as_text(result: AnalysisResult) -> str:
    """Format analysis result as human-readable text.

    Args:
        result: Analysis result to format.

    Returns:
        Human-readable text representation.
    """
    lines = [
        "=" * 60,
        "JENKINS JOB ANALYSIS",
        "=" * 60,
        f"Job URL: {result.jenkins_url}",
        f"Status: {result.status}",
        f"Job ID: {result.job_id}",
        "",
        "SUMMARY:",
        result.summary,
        "",
    ]

    if result.failures:
        lines.append("=" * 60)
        lines.append("FAILURES:")
        lines.append("=" * 60)

        for i, f in enumerate(result.failures, 1):
            icon = (
                "[PRODUCT BUG]" if f.classification == "product_bug" else "[CODE ISSUE]"
            )
            lines.extend(
                [
                    "",
                    f"[{i}] {icon}",
                    f"Test: {f.test_name}",
                    f"Error: {f.error}",
                    "",
                    "Explanation:",
                    f.explanation,
                ]
            )
            if f.fix_suggestion:
                lines.extend(["", "Fix Suggestion:", f.fix_suggestion])
            if f.bug_report:
                lines.extend(
                    [
                        "",
                        "Bug Report:",
                        f"  Title: {f.bug_report.title}",
                        f"  Severity: {f.bug_report.severity}",
                        f"  Component: {f.bug_report.component}",
                    ]
                )
            lines.append("-" * 40)

    if result.child_job_analyses:
        lines.append("")
        lines.append("=" * 60)
        lines.append("CHILD JOB ANALYSES:")
        lines.append("=" * 60)
        for child in result.child_job_analyses:
            lines.extend(format_child_analysis_as_text(child))

    return "\n".join(lines)
