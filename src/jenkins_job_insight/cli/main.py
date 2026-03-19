"""jji -- CLI tool for the jenkins-job-insight REST API."""

import typer

from jenkins_job_insight.cli.client import JJIClient, JJIError
from jenkins_job_insight.cli.output import print_output

# -- App and sub-command groups -----------------------------------------------

app = typer.Typer(
    name="jji",
    help="CLI for the jenkins-job-insight REST API.",
    no_args_is_help=True,
)

results_app = typer.Typer(help="Manage analysis results.", no_args_is_help=True)
history_app = typer.Typer(help="Query failure history.", no_args_is_help=True)
comments_app = typer.Typer(
    help="Manage comments on test failures.", no_args_is_help=True
)
classifications_app = typer.Typer(
    help="List test classifications.", no_args_is_help=True
)

app.add_typer(results_app, name="results")
app.add_typer(history_app, name="history")
app.add_typer(comments_app, name="comments")
app.add_typer(classifications_app, name="classifications")

# -- Global state managed via callback ---------------------------------------

_state: dict = {}

# Shared option definition reused across leaf commands so --json works
# both globally (before the subcommand) and per-command (after it).
_JSON_OPTION = typer.Option(False, "--json", help="Output as JSON instead of table.")


def _set_json(json_output: bool) -> None:
    """Set JSON mode from a per-command --json flag."""
    if json_output:
        _state["json"] = True


def _get_client(server_url: str = "", username: str = "") -> JJIClient:
    """Build (or return cached) JJIClient from global state."""
    url = server_url or _state.get("server_url", "")
    uname = username or _state.get("username", "")
    return JJIClient(server_url=url, username=uname)


def _handle_error(err: JJIError) -> None:
    """Print a JJIError and exit with code 1."""
    typer.echo(f"Error: {err}", err=True)
    if err.status_code == 401:
        typer.echo(
            "Hint: Use --user <name> or set JJI_USERNAME to authenticate.",
            err=True,
        )
    raise typer.Exit(code=1)


@app.callback()
def main_callback(
    server: str = typer.Option(
        None,
        "--server",
        "-s",
        envvar="JJI_SERVER_URL",
        help="Server URL (required: set JJI_SERVER_URL or use --server).",
    ),
    port: int = typer.Option(
        None,
        "--port",
        "-p",
        envvar="JJI_PORT",
        help="Server port (appended to --server URL if provided).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON instead of table.",
    ),
    username: str = typer.Option(
        "",
        "--user",
        envvar="JJI_USERNAME",
        help="Username for authenticated actions.",
    ),
):
    """jji -- CLI for the jenkins-job-insight REST API."""
    if not server:
        typer.echo(
            "Error: Server URL not configured. Set JJI_SERVER_URL or use --server.",
            err=True,
        )
        raise typer.Exit(1)

    if port:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(server)
        server = urlunparse(parsed._replace(netloc=f"{parsed.hostname}:{port}"))

    _state["server_url"] = server
    _state["username"] = username
    _state["json"] = json_output


# -- Health -------------------------------------------------------------------


@app.command()
def health(
    json_output: bool = _JSON_OPTION,
):
    """Check server health."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.health()
    except JJIError as err:
        _handle_error(err)
    print_output(data, columns=["status"], as_json=_state.get("json", False))


# -- Results ------------------------------------------------------------------


@results_app.command("list")
def results_list(
    limit: int = typer.Option(50, "--limit", "-l", help="Max results to return."),
    json_output: bool = _JSON_OPTION,
):
    """List recent analyzed jobs."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.list_results(limit=limit)
    except JJIError as err:
        _handle_error(err)
    print_output(
        data,
        columns=["job_id", "status", "jenkins_url", "created_at"],
        labels={
            "job_id": "JOB ID",
            "jenkins_url": "JENKINS URL",
            "created_at": "CREATED",
        },
        as_json=_state.get("json", False),
    )


