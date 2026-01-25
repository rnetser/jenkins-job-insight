import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import NoReturn

import jenkins
from fastapi import HTTPException

from jenkins_job_insight.ai import AIClient, get_ai_client
from jenkins_job_insight.config import Settings
from jenkins_job_insight.jenkins import JenkinsClient
from jenkins_job_insight.models import (
    AnalysisResult,
    AnalyzeRequest,
    BugReport,
    ChildJobAnalysis,
    FailureAnalysis,
    TestFailure,
)
from jenkins_job_insight.repository import RepositoryManager

VALID_SEVERITIES = {"critical", "high", "medium", "low"}
VALID_CLASSIFICATIONS = {"code_issue", "product_bug"}
FALLBACK_TAIL_LINES = 200
MAX_CONCURRENT_AI_CALLS = 10

# Pre-compiled pattern for error detection with word boundaries
ERROR_PATTERN = re.compile(
    r"\b(error|fail(ed|ure)?|exception|traceback|assert(ion)?|warn(ing)?|critical|fatal)\b",
    re.IGNORECASE,
)


async def run_parallel_with_limit(
    coroutines: list,
    max_concurrency: int = MAX_CONCURRENT_AI_CALLS,
) -> list:
    """Run coroutines in parallel with bounded concurrency.

    Args:
        coroutines: List of coroutines to execute.
        max_concurrency: Maximum concurrent executions.

    Returns:
        List of results (including exceptions if any failed).
    """
    semaphore = asyncio.Semaphore(max_concurrency)

    async def bounded(coro):
        async with semaphore:
            return await coro

    return await asyncio.gather(
        *[bounded(c) for c in coroutines],
        return_exceptions=True,
    )


def extract_relevant_console_lines(console_output: str) -> str:
    """Extract only error, failure, and warning lines from console output.

    When no structured test report is available, we need to extract
    relevant information from the console without sending the entire log.

    Args:
        console_output: Full Jenkins console output.

    Returns:
        Extracted relevant lines (errors, failures, warnings, exceptions).
    """
    relevant_lines: list[str] = []
    lines = console_output.splitlines()

    # Track lines we've already added to avoid duplicates
    seen_indices: set[int] = set()
    in_traceback = False

    for i, line in enumerate(lines):
        # Check if line matches error pattern (word boundaries, case-insensitive)
        if ERROR_PATTERN.search(line):
            # Add some context: 2 lines before
            start = max(0, i - 2)
            for j in range(start, i):
                if j not in seen_indices:
                    relevant_lines.append(lines[j])
                    seen_indices.add(j)
            # Add the error line itself (with duplicate check)
            if i not in seen_indices:
                relevant_lines.append(line)
                seen_indices.add(i)
            in_traceback = True
        elif in_traceback:
            # Continue capturing indented lines (stack trace)
            if line.startswith((" ", "\t")) or line.strip() == "":
                if i not in seen_indices:
                    relevant_lines.append(line)
                    seen_indices.add(i)
            else:
                in_traceback = False

    if relevant_lines:
        return "\n".join(relevant_lines)

    # Fallback: if nothing found, return last N lines (likely has the failure info)
    return (
        "\n".join(lines[-FALLBACK_TAIL_LINES:])
        if len(lines) > FALLBACK_TAIL_LINES
        else console_output
    )


def find_test_file(repo_path: Path, test_class: str) -> str | None:
    """Find and read test file matching the test class name.

    Args:
        repo_path: Path to the cloned repository.
        test_class: Full test class name (e.g., tests.test_mtv_warm_migration.TestClass).

    Returns:
        The content of the test file if found, None otherwise.
    """
    # Convert class name to path: tests.test_mtv_warm_migration.TestClass -> tests/test_mtv_warm_migration.py
    parts = test_class.rsplit(".", 1)
    if len(parts) >= 1:
        module_path = parts[0].replace(".", "/") + ".py"
        test_file = repo_path / module_path
        if test_file.exists():
            return test_file.read_text()

    # Fallback: search for files containing the class name
    class_name = parts[-1] if parts else test_class
    for py_file in repo_path.rglob("*.py"):
        try:
            content = py_file.read_text()
            if test_class in content or class_name in content:
                return content
        except (OSError, UnicodeDecodeError):
            continue
    return None


