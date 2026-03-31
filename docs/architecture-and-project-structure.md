# Architecture and Project Structure

`jenkins-job-insight` is a single application that combines a FastAPI backend, a React web UI, a REST-backed CLI, and a SQLite persistence layer. A typical run starts with a Jenkins build or raw JUnit XML, passes through the analyzer and optional tracker automation, and ends up as a stored result that powers the HTML report, history views, comments, reviews, and CLI output.

## Repository map

- `src/jenkins_job_insight/main.py`: FastAPI app, startup lifecycle, API routes, SPA hosting, status polling, comments/reviews, history, and issue preview/create endpoints.
- `src/jenkins_job_insight/models.py`: request and response models such as `AnalyzeRequest`, `AnalyzeFailuresRequest`, `FailureAnalysis`, `ChildJobAnalysis`, and `AnalysisResult`.
- `src/jenkins_job_insight/analyzer.py`: the core orchestration layer that talks to Jenkins, groups failures, builds AI prompts, and assembles final analysis results.
- `src/jenkins_job_insight/jenkins.py`: thin `python-jenkins` wrapper with helper methods for build info, console output, test reports, and URL parsing.
- `src/jenkins_job_insight/jenkins_artifacts.py`: artifact download, safe archive extraction, and artifact-context generation for AI analysis.
- `src/jenkins_job_insight/jira.py`: Jira candidate search plus AI-based relevance filtering for `PRODUCT BUG` matches.
- `src/jenkins_job_insight/bug_creation.py`: preview, duplicate lookup, and creation helpers for GitHub issues and Jira bugs.
- `src/jenkins_job_insight/comment_enrichment.py`: detects GitHub/Jira references in comments and fetches live status information.
- `src/jenkins_job_insight/storage.py`: SQLite schema, migrations, result persistence, failure history, comments, reviews, and classification overrides.
- `src/jenkins_job_insight/encryption.py`: encryption-at-rest for stored sensitive request parameters.
- `src/jenkins_job_insight/repository.py`: temporary clone management for the optional tests repository context.
- `src/jenkins_job_insight/cli/`: the `jji` CLI, including HTTP client, config loader, and terminal output formatting.
- `frontend/src/`: React + TypeScript UI for dashboard, live status, HTML report, failure history, and test history pages.
- `examples/pytest-junitxml/`: a drop-in pytest/JUnit example that sends failed test XML to JJI and writes enriched XML back.
- `tests/`: backend, storage, Jira, bug-creation, Jenkins, and CLI tests.
- `tox.toml`, `.pre-commit-config.yaml`, `Dockerfile`, `docker-compose.yaml`, and `entrypoint.sh`: local validation and runtime packaging.

## End-to-end flow

1. A client submits a Jenkins build to `POST /analyze`, or raw failures/XML to `POST /analyze-failures`.
2. `main.py` creates a `job_id`, saves an initial SQLite row, and either queues background work or runs direct analysis immediately.
3. `analyzer.py` pulls Jenkins build data, structured test reports, console logs, and optional build artifacts. If `tests_repo_url` is configured, it also clones the test repository into a temporary workspace so the AI can inspect real code.
4. Failures are grouped by signature so repeated failures only produce one AI call per unique root cause.
5. The final result is enriched with Jira matches when the failure is classified as a product bug.
6. `storage.py` persists the result, flattens failures into history tables, and exposes the same data to the React UI, REST API, and CLI.

The asynchronous Jenkins entry point is deliberately queue-oriented: it creates the record first, then lets the client poll by `job_id`.

```921:976:src/jenkins_job_insight/main.py
@app.post("/analyze", status_code=202, response_model=None)
async def analyze(
    request: Request,
    body: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    *,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Submit a Jenkins job for analysis.

    Returns immediately with a job_id. Poll /results/{job_id} for status.
    """
    logger.debug(f"Starting analysis for {body.job_name} #{body.build_number}")
    base_url = _extract_base_url()

    # Validate AI config early -- fail fast before queuing invalid jobs.
    _resolve_ai_config(body)

    # Generate job_id here so we can return it to the client for polling
    job_id = str(uuid.uuid4())
    merged = _merge_settings(body, settings)
    jenkins_url = build_jenkins_url(
        merged.jenkins_url, body.job_name, body.build_number
    )
    # Save initial pending state before queueing background task.
    # Only persist request_params for waiting jobs (wait_for_completion),
    # since those are the only ones that need resumption after restart.
    # This avoids storing encrypted secrets with no operational use.
    initial_result: dict = {
        "job_name": body.job_name,
        "build_number": body.build_number,
    }
    can_resume_wait = merged.wait_for_completion and bool(merged.jenkins_url)
    if can_resume_wait:
        initial_result["request_params"] = _build_request_params(
            body,
            merged,
            body.ai_provider or AI_PROVIDER,
            body.ai_model or AI_MODEL,
        )
    await save_result(
        job_id,
        jenkins_url,
        "waiting" if can_resume_wait else "pending",
        initial_result,
    )
    background_tasks.add_task(process_analysis_with_id, job_id, body, merged)
    message = f"Analysis job queued. Poll /results/{job_id} for status."

    response: dict = {
        "status": "queued",
        "job_id": job_id,
        "message": message,
    }

    return _attach_result_links(response, base_url, job_id)
```