@results_app.command("show")
def results_show(
    job_id: str = typer.Argument(help="Job ID to show."),
    full: bool = typer.Option(False, "--full", "-f", help="Show complete JSON result."),
    json_output: bool = _JSON_OPTION,
):
    """Show analysis result for a job."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.get_result(job_id)
    except JJIError as err:
        _handle_error(err)

    if full or _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        summary = {
            "job_id": data.get("job_id", ""),
            "status": data.get("status", ""),
            "jenkins_url": data.get("jenkins_url", ""),
            "created_at": data.get("created_at", ""),
        }
        result = data.get("result")
        if isinstance(result, dict):
            summary["summary"] = result.get("summary", "")
            summary["ai_provider"] = result.get("ai_provider", "")
            summary["failures"] = len(result.get("failures", []))
            summary["children"] = len(result.get("child_job_analyses", []))
        print_output(
            summary,
            columns=[
                "job_id",
                "status",
                "summary",
                "failures",
                "children",
                "ai_provider",
                "created_at",
            ],
            labels={"ai_provider": "AI PROVIDER", "created_at": "CREATED"},
            as_json=False,
        )


@results_app.command("delete")
def results_delete(
    job_id: str = typer.Argument(help="Job ID to delete."),
    json_output: bool = _JSON_OPTION,
):
    """Delete a job and all related data."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.delete_job(job_id)
    except JJIError as err:
        _handle_error(err)
    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        typer.echo(f"Deleted job {data.get('job_id', job_id)}")


# -- Analyze ------------------------------------------------------------------


@app.command()
def analyze(
    job_name: str = typer.Argument(help="Jenkins job name."),
    build_number: int = typer.Argument(help="Build number to analyze."),
    sync: bool = typer.Option(False, "--sync", help="Wait for analysis to complete."),
    provider: str = typer.Option(
        "", "--provider", help="AI provider (e.g. claude, gemini, cursor)."
    ),
    model: str = typer.Option("", "--model", help="AI model to use."),
    jira: bool = typer.Option(
        False, "--jira", help="Enable Jira integration for this analysis."
    ),
    json_output: bool = _JSON_OPTION,
):
    """Submit a Jenkins job for analysis."""
    _set_json(json_output)
    extras: dict = {}
    if provider:
        extras["ai_provider"] = provider
    if model:
        extras["ai_model"] = model
    if jira:
        extras["enable_jira"] = True
    try:
        client = _get_client()
        data = client.analyze(job_name, build_number, sync=sync, **extras)
    except JJIError as err:
        _handle_error(err)

    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    elif sync:
        print_output(
            data,
            columns=["job_id", "status", "summary"],
            as_json=False,
        )
    else:
        typer.echo(f"Job queued: {data.get('job_id', '')}")
        typer.echo(f"Status: {data.get('status', '')}")
        typer.echo(f"Poll: {data.get('result_url', '')}")


# -- Status -------------------------------------------------------------------


@app.command()
def status(
    job_id: str = typer.Argument(help="Job ID to check."),
    json_output: bool = _JSON_OPTION,
):
    """Check analysis status for a job."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.get_result(job_id)
    except JJIError as err:
        _handle_error(err)
    if _state.get("json"):
        print_output(data, columns=[], as_json=True)
    else:
        print_output(
            {"job_id": data.get("job_id", ""), "status": data.get("status", "")},
            columns=["job_id", "status"],
            as_json=False,
        )


# -- History ------------------------------------------------------------------


@history_app.command("test")
def history_test(
    test_name: str = typer.Argument(help="Fully qualified test name."),
    limit: int = typer.Option(20, "--limit", "-l"),
    job_name: str = typer.Option("", "--job-name", "-j"),
    json_output: bool = _JSON_OPTION,
):
    """Show failure history for a specific test."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.get_test_history(test_name, limit=limit, job_name=job_name)
    except JJIError as err:
        _handle_error(err)

    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        failure_rate = data.get("failure_rate")
        rate_str = f"{failure_rate:.1%}" if failure_rate is not None else "N/A"
        typer.echo(f"Test: {data.get('test_name', '')}")
        typer.echo(f"Failures: {data.get('failures', 0)}")
        typer.echo(f"Failure rate: {rate_str}")
        typer.echo(f"Last classification: {data.get('last_classification', 'N/A')}")
        runs = data.get("recent_runs", [])
        if runs:
            typer.echo("\nRecent runs:")
            print_output(
                runs,
                columns=["job_name", "build_number", "classification", "analyzed_at"],
                labels={"build_number": "BUILD", "analyzed_at": "DATE"},
                as_json=False,
            )
        comments = data.get("comments", [])
        if comments:
            typer.echo("\nComments:")
            print_output(
                comments,
                columns=["username", "comment", "created_at"],
                labels={"created_at": "DATE"},
                as_json=False,
            )