def find_file_in_repo(repo_path: Path, filename: str) -> Path | None:
    """Find file in repository with priority search.

    Args:
        repo_path: Repository root path.
        filename: File name or path to find.

    Returns:
        Path to file if found, None otherwise.
    """
    # Normalize the filename
    filename = filename.replace("\\", "/")
    if filename.startswith("/"):
        filename = Path(filename).name
    while filename.startswith("./") or filename.startswith("../"):
        filename = filename.split("/", 1)[-1] if "/" in filename else filename

    # Try direct path first
    direct = repo_path / filename
    if direct.exists() and direct.is_file():
        try:
            direct.relative_to(repo_path)  # Security check
            return direct
        except ValueError:
            pass

    # Search in priority directories
    priority_dirs = ["tests", "test", "src", "utilities", "libs", "lib"]
    basename = Path(filename).name

    for priority_dir in priority_dirs:
        search_path = repo_path / priority_dir
        if search_path.exists():
            for found in search_path.rglob(basename):
                if found.is_file():
                    return found

    # Full repo search as fallback
    for found in repo_path.rglob(basename):
        if found.is_file():
            return found

    return None


def extract_files_from_failure_text(
    repo_path: Path,
    failure_text: str | None,
    max_files: int = 5,
) -> list[tuple[str, str]]:
    """Extract relevant files mentioned in failure text/stack trace.

    Based on testinsight-ai's _extract_relevant_repository_files.

    Args:
        repo_path: Path to cloned repository.
        failure_text: Error message, stack trace, or failure output.
        max_files: Maximum number of files to extract.

    Returns:
        List of (relative_path, content) tuples.
    """
    if not failure_text:
        failure_text = ""

    files: list[tuple[str, str]] = []
    seen_paths: set[str] = set()

    # Pattern 1: Python files in pytest/traceback format
    test_file_patterns = [
        r'File "([^"]+\.py)"',  # Python traceback: File "path/file.py", line X
        r"([\w\-/]+\.py)::",  # pytest: path/test-file.py::test_function
        r"([\w\-/]+\.py)",  # General Python files
    ]

    for pattern in test_file_patterns:
        matches = re.findall(pattern, failure_text)
        for match in matches:
            file_name = match if isinstance(match, str) else match[0]
            file_name = file_name.split("::")[0] if "::" in file_name else file_name

            # Try to find file in repo
            file_path = find_file_in_repo(repo_path, file_name)
            if file_path and file_path.exists():
                try:
                    content = file_path.read_text()
                    relative_path = str(file_path.relative_to(repo_path))
                    if relative_path not in seen_paths:
                        seen_paths.add(relative_path)
                        files.append((relative_path, content))
                except (OSError, UnicodeDecodeError):
                    continue

            if len(files) >= max_files:
                return files

    # Pattern 2: Any file path mentioned in the text
    if len(files) < max_files:
        path_pattern = r"([A-Za-z0-9_./\-]+\.(?:py|yaml|yml|json|sh))"
        candidates = re.findall(path_pattern, failure_text)
        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate:
                continue

            file_path = find_file_in_repo(repo_path, candidate)
            if file_path and file_path.exists():
                try:
                    content = file_path.read_text()
                    relative_path = str(file_path.relative_to(repo_path))
                    if relative_path not in seen_paths:
                        seen_paths.add(relative_path)
                        files.append((relative_path, content))
                except (OSError, UnicodeDecodeError):
                    continue

            if len(files) >= max_files:
                return files

    return files


def handle_jenkins_exception(
    e: Exception, job_name: str, build_number: int
) -> NoReturn:
    """Convert Jenkins exceptions to appropriate HTTPExceptions.

    Args:
        e: The exception raised by the Jenkins client.
        job_name: Name of the Jenkins job being accessed.
        build_number: Build number being accessed.

    Raises:
        HTTPException: With appropriate status code and detail message.
    """
    if isinstance(e, jenkins.NotFoundException):
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_name}' build #{build_number} not found in Jenkins",
        )

    if isinstance(e, jenkins.JenkinsException):
        error_msg = str(e).lower()
        if (
            "does not exist" in error_msg
            or "not found" in error_msg
            or "404" in error_msg
        ):
            raise HTTPException(
                status_code=404,
                detail=f"Job '{job_name}' build #{build_number} not found in Jenkins",
            )
        elif "unauthorized" in error_msg or "401" in error_msg:
            raise HTTPException(
                status_code=502,
                detail="Jenkins authentication failed. Check JENKINS_USER and JENKINS_PASSWORD.",
            )
        elif "forbidden" in error_msg or "403" in error_msg:
            raise HTTPException(
                status_code=502,
                detail=f"Access denied to job '{job_name}'. Check Jenkins permissions.",
            )
        else:
            raise HTTPException(
                status_code=502,
                detail=f"Jenkins error: {e!s}",
            )

    # For any other exception type
    raise HTTPException(
        status_code=502,
        detail=f"Failed to connect to Jenkins: {e!s}",
    )