The analyzer’s most important scaling feature is failure deduplication. It uses one representative failure to build the prompt, then applies the same structured result to every test in the signature group.

```872:947:src/jenkins_job_insight/analyzer.py
# Use the first failure as representative
representative = failures[0]
error_signature = get_failure_signature(representative)
test_names = [f.test_name for f in failures]

custom_prompt_section, artifacts_section, resources_section, query_section = (
    _build_prompt_sections(
        custom_prompt, artifacts_context, repo_path, server_url, job_id
    )
)

has_git_repo = bool(repo_path and (repo_path / ".git").exists())
repo_sentence = (
    "You have access to the test repository. Explore the code to understand the failure."
    if has_git_repo
    else "No test repository is available. Base your analysis on the console output and artifacts context provided."
)

prompt = f"""{query_section}
Analyze this test failure from a Jenkins CI job.

ERROR SIGNATURE: {error_signature}

AFFECTED TESTS ({len(failures)} tests with same error):
{chr(10).join(f"- {name}" for name in test_names)}

ERROR: {representative.error_message}
STACK TRACE:
{representative.stack_trace}

CONSOLE CONTEXT:
{console_context}
{artifacts_section}

{repo_sentence}

Note: Multiple tests failed with the same error. Provide ONE analysis that applies to all of them.
{custom_prompt_section}{resources_section}
{_JSON_RESPONSE_SCHEMA}
"""

if artifacts_context:
    logger.info(
        f"Prompt includes Jenkins artifacts context ({len(artifacts_context)} chars)"
    )

logger.debug(f"AI prompt length: {len(prompt)} chars")
logger.info(
    f"Calling {ai_provider.upper()} CLI for failure group ({len(failures)} tests with same error)"
)
logger.info(f"Calling AI CLI with {_format_timeout_log(ai_cli_timeout)}")
success, analysis_output = await _call_ai_cli_with_retry(
    prompt,
    cwd=repo_path,
    ai_provider=ai_provider,
    ai_model=ai_model,
    ai_cli_timeout=ai_cli_timeout,
    cli_flags=PROVIDER_CLI_FLAGS.get(ai_provider, []),
)

# Parse the AI response into structured data
if success:
    parsed = _parse_json_response(analysis_output)
else:
    parsed = AnalysisDetail(details=analysis_output)

# Apply the same analysis to all failures in the group
return [
    FailureAnalysis(
        test_name=f.test_name,
        error=f.error_message,
        analysis=parsed,
        error_signature=get_failure_signature(f),
    )
    for f in failures
]
```

## FastAPI app and backend services

`src/jenkins_job_insight/main.py` is the composition root for the whole application. It does more than expose routes:

- Initializes the database in the FastAPI lifespan hook.
- Marks stale `pending` and `running` jobs as failed on startup.
- Resumes `waiting` jobs that were monitoring Jenkins when the service restarted.
- Serves the built React app and its static assets.
- Hosts JSON API endpoints for analysis, results, history, comments, reviews, classification, Jira/GitHub preview/create flows, and health checks.

The API surface is organized around a few core areas:

- Analysis: `POST /analyze` and `POST /analyze-failures`
- Result retrieval: `GET /results`, `GET /results/{job_id}`, `GET /api/dashboard`
- Collaboration: comments, review state, override classification
- History: `GET /history/failures`, `GET /history/test/{test_name}`, `GET /history/search`, `GET /history/stats/{job_name}`
- Automation: preview/create tracker issues and enrich comment links
- Capability discovery: `GET /api/capabilities`, `GET /ai-configs`, `GET /health`

`src/jenkins_job_insight/models.py` keeps those interfaces explicit. That file is where to look when you want the canonical request/response shapes for things like `AnalyzeRequest`, `AnalyzeFailuresRequest`, `FailureAnalysis`, `ChildJobAnalysis`, and `AnalysisResult`.