@history_app.command("search")
def history_search(
    signature: str = typer.Option(
        ..., "--signature", "-s", help="Error signature hash."
    ),
    json_output: bool = _JSON_OPTION,
):
    """Find tests that failed with the same error signature."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.search_by_signature(signature)
    except JJIError as err:
        _handle_error(err)

    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        typer.echo(f"Signature: {data.get('signature', '')}")
        typer.echo(f"Total occurrences: {data.get('total_occurrences', 0)}")
        typer.echo(f"Unique tests: {data.get('unique_tests', 0)}")
        tests = data.get("tests", [])
        if tests:
            print_output(
                tests,
                columns=["test_name", "occurrences"],
                as_json=False,
            )


@history_app.command("stats")
def history_stats(
    job_name: str = typer.Argument(help="Jenkins job name."),
    json_output: bool = _JSON_OPTION,
):
    """Show aggregate statistics for a job."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.get_job_stats(job_name)
    except JJIError as err:
        _handle_error(err)

    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        overall_rate = data.get("overall_failure_rate")
        overall_rate_str = f"{overall_rate:.1%}" if overall_rate is not None else "N/A"
        typer.echo(f"Job: {data.get('job_name', '')}")
        typer.echo(f"Builds analyzed: {data.get('total_builds_analyzed', 0)}")
        typer.echo(f"Builds with failures: {data.get('builds_with_failures', 0)}")
        typer.echo(f"Failure rate: {overall_rate_str}")
        failures = data.get("most_common_failures", [])
        if failures:
            typer.echo("\nMost common failures:")
            print_output(
                failures,
                columns=["test_name", "count", "classification"],
                as_json=False,
            )


@history_app.command("trends")
def history_trends(
    period: str = typer.Option(
        "daily", "--period", "-p", help="Aggregation period: daily or weekly."
    ),
    days: int = typer.Option(30, "--days", "-d", help="Lookback window in days."),
    job_name: str = typer.Option("", "--job-name", "-j"),
    json_output: bool = _JSON_OPTION,
):
    """Show failure rate trends over time."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.get_trends(period=period, days=days, job_name=job_name)
    except JJIError as err:
        _handle_error(err)

    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        trend_data = data.get("data", [])
        if trend_data:
            print_output(
                trend_data,
                columns=["date", "failures", "unique_tests"],
                labels={"unique_tests": "UNIQUE TESTS"},
                as_json=False,
            )
        else:
            typer.echo("No trend data available.")


@history_app.command("failures")
def history_failures(
    limit: int = typer.Option(50, "--limit", "-l"),
    offset: int = typer.Option(0, "--offset", "-o"),
    search: str = typer.Option("", "--search", "-s", help="Search test names."),
    classification: str = typer.Option("", "--classification", "-c"),
    job_name: str = typer.Option("", "--job-name", "-j"),
    json_output: bool = _JSON_OPTION,
):
    """List paginated failure history."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.get_all_failures(
            search=search,
            job_name=job_name,
            classification=classification,
            limit=limit,
            offset=offset,
        )
    except JJIError as err:
        _handle_error(err)

    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        failures = data.get("failures", [])
        typer.echo(
            f"Total: {data.get('total', 0)} (showing {len(failures)}, offset {data.get('offset', 0)})"
        )
        if failures:
            print_output(
                failures,
                columns=["test_name", "job_name", "classification", "analyzed_at"],
                labels={"analyzed_at": "DATE"},
                as_json=False,
            )


# -- Classify -----------------------------------------------------------------