ANALYSIS_PROMPT = """You are an expert at analyzing Jenkins CI/CD test failures.

CLASSIFICATION RULES - READ CAREFULLY:

code_issue = Problems in TEST CODE or TEST INFRASTRUCTURE:
  - Test fixtures failing (download failures, setup errors)
  - Setup/teardown failures
  - Wrong assertions in test code
  - Missing dependencies in test code
  - Environment/configuration issues in tests
  - Network/download failures during test setup
  - File not found errors in test utilities
  - ANY infrastructure issue that prevents tests from running

product_bug = ONLY when test correctly caught a bug in the PRODUCT BEING TESTED:
  - Product API returned wrong status code when test ran correctly
  - Product returned incorrect data
  - Product behavior differs from specification
  - The test executed but product output was wrong

DEFAULT: If unclear or cannot determine, classify as code_issue.
Product bugs require CLEAR EVIDENCE the product itself is broken, not test infrastructure.

For code_issue, provide fix_suggestion with:
  - Exact file path from the repository
  - Function/method to modify
  - Actual code change needed

For product_bug, provide bug_report with actionable details.

Respond in JSON format:
{
  "summary": "Brief overview",
  "failures": [
    {
      "test_name": "...",
      "error": "brief error description",
      "classification": "code_issue" or "product_bug",
      "explanation": "detailed explanation",
      "fix_suggestion": "file path, function, and code change" (if code_issue),
      "bug_report": {"title": "...", "description": "...", "severity": "...", "component": "...", "evidence": "..."} (if product_bug)
    }
  ]
}
"""


def load_analysis_prompt(prompt_file: str) -> str:
    """Load analysis prompt from file or use default.

    Args:
        prompt_file: Path to the custom prompt file.

    Returns:
        The custom prompt content if file exists, otherwise the default ANALYSIS_PROMPT.
    """
    prompt_path = Path(prompt_file)
    if prompt_path.exists():
        return prompt_path.read_text()
    return ANALYSIS_PROMPT


def extract_failed_child_jobs(build_info: dict) -> list[tuple[str, int]]:
    """Extract failed child job names and build numbers from pipeline build info.

    Looks for failed jobs in subBuilds (Pipeline plugin) and triggeredBuilds
    (older Jenkins plugins).

    Args:
        build_info: Jenkins build information dictionary.

    Returns:
        List of (job_name, build_number) tuples for failed child jobs.
    """
    failed_jobs: list[tuple[str, int]] = []

    # Check for subBuilds in pipeline (Blue Ocean / Pipeline plugin)
    sub_builds = build_info.get("subBuilds", [])
    for sub in sub_builds:
        if sub.get("result") in ("FAILURE", "UNSTABLE"):
            job_name = sub.get("jobName", "")
            build_num = sub.get("buildNumber", 0)
            if job_name and build_num:
                failed_jobs.append((job_name, build_num))

    # Also check actions for triggered builds (older Jenkins plugins)
    for action in build_info.get("actions", []):
        action_class = action.get("_class", "")
        triggered_builds = action.get("triggeredBuilds", [])

        # Check for BuildAction or similar action types
        if triggered_builds or "BuildAction" in action_class:
            for triggered in triggered_builds:
                if triggered.get("result") in ("FAILURE", "UNSTABLE"):
                    # Try to get job name from different possible fields
                    job_name = triggered.get("jobName", "")
                    if not job_name:
                        # Try to parse from URL if available
                        url = triggered.get("url", "")
                        if url:
                            try:
                                job_name, _ = JenkinsClient.parse_jenkins_url(url)
                            except ValueError:
                                continue
                    build_num = triggered.get("number", triggered.get("buildNumber", 0))
                    if job_name and build_num:
                        failed_jobs.append((job_name, build_num))

    return failed_jobs