> **Tip:** Set `PUBLIC_BASE_URL` when JJI runs behind a reverse proxy or external hostname. Link generation intentionally uses that trusted setting instead of request headers, which avoids host-header problems but means the app will otherwise return relative URLs.

### Analyzer and Jenkins integration

`src/jenkins_job_insight/analyzer.py` is the real engine room.

One deliberate design choice is that JJI talks to AI providers through their command-line tools, not through vendor SDKs. In practice that means the analyzer is responsible for prompt construction, workspace setup, retries, and structured-response parsing, while authentication stays with the provider CLI itself.

For Jenkins-backed analysis, the analyzer does the following:

- Uses `JenkinsClient` from `src/jenkins_job_insight/jenkins.py` to fetch build info, console output, and structured test reports.
- Detects failed child jobs and recursively analyzes them, so pipeline/orchestrator jobs are represented as parent results with nested `child_job_analyses`.
- Falls back to console-only analysis when no structured test report exists.
- Optionally clones the tests repository with `RepositoryManager` so the AI can inspect real files and git history.
- Optionally downloads and extracts build artifacts with `src/jenkins_job_insight/jenkins_artifacts.py`, then exposes them as `build-artifacts/` inside the AI working directory.

That artifact layer is more than a convenience. It includes archive size limits, path traversal protection, and safe tar/zip extraction before the AI ever sees the files. If your Jenkins jobs produce diagnostic logs, events, or YAML/JSON status dumps, this is where that context is turned into something the analyzer can use.

The analyzer also has a history-aware path. When it has a local server URL and a current `job_id`, it points the AI at `src/jenkins_job_insight/ai-prompts/FAILURE_HISTORY_ANALYSIS.md` and lets the AI query JJI’s own history endpoints. That keeps the main prompt smaller and makes history lookup a first-class tool instead of an enormous prompt appendix.

### Jira, issue creation, and comment enrichment

`src/jenkins_job_insight/jira.py` is focused on one job: taking `PRODUCT BUG` analyses and searching Jira for likely duplicates.

Its design is pragmatic:

- `JiraClient` auto-detects Jira Cloud versus Server/Data Center based on the credentials you provide.
- Searches are scoped to Bug issues and can be narrowed to a project key.
- The analyzer stores `jira_search_keywords` inside each `ProductBugReport`.
- Jira search returns broad candidates, then an additional AI pass filters those candidates down to the genuinely relevant ones.

That means Jira enrichment is post-analysis automation, not part of the root-cause classification step itself.

Related automation lives alongside it:

- `src/jenkins_job_insight/bug_creation.py` turns stored failure analysis into GitHub issue or Jira bug preview content, searches for duplicates, and can create the tracker item if the server is configured to allow it.
- `src/jenkins_job_insight/comment_enrichment.py` scans comments for GitHub PR links, GitHub issue links, and Jira keys, then fetches live status so the report UI can show whether linked work is open, closed, merged, and so on.

In practice, this gives JJI two useful post-analysis layers:
- Deduplicate likely product bugs against Jira.
- Turn a stored result into a ready-to-file issue or bug without re-running the analysis.

## Storage, history, and recovery

`src/jenkins_job_insight/storage.py` uses SQLite, with the database defaulting to `/data/results.db`. The schema is intentionally split between “current job state” and “query-friendly history”.

The key tables are:

- `results`: one row per analysis job, including current status, timestamps, Jenkins URL, and the full JSON result blob.
- `comments`: user comments on top-level or child-job failures.
- `failure_reviews`: per-failure reviewed/not-reviewed state.
- `test_classifications`: manual and AI-written classifications, including visibility rules and override history.
- `failure_history`: a flattened, denormalized record of failures used by the dashboard and history endpoints.

A few storage behaviors matter when you are reasoning about the system:

- Completed results are flattened into `failure_history` after analysis, so history queries do not have to repeatedly parse nested JSON blobs.
- User overrides are mirrored back into both `failure_history` and the stored result JSON, so page refreshes and history filters stay in sync.
- Startup recovery treats stale jobs differently: orphaned `pending` and `running` jobs are failed immediately, while `waiting` jobs can be resumed if their stored request parameters are still usable.
- Sensitive request fields are encrypted before they are stored for wait/resume behavior, and they are stripped back out before API responses are returned.

> **Warning:** Waiting-job resumption depends on encrypted stored request parameters. If you change `JJI_ENCRYPTION_KEY`, old waiting jobs may no longer be decryptable and will not resume cleanly.

> **Note:** `failure_history` records failure events, not every passing run. That is why some history views show estimated pass rates, and why some statistics only become meaningful when you scope them to a specific job.

## Web UI and HTML reporting