@app.command()
def classify(
    test_name: str = typer.Argument(help="Fully qualified test name."),
    classification: str = typer.Option(
        ...,
        "--type",
        "-t",
        help="FLAKY, REGRESSION, INFRASTRUCTURE, KNOWN_BUG, or INTERMITTENT.",
    ),
    job_id: str = typer.Option(
        ..., "--job-id", help="Job ID this classification applies to."
    ),
    reason: str = typer.Option("", "--reason", "-r", help="Reason for classification."),
    job_name: str = typer.Option("", "--job-name", "-j"),
    references: str = typer.Option("", "--references", help="Bug URLs or ticket keys."),
    child_job: str = typer.Option("", "--child-job", help="Child job name."),
    child_build: int = typer.Option(0, "--child-build", help="Child build number."),
    json_output: bool = _JSON_OPTION,
):
    """Classify a test failure."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.classify_test(
            test_name=test_name,
            classification=classification.upper(),
            job_id=job_id,
            reason=reason,
            job_name=child_job if child_job else job_name,
            references=references,
            child_build_number=child_build,
        )
    except JJIError as err:
        _handle_error(err)
    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        typer.echo(f"Classification created (id: {data.get('id', '')})")


# -- Classifications ---------------------------------------------------------


@classifications_app.command("list")
def classifications_list(
    job_id: str = typer.Option("", "--job-id"),
    test_name: str = typer.Option("", "--test-name", "-t"),
    classification: str = typer.Option("", "--type", "-c"),
    job_name: str = typer.Option("", "--job-name", "-j"),
    json_output: bool = _JSON_OPTION,
):
    """List test classifications."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.get_classifications(
            test_name=test_name,
            classification=classification.upper() if classification else "",
            job_name=job_name,
            job_id=job_id,
        )
    except JJIError as err:
        _handle_error(err)

    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        items = data.get("classifications", [])
        if items:
            print_output(
                items,
                columns=[
                    "test_name",
                    "classification",
                    "reason",
                    "created_by",
                    "created_at",
                ],
                labels={"created_by": "BY", "created_at": "DATE"},
                as_json=False,
            )
        else:
            typer.echo("No classifications found.")


# -- Comments -----------------------------------------------------------------


@comments_app.command("list")
def comments_list(
    job_id: str = typer.Argument(help="Job ID to list comments for."),
    json_output: bool = _JSON_OPTION,
):
    """List comments for a job."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.get_comments(job_id)
    except JJIError as err:
        _handle_error(err)

    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        items = data.get("comments", [])
        if items:
            print_output(
                items,
                columns=["id", "test_name", "comment", "username", "created_at"],
                labels={"created_at": "DATE"},
                as_json=False,
            )
        else:
            typer.echo("No comments for this job.")


@comments_app.command("add")
def comments_add(
    job_id: str = typer.Argument(help="Job ID."),
    test_name: str = typer.Option(..., "--test", "-t", help="Test name to comment on."),
    message: str = typer.Option(..., "--message", "-m", help="Comment text."),
    child_job_name: str = typer.Option("", "--child-job"),
    child_build_number: int = typer.Option(0, "--child-build"),
    json_output: bool = _JSON_OPTION,
):
    """Add a comment to a test failure."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.add_comment(
            job_id=job_id,
            test_name=test_name,
            comment=message,
            child_job_name=child_job_name,
            child_build_number=child_build_number,
        )
    except JJIError as err:
        _handle_error(err)
    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        typer.echo(f"Comment added (id: {data.get('id', '')})")


