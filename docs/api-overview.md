# API Overview

`jenkins-job-insight` exposes a JSON API for analyzing Jenkins failures, storing the results, and giving you both machine-friendly and browser-friendly views of the same job. A single result can contain top-level failures plus nested `child_job_analyses` when a pipeline fans out into child jobs.

Most users touch four parts of the API:

- submit work with `POST /analyze` or `POST /analyze-failures`
- fetch stored output from `GET /results/{job_id}` or browse the React report and status routes at `/results/{job_id}` and `/status/{job_id}`
- triage failures with comments, review state, classifications, and issue creation endpoints
- explore the live schema in `GET /openapi.json` and Swagger UI in `GET /docs`

## Primary Workflows

Use `POST /analyze` when the service can reach Jenkins directly. Required fields are `job_name` and `build_number`, and `job_name` can include folder-style Jenkins paths such as `folder/job-name`. The endpoint is asynchronous only: it returns `202 Accepted`, a new `job_id`, and a `result_url` you can poll or open. If the target Jenkins build is still running and `wait_for_completion` stays enabled, the stored job enters `waiting` until Jenkins finishes; `poll_interval_minutes` and `max_wait_minutes` control that monitoring window.

The repository’s test suite exercises that async contract directly:

```130:145:tests/test_main.py
response = test_client.post(
    "/analyze",
    json={
        "job_name": "test",
        "build_number": 123,
        "tests_repo_url": "https://github.com/example/repo",
        "ai_provider": "claude",
        "ai_model": "test-model",
    },
)
assert response.status_code == 202
data = response.json()
assert data["status"] == "queued"
assert data["base_url"] == ""
assert data["result_url"].startswith("/results/")
```

Use `POST /analyze-failures` when you already have failure objects or raw JUnit XML and do not need Jenkins lookups. This route is synchronous only, but it still creates a stored `job_id`, so `GET /results/{job_id}` works afterward just like it does for Jenkins-backed analysis. It also reuses the same grouped-failure analysis flow, so repeated failures are deduplicated before AI analysis.

The request must contain exactly one of `failures` or `raw_xml`. When you send XML, the response can include `enriched_xml`:

```661:674:tests/test_main.py
response = test_client.post(
    "/analyze-failures",
    json={
        "raw_xml": self.SAMPLE_XML,
        "ai_provider": "claude",
        "ai_model": "test-model",
    },
)
assert response.status_code == 200
data = response.json()
assert data["status"] == "completed"
assert data["enriched_xml"] is not None
assert "<?xml" in data["enriched_xml"]
assert len(data["failures"]) == 1
```

> **Note:** `POST /analyze` returns `status: "queued"` in its immediate response. That is a submission state, not a stored job state.

## Endpoint Summary

### Analysis And Results

| Endpoint | Use it for | Notes |
| --- | --- | --- |
| `POST /analyze` | Analyze a Jenkins job/build. | Async only. Returns `202` with `status: "queued"`. If Jenkins is still running and `wait_for_completion` is enabled, the stored job moves to `waiting` until the build finishes. |
| `POST /analyze-failures` | Analyze raw failures or JUnit XML without Jenkins. | Synchronous only. Accepts either `failures` or `raw_xml`, not both. |
| `GET /results/{job_id}` | Fetch the stored JSON result for a job. | JSON clients get the stored wrapper plus `base_url`, `result_url`, and `capabilities`; the response status is `202` while the job is `pending`, `waiting`, or `running`. Browser requests redirect to `/status/{job_id}` while work is in progress and otherwise load the React report UI. |
| `GET /results` | List recent analysis jobs. | Returns recent `job_id`, `jenkins_url`, `status`, and timestamp fields. |
| `DELETE /results/{job_id}` | Delete a job and related stored data. | Removes the job, comments, review state, history rows, and classifications. |

### Review, Comments, And Issue Workflows