The HTML report is a React single-page application in `frontend/`, not a server-rendered template system. FastAPI serves the compiled frontend, and the browser then drives everything else through JSON endpoints.

That split is visible in the shared `/results/{job_id}` route. Browsers get the SPA or a redirect to the live status page, while API clients get JSON.

```1146:1168:src/jenkins_job_insight/main.py
@app.get("/results/{job_id}", response_model=None)
async def get_job_result(request: Request, job_id: str, response: Response):
    """Retrieve stored result by job_id, or serve SPA for browser requests."""
    # Content negotiation: browsers requesting HTML get the SPA
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        result = await get_result(job_id)
        if result and result.get("status") in IN_PROGRESS_STATUSES:
            return RedirectResponse(url=f"/status/{job_id}", status_code=302)
        return _serve_spa()

    logger.debug(f"GET /results/{job_id}")
    result = await get_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")
    _attach_result_links(result, _extract_base_url(), job_id)
    settings = get_settings()
    result["capabilities"] = {
        "github_issues": settings.github_issues_enabled,
        "jira_bugs": settings.jira_enabled,
    }
    if result.get("status") in IN_PROGRESS_STATUSES:
        response.status_code = 202
```

On the frontend side, the routes are deliberately small and direct:

```13:28:frontend/src/App.tsx
export default function App() {
  return (
    <BrowserRouter basename="/">
      <Routes>
        <Route path="/register" element={<RegisterPage />} />
        <Route element={<Layout />}>
          <Route index element={<ProtectedRoute><DashboardPage /></ProtectedRoute>} />
          <Route path="/dashboard" element={<Navigate to="/" replace />} />
          <Route path="/history" element={<ProtectedRoute><HistoryPage /></ProtectedRoute>} />
          <Route path="/history/test/:testName" element={<ProtectedRoute><TestHistoryPage /></ProtectedRoute>} />
          <Route path="/results/:jobId" element={<ProtectedRoute><ReportPage /></ProtectedRoute>} />
          <Route path="/status/:jobId" element={<ProtectedRoute><StatusPage /></ProtectedRoute>} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
```

The important UI pieces are:

- `frontend/src/pages/DashboardPage.tsx`: recent analyses, failure counts, review/comment counts, delete action, and polling refresh.
- `frontend/src/pages/StatusPage.tsx`: live polling screen for `waiting`, `pending`, and `running` jobs.
- `frontend/src/pages/ReportPage.tsx`: the main HTML report view for one analysis.
- `frontend/src/pages/report/`: report-specific building blocks such as `FailureCard`, `ChildJobSection`, `CommentsSection`, `ClassificationSelect`, `BugCreationDialog`, and `ReviewToggle`.
- `frontend/src/pages/report/ReportContext.tsx`: page-scoped `useReducer` state for results, comments, review state, enrichments, AI configs, and manual overrides.
- `frontend/src/pages/HistoryPage.tsx` and `frontend/src/pages/TestHistoryPage.tsx`: flattened failure history and per-test drill-down pages.
- `frontend/src/lib/grouping.ts`: mirrors backend failure grouping logic so the UI can present deduplicated cards that line up with backend error-signature grouping.
- `frontend/src/lib/api.ts`: the centralized fetch wrapper used by the whole frontend.

> **Note:** The `jji_username` cookie is used for attribution and collaboration in the UI. It is a convenience mechanism, not a full authentication system.

## CLI, configuration, and runtime packaging

JJI ships two entry points from `pyproject.toml`: the server entry point `jenkins-job-insight` and the CLI entry point `jji`. The CLI lives under `src/jenkins_job_insight/cli/` and is intentionally thin: `JJIClient` is just an HTTP client that maps commands to the same REST endpoints the web UI uses.

The sample CLI/server-profile config is in `config.example.toml`. It is structured around shared defaults plus named servers, so you can reuse the same CLI against local, staging, and production environments without changing commands.

```12:23:config.example.toml
[default]
server = "dev"  # Default server name

[defaults]
# Global defaults -- all servers inherit these
jenkins_url = "https://jenkins.example.com"
jenkins_user = "your-jenkins-user"
jenkins_password = "your-jenkins-token"  # pragma: allowlist secret
jenkins_ssl_verify = true
tests_repo_url = "https://github.com/your-org/your-tests"
ai_provider = "claude"
ai_model = "claude-opus-4-6[1m]"
```

The rest of that file defines per-environment profiles such as `[servers.dev]`, `[servers.staging]`, and `[servers.prod]`, each with its own URL, username, SSL setting, and optional overrides.

`docker-compose.yaml` shows the intended packaging model: one container, one public port, and a persistent `/data` mount for SQLite.