@comments_app.command("delete")
def comments_delete(
    job_id: str = typer.Argument(help="Job ID."),
    comment_id: int = typer.Argument(help="Comment ID to delete."),
    json_output: bool = _JSON_OPTION,
):
    """Delete a comment."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.delete_comment(job_id, comment_id)
    except JJIError as err:
        _handle_error(err)
    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        typer.echo("Comment deleted.")


# -- Bug Creation -------------------------------------------------------------


def _validate_issue_type(issue_type: str) -> str:
    """Validate and normalize issue type, exit on invalid input."""
    normalized = issue_type.lower()
    if normalized not in ("github", "jira"):
        typer.echo(
            f"Error: --type must be 'github' or 'jira', got '{issue_type}'",
            err=True,
        )
        raise typer.Exit(code=1)
    return normalized


@app.command("preview-issue")
def preview_issue(
    job_id: str = typer.Argument(help="Job ID."),
    test_name: str = typer.Option(..., "--test", "-t", help="Test name."),
    issue_type: str = typer.Option(..., "--type", help="github or jira."),
    child_job_name: str = typer.Option("", "--child-job"),
    child_build_number: int = typer.Option(0, "--child-build"),
    json_output: bool = _JSON_OPTION,
):
    """Preview generated issue content (GitHub or Jira)."""
    _set_json(json_output)
    normalized_type = _validate_issue_type(issue_type)
    try:
        client = _get_client()
        if normalized_type == "github":
            data = client.preview_github_issue(
                job_id=job_id,
                test_name=test_name,
                child_job_name=child_job_name,
                child_build_number=child_build_number,
            )
        else:
            data = client.preview_jira_bug(
                job_id=job_id,
                test_name=test_name,
                child_job_name=child_job_name,
                child_build_number=child_build_number,
            )
    except JJIError as err:
        _handle_error(err)

    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        typer.echo(f"Title: {data.get('title', '')}")
        typer.echo(f"\nBody:\n{data.get('body', '')}")
        similar = data.get("similar_issues", [])
        if similar:
            typer.echo(f"\nSimilar issues ({len(similar)}):")
            for s in similar:
                label = s.get("key") or f"#{s.get('number', '')}"
                typer.echo(f"  {label}: {s.get('title', '')} ({s.get('url', '')})")


@app.command("create-issue")
def create_issue(
    job_id: str = typer.Argument(help="Job ID."),
    test_name: str = typer.Option(..., "--test", "-t", help="Test name."),
    issue_type: str = typer.Option(..., "--type", help="github or jira."),
    title: str = typer.Option(..., "--title", help="Issue title."),
    body: str = typer.Option(..., "--body", help="Issue body."),
    child_job_name: str = typer.Option("", "--child-job"),
    child_build_number: int = typer.Option(0, "--child-build"),
    json_output: bool = _JSON_OPTION,
):
    """Create a GitHub issue or Jira bug from a failure analysis."""
    _set_json(json_output)
    normalized_type = _validate_issue_type(issue_type)
    try:
        client = _get_client()
        if normalized_type == "github":
            data = client.create_github_issue(
                job_id=job_id,
                test_name=test_name,
                title=title,
                body=body,
                child_job_name=child_job_name,
                child_build_number=child_build_number,
            )
        else:
            data = client.create_jira_bug(
                job_id=job_id,
                test_name=test_name,
                title=title,
                body=body,
                child_job_name=child_job_name,
                child_build_number=child_build_number,
            )
    except JJIError as err:
        _handle_error(err)

    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        key_or_number = data.get("key") or f"#{data.get('number', '')}"
        typer.echo(f"Created: {key_or_number}")
        typer.echo(f"URL: {data.get('url', '')}")
        if data.get("comment_id"):
            typer.echo(f"Comment added (id: {data['comment_id']})")


@app.command("override-classification")
def override_classification_cmd(
    job_id: str = typer.Argument(help="Job ID."),
    test_name: str = typer.Option(..., "--test", "-t", help="Test name."),
    classification: str = typer.Option(
        ..., "--classification", "-c", help="CODE ISSUE or PRODUCT BUG."
    ),
    child_job_name: str = typer.Option("", "--child-job"),
    child_build_number: int = typer.Option(0, "--child-build"),
    json_output: bool = _JSON_OPTION,
):
    """Override the classification of a failure."""
    _set_json(json_output)
    try:
        client = _get_client()
        data = client.override_classification(
            job_id=job_id,
            test_name=test_name,
            classification=classification,
            child_job_name=child_job_name,
            child_build_number=child_build_number,
        )
    except JJIError as err:
        _handle_error(err)

    if _state.get("json", False):
        print_output(data, columns=[], as_json=True)
    else:
        typer.echo(f"Classification overridden to: {data.get('classification', '')}")