| Endpoint | Use it for | Notes |
| --- | --- | --- |
| `GET /results/{job_id}/comments` | Fetch comments and review state together. | Returns both `comments` and `reviews` in one response. |
| `POST /results/{job_id}/comments` | Add a comment to a specific failed test. | Returns `201` with the new comment ID. |
| `DELETE /results/{job_id}/comments/{comment_id}` | Delete a comment. | Uses the built-in username cookie for UI/CLI identity. |
| `PUT /results/{job_id}/reviewed` | Mark a failure reviewed or unreviewed. | Works for top-level and child-job failures. |
| `GET /results/{job_id}/review-status` | Get a lightweight review summary. | Returns `total_failures`, `reviewed_count`, and `comment_count`. |
| `PUT /results/{job_id}/override-classification` | Override the main analysis classification. | Switches a failure between `CODE ISSUE` and `PRODUCT BUG`. |
| `POST /results/{job_id}/enrich-comments` | Resolve live GitHub PR and Jira statuses mentioned in comments. | Best-effort enrichment; failures are swallowed rather than crashing the request. |
| `POST /results/{job_id}/preview-github-issue` | Draft a GitHub issue body from a failure. | Can also search for similar existing GitHub issues. |
| `POST /results/{job_id}/create-github-issue` | Create the GitHub issue. | Uses server-side GitHub config, returns `201`, and auto-adds a comment with the created issue URL. |
| `POST /results/{job_id}/preview-jira-bug` | Draft a Jira bug from a failure. | Can also search for similar Jira issues. |
| `POST /results/{job_id}/create-jira-bug` | Create the Jira bug. | Uses server-side Jira config, returns `201`, and auto-adds a comment with the created bug URL. |

> **Warning:** For any endpoint that targets a failure inside a child job, send both `child_job_name` and `child_build_number`. Sending only one is rejected with validation errors.

### History And Classifications

| Endpoint | Use it for | Notes |
| --- | --- | --- |
| `GET /history/failures` | Browse paginated failure history. | Supports `search`, `job_name`, `classification`, `limit`, and `offset`. |
| `GET /history/test/{test_name}` | Inspect the history of one test. | Returns recent runs, comments, classification breakdown, and failure-rate fields. |
| `GET /history/search` | Find all tests that failed with the same error signature. | Requires the `signature` query parameter. |
| `GET /history/stats/{job_name}` | Get aggregate statistics for one job. | Includes overall failure rate, common failures, and a recent trend summary. |
| `GET /history/trends` | Get time-series failure data. | Supports `daily` and `weekly` grouping. |
| `POST /history/classify` | Add a historical label to a test. | Allowed labels are `FLAKY`, `REGRESSION`, `INFRASTRUCTURE`, `KNOWN_BUG`, and `INTERMITTENT`. `KNOWN_BUG` requires `references`. |
| `GET /history/classifications` | Query saved historical classifications. | Useful for dashboards, UI filters, and custom clients. |

> **Note:** Historical labels like `FLAKY`, `REGRESSION`, and `KNOWN_BUG` are separate from the main analysis classification. The history API stores test-history labels, while `PUT /results/{job_id}/override-classification` only switches the primary analysis result between `CODE ISSUE` and `PRODUCT BUG`.

### Browser, Health, And Documentation Endpoints

| Endpoint | Use it for | Notes |
| --- | --- | --- |
| `GET /api/dashboard` | Fetch dashboard rows as JSON. | Used by the React dashboard. |
| `GET /api/capabilities` | Discover which server-side issue workflows are available. | Returns `github_issues` and `jira_bugs` booleans. |
| `GET /ai-configs` | List distinct `ai_provider` and `ai_model` pairs from completed jobs. | Useful for clients that want to present known-working configs. |
| `GET /health` | Health check. | Used by the container health checks. |
| `GET /docs` | Open Swagger UI. | Generated automatically by FastAPI. |
| `GET /openapi.json` | Fetch the OpenAPI schema. | Generated automatically by FastAPI. |

The browser UI is a React app rooted at `GET /`. The frontend route table shows the main browser views:

```15:24:frontend/src/App.tsx
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
```

> **Warning:** Browser identity is client-side. The frontend stores `jji_username` as a cookie for attribution, not as an authentication boundary, and the service still does not expose token-based API authentication. Treat it as a trusted-network tool.

Across the API, the common response patterns are predictable: validation problems surface as `422`, not-found conditions as `404`, failure-specific operations against still-running jobs can return `202`, failed analysis jobs can return `409`, and upstream GitHub or Jira failures surface as `502`.

## Job States

The API uses one immediate submission word and five stored job states:

- `queued`: returned by `POST /analyze` to say the request was accepted and a `job_id` was allocated
- `pending`: a result row exists, but background analysis has not started processing the job yet
- `waiting`: the service is polling Jenkins because the target build is still running and `wait_for_completion` is enabled
- `running`: analysis is actively in progress
- `completed`: the final result is available
- `failed`: the analysis did not complete successfully

> **Note:** `queued` is only the immediate submission response. Stored job objects themselves use `pending`, `waiting`, `running`, `completed`, and `failed`.

`POST /analyze-failures` is synchronous, so its response model only uses `completed` or `failed`. If a browser opens `GET /results/{job_id}` while a job is `pending`, `waiting`, or `running`, the server redirects to `/status/{job_id}`; once the job is done, the same route serves the React report view.