```22:41:docker-compose.yaml
services:
  jenkins-job-insight:
    # Build from local Dockerfile
    build:
      context: .
      dockerfile: Dockerfile

    # Container name for easier management
    container_name: jenkins-job-insight

    # Ports: Web UI + API served on the same port
    ports:
      - "8000:8000"   # Web UI (React) + REST API
      # Dev mode: Vite HMR for frontend hot-reload (uncomment with DEV_MODE=true)
      # - "5173:5173"

    # Persist SQLite database across container restarts
    # The ./data directory on host maps to /data in container
    volumes:
      - ./data:/data
```

Under the hood, the `Dockerfile` builds the frontend with Vite, installs the Python application, and copies the compiled `frontend/dist` assets into the runtime image. `entrypoint.sh` adds OpenShift-friendly behavior, default port handling, and optional frontend hot-reload in `DEV_MODE`.

## Examples and tests

The `examples/pytest-junitxml/` directory shows how to use JJI outside Jenkins. The example is a standalone pytest plugin that sends raw JUnit XML to `POST /analyze-failures` after a failing test session and writes the enriched XML back to the same file.

```33:67:examples/pytest-junitxml/conftest_junit_ai.py
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
    if session.config.option.analyze_with_ai:
        setup_ai_analysis(session)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Enrich JUnit XML with AI analysis when tests fail.

    Only runs when exitstatus indicates test failures (exit code != 0).
    Skips enrichment when all tests pass or execution was interrupted.
    """
    if session.config.option.analyze_with_ai:
        if exitstatus == 0:
            logger.info(
                "No test failures (exit code %d), skipping AI analysis", exitstatus
            )

        else:
            try:
                enrich_junit_xml(session)
            except Exception:
                logger.exception("Failed to enrich JUnit XML, original preserved")
```

The test suite is broad and maps closely to the main architecture:

- `tests/test_main.py`: FastAPI routes, async job queuing, XML analysis mode, waiting/resume behavior, dashboard/history endpoints, comments/reviews, issue preview/create flows, and SPA behavior.
- `tests/test_storage.py`: schema initialization, persistence, classification overrides, result recovery, and stale-job handling.
- `tests/test_analyzer.py`: Jenkins exception translation and AI CLI retry behavior.
- `tests/test_jira.py`: Jira Cloud vs Server/DC auth detection, search, candidate handling, and AI relevance filtering.
- `tests/test_bug_creation.py`: issue text generation, fallback behavior, duplicate lookup, and issue creation helpers.
- `tests/test_jenkins.py`: Jenkins URL parsing edge cases.
- `tests/test_cli_main.py` and `tests/test_cli_client.py`: CLI parity, flag handling, config behavior, and HTTP client request mapping.
- `frontend/src/**/__tests__`: frontend API wrapper, grouping logic, cookies, and report-context helpers.

The repo’s top-level validation entry point is `tox.toml`, which runs both backend and frontend checks:

```1:30:tox.toml
skipsdist = true
envlist = ["backend", "frontend"]

[env.backend]
description = "Run Python tests"
commands = [["uv", "run", "--extra", "tests", "pytest", "tests/", "-q"]]
allowlist_externals = ["uv"]

[env.frontend]
commands = [
  [
    "npm",
    "ci",
    "--no-audit",
    "--no-fund",
  ],
  [
    "npx",
    "vite",
    "build",
  ],
  [
    "npm",
    "test",
  ],
]
description = "Run frontend build and tests"
skip_install = true
allowlist_externals = ["npm", "npx"]
change_dir = "frontend"
```

> **Note:** This repository does not currently include a checked-in GitHub Actions, GitLab CI, CircleCI, or Jenkins pipeline file. The practical automation entry points in the repo are `tox.toml`, `.pre-commit-config.yaml`, `Dockerfile`, and `docker-compose.yaml`.

## What to read first

If you are onboarding to the codebase, this order gives the fastest payoff:

1. `src/jenkins_job_insight/main.py`
2. `src/jenkins_job_insight/analyzer.py`
3. `src/jenkins_job_insight/storage.py`
4. `frontend/src/App.tsx` and `frontend/src/pages/ReportPage.tsx`
5. `src/jenkins_job_insight/jira.py` and `src/jenkins_job_insight/bug_creation.py`
6. `examples/pytest-junitxml/`
7. `tests/test_main.py`, `tests/test_storage.py`, and `tests/test_cli_main.py`

That path takes you from request entry, to analysis orchestration, to persistence, to HTML reporting, to tracker automation, and finally to the tests that describe the expected behavior.