def extract_failed_child_jobs_from_console(
    console_output: str,
) -> list[tuple[str, int]]:
    """Extract failed child jobs from console output using regex.

    Looks for patterns like:
    - Build folder » job-name #123 completed: FAILURE
    - Build job-name #123 completed: FAILURE

    Args:
        console_output: The Jenkins console output text.

    Returns:
        List of (job_name, build_number) tuples for failed child jobs.
    """
    failed_jobs: list[tuple[str, int]] = []

    # Pattern: Build [job path] #[number] completed: FAILURE/UNSTABLE
    pattern = r"Build\s+(.+?)\s+#(\d+)\s+completed:\s*(FAILURE|UNSTABLE)"
    matches = re.findall(pattern, console_output)

    for match in matches:
        job_path = match[0].strip()
        build_num = int(match[1])
        # Convert "folder » job" to "folder/job" format for Jenkins API
        # Example: "mtv-base » mtv-deploy-dynamic" -> "mtv-base/mtv-deploy-dynamic"
        # The URL construction will handle adding /job/ segments for display
        job_name = job_path.replace(" » ", "/")
        failed_jobs.append((job_name, build_num))

    return failed_jobs


def extract_failures_from_test_report(test_report: dict) -> list[TestFailure]:
    """Extract failed test cases from Jenkins test report.

    Parses the structured test report from Jenkins /testReport/api/json endpoint
    and extracts all failed and regression tests.

    Args:
        test_report: Jenkins test report dictionary from the API.

    Returns:
        List of TestFailure objects containing test details.
    """
    failures: list[TestFailure] = []

    # Handle both top-level suites and nested childReports structure
    suites = test_report.get("suites", [])

    # Some Jenkins configurations use childReports instead of suites at top level
    child_reports = test_report.get("childReports", [])
    for child_report in child_reports:
        result = child_report.get("result", {})
        suites.extend(result.get("suites", []))

    for suite in suites:
        for case in suite.get("cases", []):
            status = case.get("status", "")
            if status in ("FAILED", "REGRESSION"):
                class_name = case.get("className", "")
                test_name = case.get("name", "")
                full_name = f"{class_name}.{test_name}" if class_name else test_name

                failures.append(
                    TestFailure(
                        test_name=full_name,
                        error_message=case.get("errorDetails", "") or "",
                        stack_trace=case.get("errorStackTrace", "") or "",
                        duration=case.get("duration", 0.0) or 0.0,
                        status=status,
                    )
                )

    return failures


async def analyze_single_test_failure(
    failure: TestFailure,
    console_context: str,
    repo_path: Path | None,
    ai_client: AIClient,
    settings: Settings,
) -> FailureAnalysis:
    """Analyze a single test failure with dedicated AI context.

    Each test gets its own AI call with fresh context for better analysis.

    Args:
        failure: The test failure to analyze.
        console_context: Relevant console lines for context.
        repo_path: Path to cloned test repo (optional).
        ai_client: AI client for analysis.
        settings: Application settings.

    Returns:
        FailureAnalysis with classification and suggestions.
    """
    # Get source code for THIS specific test only
    test_code = ""
    if repo_path:
        test_class = failure.test_name.rsplit(".", 1)[0]
        source = await asyncio.to_thread(find_test_file, repo_path, test_class)
        if source:
            test_code = f"\n\nTEST SOURCE CODE:\n{source}"

    # Get source code from stack trace/error
    files_context = ""
    if repo_path:
        # Build failure text from all available info
        failure_text = (
            f"{failure.error_message}\n{failure.stack_trace}\n{console_context}"
        )
        extracted_files = await asyncio.to_thread(
            extract_files_from_failure_text, repo_path, failure_text, 3
        )

        if extracted_files:
            files_context = "\n\nRELEVANT SOURCE FILES:\n"
            for file_path, content in extracted_files:
                files_context += f"\n=== {file_path} ===\n{content}\n"

    # Build focused prompt for this single test
    analysis_prompt = load_analysis_prompt(settings.prompt_file)
    prompt = f"""Analyze this single test failure:

TEST: {failure.test_name}
STATUS: {failure.status}
ERROR: {failure.error_message}
STACK TRACE:
{failure.stack_trace}
{test_code}
{files_context}

RELEVANT CONSOLE CONTEXT:
{console_context}

CLASSIFICATION RULES:

code_issue = Problems in TEST CODE or TEST INFRASTRUCTURE:
  - Test fixtures failing (download failures, setup errors)
  - Setup/teardown failures
  - Wrong assertions in test code
  - Missing dependencies in test code
  - Environment/configuration issues in tests
  - Network/download failures during test setup
  - File not found errors in test utilities
  - ANY infrastructure issue that prevents tests from running

product_bug = ONLY when test correctly caught a bug in the PRODUCT BEING TESTED:
  - Product API returned wrong status code when test ran correctly
  - Product returned incorrect data
  - Product behavior differs from specification
  - The test executed but product output was wrong

DEFAULT: If unclear or cannot determine, classify as code_issue.

Respond in JSON format:
{{
    "test_name": "{failure.test_name}",
    "error": "brief error description",
    "classification": "code_issue" or "product_bug",
    "explanation": "detailed explanation",
    "fix_suggestion": "Provide a SPECIFIC fix with: (1) File path: exact path to the file, (2) Location: function/line to modify, (3) Change: the actual code change needed. Example: 'In utilities/virtctl.py, in the download_virtctl() function, add validation after the download: if os.path.getsize(tar_path) == 0: raise ValueError(\"Downloaded file is empty\")'." (only if code_issue, otherwise omit),
    "bug_report": {{"title": "...", "description": "...", "severity": "critical|high|medium|low", "component": "...", "evidence": "..."}} (only if product_bug, otherwise omit)
}}
"""

    try:
        response_text = await asyncio.to_thread(
            ai_client.analyze, prompt, system_prompt=analysis_prompt
        )
        response_data = parse_ai_response(response_text)

        # Build single failure from response
        # Wrap in failures array if AI returned a single object
        if "failures" not in response_data:
            response_data = {"failures": [response_data]}
        failures = build_failures_from_response(response_data)
        if failures:
            return failures[0]

        # Fallback if parsing failed
        return FailureAnalysis(
            test_name=failure.test_name,
            error=failure.error_message,
            classification="code_issue",
            explanation="AI analysis failed to parse response",
        )
    except Exception as e:
        return FailureAnalysis(
            test_name=failure.test_name,
            error=failure.error_message,
            classification="code_issue",
            explanation=f"AI analysis failed: {e}",
        )