## Absolute URLs In Responses

The API still attaches `base_url` and `result_url` to submission and result responses, but it no longer emits `html_report_url`. `GET /results/{job_id}` also adds a `capabilities` object so clients can tell whether GitHub issue creation and Jira bug creation are enabled on the server.

Absolute link generation is now opt-in. The configuration model documents the rule directly:

```70:74:src/jenkins_job_insight/config.py
# Trusted public base URL — used for result_url and tracker links.
# When set, _extract_base_url() returns this value verbatim.
# When unset, _extract_base_url() returns an empty string (relative
# URLs only) — request Host / X-Forwarded-* headers are never trusted.
public_base_url: str | None = None
```

Set `PUBLIC_BASE_URL` when you want absolute links in API responses or tracker previews. If you leave it unset, `base_url` is empty and `result_url` remains relative, such as `/results/{job_id}`.

> **Tip:** If you want `preview-github-issue` or `preview-jira-bug` to include clickable report links, configure `PUBLIC_BASE_URL` and send `include_links: true`. Without a public base URL, the service omits those report links rather than trusting request headers.

## OpenAPI And Swagger

You do not need to hand-maintain API docs. The app uses FastAPI’s generated schema and Swagger UI. The repository tests verify both `GET /openapi.json` and `GET /docs`, and the generated schema reports title `Jenkins Job Insight` with version `0.1.0`:

```934:943:tests/test_main.py
response = test_client.get("/openapi.json")
assert response.status_code == 200
schema = response.json()
assert schema["info"]["title"] == "Jenkins Job Insight"
assert schema["info"]["version"] == "0.1.0"

response = test_client.get("/docs")
assert response.status_code == 200
```

The browser landing page is `GET /` (with `/dashboard` as a client-side alias), not `GET /docs`. Swagger covers the FastAPI JSON endpoints only; browser routes such as `/results/{job_id}`, `/status/{job_id}`, `/history`, and `/register` are handled by the React SPA and are not listed as separate OpenAPI operations.

> **Tip:** With the default local container setup, open [http://localhost:8000/docs](http://localhost:8000/docs) for Swagger UI and [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json) for the raw schema.

## Configuration

At minimum, the service needs an AI provider and model. For Jenkins-backed `POST /analyze` calls, Jenkins connectivity can be configured on the server or supplied per request. The checked-in environment template shows a typical Jenkins-backed setup:

```6:19:.env.example
JENKINS_URL=https://jenkins.example.com
JENKINS_USER=your-username
JENKINS_PASSWORD=your-api-token
JENKINS_SSL_VERIFY=true

# ===================
# AI CLI Configuration
# ===================
# Choose AI provider (required): "claude", "gemini", or "cursor"
AI_PROVIDER=claude

# AI model to use (required, applies to any provider)
# Can also be set per-request in webhook body
AI_MODEL=your-model-name
```

Analysis requests can override many environment-backed defaults per request, including AI settings, Jenkins connectivity, Jira options, artifact download limits, `tests_repo_url`, and the Jenkins monitoring controls `wait_for_completion`, `poll_interval_minutes`, and `max_wait_minutes`. Absolute API links come from the server-level `PUBLIC_BASE_URL` setting, not from request headers.

> **Note:** Analysis endpoints support per-request overrides, but the issue preview/create flows use server-level GitHub and Jira configuration rather than caller-supplied credentials.

For local deployment, the checked-in Compose file serves the React UI and REST API together on port `8000` and uses `GET /health` as its liveness check:

```33:60:docker-compose.yaml
ports:
  - "8000:8000"   # Web UI (React) + REST API
  # Dev mode: Vite HMR for frontend hot-reload (uncomment with DEV_MODE=true)
  # - "5173:5173"

# Persist SQLite database across container restarts
# The ./data directory on host maps to /data in container
volumes:
  - ./data:/data
  # Optional: Mount gcloud credentials for Vertex AI authentication
  # Uncomment if using CLAUDE_CODE_USE_VERTEX=1 with Application Default Credentials
  # - ~/.config/gcloud:/home/appuser/.config/gcloud:ro
  # Dev mode: mount source for hot-reload (uncomment with DEV_MODE=true)
  # - ./src:/app/src
  # - ./frontend:/app/frontend


# Restart policy: container restarts unless explicitly stopped
restart: unless-stopped

# Health check configuration
# Verifies the service is responding on /health endpoint
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 10s
```

That same Compose setup also persists the SQLite-backed application data under `./data`, so analysis history and stored results survive container restarts.