def parse_ai_response(response_text: str) -> dict:
    """Parse AI response text into a dictionary.

    Attempts to parse JSON directly, then falls back to extracting JSON from text.

    Args:
        response_text: The AI response text, potentially containing JSON.

    Returns:
        Parsed dictionary with 'summary' and 'failures' keys.
    """
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return {"summary": "Failed to parse AI response", "failures": []}


def ensure_string(value: object) -> str:
    """Convert various types to string for Pydantic model compatibility.

    AI responses sometimes return dicts or lists where strings are expected.

    Args:
        value: The value to convert (string, dict, list, or None).

    Returns:
        A string representation of the value.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # Format dict as readable string
        parts = []
        for k, v in value.items():
            parts.append(f"{k}: {v}")
        return "\n".join(parts)
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value)


def build_failures_from_response(response_data: dict) -> list[FailureAnalysis]:
    """Build FailureAnalysis objects from parsed AI response.

    Args:
        response_data: Parsed AI response dictionary.

    Returns:
        List of FailureAnalysis objects.
    """
    failures = []
    for f in response_data.get("failures", []):
        bug_report = None
        if f.get("bug_report"):
            br = f["bug_report"]
            severity = br.get("severity", "medium")
            if severity not in VALID_SEVERITIES:
                severity = "medium"
            bug_report = BugReport(
                title=ensure_string(br.get("title", "")),
                description=ensure_string(br.get("description", "")),
                severity=severity,
                component=br.get("component", "Unknown"),
                evidence=ensure_string(br.get("evidence", "")),
            )

        classification = f.get("classification", "code_issue")
        if classification not in VALID_CLASSIFICATIONS:
            classification = "code_issue"

        fix_suggestion_value = f.get("fix_suggestion")
        failures.append(
            FailureAnalysis(
                test_name=f.get("test_name", "Unknown"),
                error=ensure_string(f.get("error", "")),
                classification=classification,
                explanation=ensure_string(f.get("explanation", "")),
                fix_suggestion=ensure_string(fix_suggestion_value)
                if fix_suggestion_value
                else None,
                bug_report=bug_report,
            )
        )
    return failures


async def analyze_child_job(
    job_name: str,
    build_number: int,
    jenkins_client: JenkinsClient,
    ai_client: AIClient,
    settings: Settings,
    depth: int = 0,
    max_depth: int = 3,
    repo_path: Path | None = None,
) -> ChildJobAnalysis:
    """Analyze a single child job, recursively analyzing its failed children.

    Each child job gets its own AI call to manage context size.

    Args:
        job_name: Name of the Jenkins job to analyze.
        build_number: Build number to analyze.
        jenkins_client: Jenkins API client.
        ai_client: AI client for analysis.
        settings: Application settings.
        depth: Current recursion depth (0 = direct child of main job).
        max_depth: Maximum recursion depth to prevent infinite loops.
        repo_path: Path to cloned test repository for source code lookup.

    Returns:
        ChildJobAnalysis with analysis results or nested child analyses.
    """
    # Construct Jenkins URL for this child job
    job_path = "/job/".join(job_name.split("/"))
    jenkins_url = f"{settings.jenkins_url.rstrip('/')}/job/{job_path}/{build_number}/"

    if depth >= max_depth:
        return ChildJobAnalysis(
            job_name=job_name,
            build_number=build_number,
            jenkins_url=jenkins_url,
            note="Max depth reached - analysis stopped to prevent infinite recursion",
        )

    # Get build info for this child
    try:
        build_info = await asyncio.to_thread(
            jenkins_client.get_build_info_safe, job_name, build_number
        )
    except Exception as e:
        return ChildJobAnalysis(
            job_name=job_name,
            build_number=build_number,
            jenkins_url=jenkins_url,
            note=f"Failed to get build info: {e}",
        )

    # Fetch console output first (needed for fallback child detection and analysis)
    try:
        console_output = await asyncio.to_thread(
            jenkins_client.get_build_console, job_name, build_number
        )
    except Exception as e:
        return ChildJobAnalysis(
            job_name=job_name,
            build_number=build_number,
            jenkins_url=jenkins_url,
            note=f"Failed to get console output: {e}",
        )

    # Check if this child also has failed children
    # Try to extract from build_info first
    failed_children = extract_failed_child_jobs(build_info)

    # Fallback to console parsing if none found from build_info
    if not failed_children:
        failed_children = extract_failed_child_jobs_from_console(console_output)

    if failed_children:
        # Recursively analyze failed children IN PARALLEL with bounded concurrency
        child_tasks = [
            analyze_child_job(
                child_name,
                child_num,
                jenkins_client,
                ai_client,
                settings,
                depth + 1,
                max_depth,
                repo_path,
            )
            for child_name, child_num in failed_children
        ]
        child_results = await run_parallel_with_limit(child_tasks)

        # Handle exceptions in results
        child_analyses = []
        for i, result in enumerate(child_results):
            if isinstance(result, Exception):
                child_name, child_num = failed_children[i]
                child_analyses.append(
                    ChildJobAnalysis(
                        job_name=child_name,
                        build_number=child_num,
                        jenkins_url="",
                        note=f"Analysis failed: {result}",
                    )
                )
            else:
                child_analyses.append(result)

        # This job failed because children failed - skip AI analysis
        # Count failures from child analyses
        code_issues = 0
        product_bugs = 0
        for child in child_analyses:
            for f in child.failures:
                if f.classification == "code_issue":
                    code_issues += 1
                else:
                    product_bugs += 1

        total_failures = code_issues + product_bugs
        summary = f"Pipeline failed due to {len(child_analyses)} child job(s)."
        if total_failures > 0:
            summary += f" Total: {total_failures} failure(s) - {code_issues} code issue(s), {product_bugs} product bug(s). See child analyses below."

        return ChildJobAnalysis(
            job_name=job_name,
            build_number=build_number,
            jenkins_url=jenkins_url,
            summary=summary,
            failures=[],  # Pipeline has no direct failures
            failed_children=child_analyses,
        )

    # No failed children - this is a leaf failure, analyze it directly

    # Try to get structured test report first (much cleaner than parsing console output)
    test_report = await asyncio.to_thread(
        jenkins_client.get_test_report, job_name, build_number
    )

    test_failures = (
        extract_failures_from_test_report(test_report) if test_report else []
    )

    # Extract relevant console lines for context
    console_context = extract_relevant_console_lines(console_output)

    # If we have test failures, analyze each in parallel with dedicated AI context
    if test_failures:
        tasks = [
            analyze_single_test_failure(
                failure=tf,
                console_context=console_context,
                repo_path=repo_path,
                ai_client=ai_client,
                settings=settings,
            )
            for tf in test_failures
        ]
        failure_results = await run_parallel_with_limit(tasks)

        # Handle exceptions in results
        failures = []
        for i, result in enumerate(failure_results):
            if isinstance(result, Exception):
                tf = test_failures[i]
                failures.append(
                    FailureAnalysis(
                        test_name=tf.test_name,
                        error=tf.error_message,
                        classification="code_issue",
                        explanation=f"Analysis failed: {result}",
                    )
                )
            else:
                failures.append(result)

        # Generate summary from parallel results
        code_issues = sum(1 for f in failures if f.classification == "code_issue")
        product_bugs = sum(1 for f in failures if f.classification == "product_bug")

        return ChildJobAnalysis(
            job_name=job_name,
            build_number=build_number,
            jenkins_url=jenkins_url,
            summary=f"Analyzed {len(failures)} test failure(s): {code_issues} code issue(s), {product_bugs} product bug(s)",
            failures=failures,
        )

    # No structured test failures - fall back to single AI analysis of console output
    analysis_prompt = load_analysis_prompt(settings.prompt_file)

    prompt = f"""Analyze this failed Jenkins job:

Job: {job_name} #{build_number}

CONSOLE OUTPUT (errors/failures/warnings extracted):
{console_context}

BUILD INFO:
{json.dumps(build_info, indent=2)}
"""
    try:
        response_text = await asyncio.to_thread(
            ai_client.analyze, prompt, system_prompt=analysis_prompt
        )
        response_data = parse_ai_response(response_text)
        failures = build_failures_from_response(response_data)

        return ChildJobAnalysis(
            job_name=job_name,
            build_number=build_number,
            jenkins_url=jenkins_url,
            summary=response_data.get("summary", "Analysis complete"),
            failures=failures,
        )
    except Exception as e:
        return ChildJobAnalysis(
            job_name=job_name,
            build_number=build_number,
            jenkins_url=jenkins_url,
            note=f"AI analysis failed: {e}",
        )


async def analyze_job(
    request: AnalyzeRequest, settings: Settings, job_id: str | None = None
) -> AnalysisResult:
    """Analyze a Jenkins job failure."""
    if job_id is None:
        job_id = str(uuid.uuid4())

    # Get Jenkins data
    jenkins_client = JenkinsClient(
        url=settings.jenkins_url,
        username=settings.jenkins_user,
        password=settings.jenkins_password,
        ssl_verify=settings.jenkins_ssl_verify,
    )

    # Use job_name and build_number directly from request
    job_name = request.job_name
    build_number = request.build_number

    # Construct full Jenkins URL for the result
    # Handle folder-style job names by replacing '/' with '/job/'
    job_path = "/job/".join(job_name.split("/"))
    jenkins_build_url = (
        f"{settings.jenkins_url.rstrip('/')}/job/{job_path}/{build_number}/"
    )

    # First get build_info (quick call) to check if build passed
    build_info: dict = {}
    try:
        build_info = await asyncio.to_thread(
            jenkins_client.get_build_info_safe, job_name, build_number
        )
    except Exception as e:
        handle_jenkins_exception(e, job_name, build_number)

    # Check if build passed - return early if yes
    build_result = build_info.get("result")
    if build_result == "SUCCESS":
        return AnalysisResult(
            job_id=job_id,
            jenkins_url=jenkins_build_url,
            status="completed",
            summary="Build passed successfully. No failures to analyze.",
            failures=[],
        )

    # Only fetch console output if build failed
    console_output: str = ""
    try:
        console_output = await asyncio.to_thread(
            jenkins_client.get_build_console, job_name, build_number
        )
    except Exception as e:
        handle_jenkins_exception(e, job_name, build_number)

    # Check for failed child jobs in pipeline
    # Try to extract from build_info first
    failed_child_jobs = extract_failed_child_jobs(build_info)

    # Fallback to console parsing if none found from build_info
    if not failed_child_jobs:
        failed_child_jobs = extract_failed_child_jobs_from_console(console_output)

    child_job_analyses: list[ChildJobAnalysis] = []

    # Create AI client for analysis (used for both main job and child jobs)
    ai_client = get_ai_client(settings)

    # Clone repo for context BEFORE child job analysis so it's available for all jobs
    # Use request value if provided, otherwise fall back to settings
    tests_repo_url = request.tests_repo_url or settings.tests_repo_url
    repo_context = ""
    repo_path: Path | None = None

    # Use RepositoryManager context for entire analysis (child jobs and main job)
    repo_manager = RepositoryManager() if tests_repo_url else None
    try:
        if repo_manager:
            repo_manager.__enter__()
            try:
                repo_path = await asyncio.to_thread(
                    repo_manager.clone, str(tests_repo_url)
                )
                repo_context = f"\nRepository cloned from: {tests_repo_url}"
            except Exception as e:
                repo_context = f"\nFailed to clone repo: {e}"

        # Analyze failed child jobs IN PARALLEL with bounded concurrency
        if failed_child_jobs:
            child_tasks = [
                analyze_child_job(
                    job_name=child_name,
                    build_number=child_num,
                    jenkins_client=jenkins_client,
                    ai_client=ai_client,
                    settings=settings,
                    depth=0,
                    max_depth=3,
                    repo_path=repo_path,
                )
                for child_name, child_num in failed_child_jobs
            ]
            child_results = await run_parallel_with_limit(child_tasks)

            # Handle exceptions in results
            for i, result in enumerate(child_results):
                if isinstance(result, Exception):
                    child_name, child_num = failed_child_jobs[i]
                    child_job_analyses.append(
                        ChildJobAnalysis(
                            job_name=child_name,
                            build_number=child_num,
                            jenkins_url="",
                            note=f"Analysis failed: {result}",
                        )
                    )
                else:
                    child_job_analyses.append(result)

        # Try to get structured test report first (much cleaner than parsing console output)
        test_report = await asyncio.to_thread(
            jenkins_client.get_test_report, job_name, build_number
        )

        test_failures = (
            extract_failures_from_test_report(test_report) if test_report else []
        )

        # If this job has failed children AND no test failures, it's a pipeline/orchestrator
        # Skip AI analysis - just return the child analyses
        if child_job_analyses and not test_failures:
            code_issues = 0
            product_bugs = 0
            for child in child_job_analyses:
                for f in child.failures:
                    if f.classification == "code_issue":
                        code_issues += 1
                    else:
                        product_bugs += 1

            total_failures = code_issues + product_bugs
            summary = f"Pipeline failed due to {len(child_job_analyses)} child job(s)."
            if total_failures > 0:
                summary += f" Total: {total_failures} failure(s) - {code_issues} code issue(s), {product_bugs} product bug(s). See child analyses below."

            return AnalysisResult(
                job_id=job_id,
                jenkins_url=jenkins_build_url,
                status="completed",
                summary=summary,
                failures=[],  # Pipeline has no direct failures
                child_job_analyses=child_job_analyses,
            )

        # Extract relevant console lines for context
        console_context = extract_relevant_console_lines(console_output)

        # Analyze main job test failures IN PARALLEL with bounded concurrency
        if test_failures:
            failure_tasks = [
                analyze_single_test_failure(
                    failure=tf,
                    console_context=console_context,
                    repo_path=repo_path,
                    ai_client=ai_client,
                    settings=settings,
                )
                for tf in test_failures
            ]
            failure_results = await run_parallel_with_limit(failure_tasks)

            # Handle exceptions in results
            failures = []
            for i, result in enumerate(failure_results):
                if isinstance(result, Exception):
                    tf = test_failures[i]
                    failures.append(
                        FailureAnalysis(
                            test_name=tf.test_name,
                            error=tf.error_message,
                            classification="code_issue",
                            explanation=f"Analysis failed: {result}",
                        )
                    )
                else:
                    failures.append(result)
        else:
            # No structured test failures - fall back to single AI analysis of console output
            analysis_prompt = load_analysis_prompt(settings.prompt_file)

            prompt = f"""Analyze this failed Jenkins job:

Job: {job_name} #{build_number}

CONSOLE OUTPUT (errors/failures/warnings extracted):
{console_context}

BUILD INFO:
{json.dumps(build_info, indent=2)}
{repo_context}
"""

            response_text = await asyncio.to_thread(
                ai_client.analyze, prompt, system_prompt=analysis_prompt
            )

            # Parse response and build failures using helper functions
            response_data = parse_ai_response(response_text)
            failures = build_failures_from_response(response_data)

        # Build summary from parallel results
        code_issues = sum(1 for f in failures if f.classification == "code_issue")
        product_bugs = sum(1 for f in failures if f.classification == "product_bug")
        summary = f"{len(failures)} failure(s) analyzed: {code_issues} code issue(s), {product_bugs} product bug(s)"

        if child_job_analyses:
            summary = (
                f"{summary}. Additionally, {len(child_job_analyses)} failed child "
                f"job(s) were analyzed recursively."
            )

        return AnalysisResult(
            job_id=job_id,
            jenkins_url=jenkins_build_url,
            status="completed",
            summary=summary,
            failures=failures,
            child_job_analyses=child_job_analyses,
        )
    finally:
        if repo_manager:
            repo_manager.__exit__(None, None, None)
