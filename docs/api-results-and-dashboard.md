# Results, Reports, and Dashboard Endpoints

These endpoints are where you read and browse analysis output after a job has been submitted. Use `/health` for probes, `/results` for a lightweight recent-jobs list, `/results/{job_id}` for full JSON retrieval, `/status/{job_id}` for the in-browser waiting screen, and `/dashboard` or `/` for the React dashboard. The browser UI is now a single-page app, so `/dashboard` and `/results/{job_id}` load the frontend shell and then call supporting JSON endpoints.

Use `/results`, `/results/{job_id}`, and `/api/dashboard` for automation. Use `/`, `/dashboard`, `/results/{job_id}`, and `/status/{job_id}` when a human is browsing results in a browser.

## Quick Reference

| Endpoint | Response type | Best for |
| --- | --- | --- |
| `GET /health` | JSON | Liveness and readiness checks |
| `GET /results` | JSON array | Listing recent jobs without loading full result payloads |
| `GET /results/{job_id}` | JSON object for API clients, SPA route for browsers | Full result retrieval and the browser report route |
| `GET /status/{job_id}` | SPA route | Watching queued, waiting, or running jobs in a browser |
| `GET /api/dashboard` | JSON array | Dashboard data for the React UI, CLI, or custom tooling |
| `GET /dashboard` | SPA route | Opening the dashboard in a browser |

## URL Generation and Browser Behavior

The service no longer trusts `Host` or `X-Forwarded-*` headers when building public links. Instead, `base_url` and `result_url` are derived only from `PUBLIC_BASE_URL`. If that setting is not configured, the API returns an empty `base_url` and relative links such as `/results/{job_id}`.

Codebase example:

```153:160:src/jenkins_job_insight/main.py
settings = get_settings()
if settings.public_base_url:
    return settings.public_base_url.rstrip("/")

logger.debug(
    "PUBLIC_BASE_URL is not set; returning empty base URL (relative paths)"
)
return ""
```

> **Tip:** If you want absolute `result_url` values or clickable tracker links in issue previews, set `PUBLIC_BASE_URL`. Reverse-proxy headers alone are no longer enough.

Browser routes still keep the lightweight username flow. HTML requests without a `jji_username` cookie are redirected to `/register`, while `/health`, `/favicon.ico`, `/api/*`, and `/assets/*` remain accessible without that cookie.

> **Note:** The `jji_username` cookie is still a UI convenience on a trusted network, not a full authentication boundary.

## GET `/health`

Use this endpoint for health checks, readiness probes, and simple smoke tests.

What to expect:

- `200 OK` with `{"status": "healthy"}`
- `GET` only
- No username cookie required
- Safe for container and orchestrator probes

Example from the test suite:

```48:52:tests/test_main.py
response = test_client.get("/health")
assert response.status_code == 200
assert response.json() == {"status": "healthy"}
```

The container image also uses `/health` for its built-in health check:

```95:96:Dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1
```

## GET `/results`

Use `/results` when you want a fast, lightweight list of recent analyses. It is intentionally small and does not return the full stored result payload.

Query parameters:

| Parameter | Default | Limits | Meaning |
| --- | --- | --- | --- |
| `limit` | `50` | Maximum `100` | Number of recent jobs to return |

Response fields for each item:

| Field | Meaning |
| --- | --- |
| `job_id` | Analysis job ID |
| `jenkins_url` | Jenkins build URL when the analysis came from Jenkins |
| `status` | `waiting`, `pending`, `running`, `completed`, or `failed` |
| `created_at` | When the job record was created |

> **Note:** `waiting` means the service accepted the job and is polling Jenkins for build completion before AI analysis starts.

The storage query shows exactly how small this endpoint is:

```629:639:src/jenkins_job_insight/storage.py
cursor = await db.execute(
    """
    SELECT job_id, jenkins_url, status, created_at
    FROM results
    ORDER BY created_at DESC
    LIMIT ?
    """,
    (limit,),
)
rows = await cursor.fetchall()
return [dict(row) for row in rows]
```

Practical behavior:

- Results are ordered newest first by `created_at`
- An empty database returns `[]`
- Requests above the max, such as `/results?limit=200`, are rejected with a `422` validation error

> **Tip:** Reach for `/results` first in automation. If you need the actual analysis payload, follow up with `GET /results/{job_id}`.

## GET `/results/{job_id}`

Use this endpoint when you need the stored JSON for one analysis job. API clients always get JSON. Browser requests that prefer HTML use this same path as the report route: the server serves the React app, and if the job is still in progress it redirects the browser to `/status/{job_id}` first.

The core handler shows the split behavior:

```1149:1169:src/jenkins_job_insight/main.py
accept = request.headers.get("accept", "")
if "text/html" in accept and "application/json" not in accept:
    result = await get_result(job_id)
    if result and result.get("status") in IN_PROGRESS_STATUSES:
        return RedirectResponse(url=f"/status/{job_id}", status_code=302)
    return _serve_spa()

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
return result
```

Top-level fields you should expect in the JSON response:

| Field | Meaning |
| --- | --- |
| `job_id` | The analysis job ID |
| `jenkins_url` | Original Jenkins build URL, if any |
| `status` | Current job state: `waiting`, `pending`, `running`, `completed`, or `failed` |
| `created_at` | When the job record was created |
| `analysis_started_at` | When analysis work actually started, if available |
| `completed_at` | When the job reached a terminal state, if available |
| `base_url` | Trusted public base URL from `PUBLIC_BASE_URL`, or `""` when unset |
| `result_url` | Canonical URL for this job result |
| `capabilities` | Feature flags such as `github_issues` and `jira_bugs` |
| `result` | The stored analysis payload, if one has been persisted |

For Jenkins-backed analyses, `result` usually includes fields such as `job_name`, `build_number`, `summary`, `ai_provider`, `ai_model`, `failures`, and `child_job_analyses`. While a job is still `waiting` or `running`, the same payload can also include `request_params`, `progress_phase`, and `progress_log`. Those fields let JJI resume waiting jobs after a restart and let the status page show refresh-safe progress details.

Sensitive values inside `request_params` are stripped before the response is returned. If peer analysis was used for a failure group, each affected failure can also include `peer_debate`, which records the participating AI configs, the round-by-round debate trail, and whether consensus was reached. Direct failure analyses created through `/analyze-failures` keep a smaller payload.

Codebase examples:

```713:740:src/jenkins_job_insight/storage.py
async def get_result(job_id: str, *, strip_sensitive: bool = True) -> dict | None:
    # ... existing code ...
    parsed = parse_result_json(row["result_json"], job_id=job_id)
    if parsed and strip_sensitive:
        parsed = strip_sensitive_from_response(parsed)
```

```610:623:src/jenkins_job_insight/peer_analysis.py
peer_debate = PeerDebate(
    consensus_reached=consensus_reached,
    rounds_used=rounds_used,
    max_rounds=max_rounds,
    ai_configs=[
        AiConfigEntry(
            ai_provider=main_ai_provider,  # type: ignore[arg-type]
            ai_model=main_ai_model,
        ),
        *peer_ai_configs,
    ],
    rounds=all_rounds,
)
```

Example from the current test suite:

```837:842:tests/test_main.py
response = test_client.get("/results/job-123")
assert response.status_code == 200
data = response.json()
assert data["job_id"] == "job-123"
assert data["base_url"] == ""
assert data["result_url"] == "/results/job-123"
```

Important behavior:

- Missing jobs return `404`
- In-progress JSON responses return `202 Accepted`
- Browser requests for completed or failed jobs use this same route as the report page
- Browser requests for `waiting`, `pending`, or `running` jobs are redirected to `/status/{job_id}`
- In a browser, the route loads the interactive report UI with grouped failures, child-job sections, peer-analysis summaries and debate timelines when available, comments, review toggles, classification overrides, and issue preview/create actions
- The response always includes `base_url` and `result_url`
- The response also includes `capabilities` so the report UI can decide whether GitHub and Jira actions should be shown

> **Note:** There is no separate `/results/{job_id}.html` route anymore. Open `/results/{job_id}` in a browser for the report page, and use the same path with `Accept: application/json` for automation.

## GET `/status/{job_id}`

Use this browser-only route when a job is still `waiting`, `pending`, or `running`. The server sends people here automatically when they open `/results/{job_id}` in a browser before analysis is finished.

The status page polls `GET /results/{job_id}` every 10 seconds, redirects to the report route when the job completes, and reads `result.progress_log`, `result.progress_phase`, and `result.request_params` from the same response so the UI can show progress, the main AI, and configured peers while the job is still in flight.

```149:187:frontend/src/pages/StatusPage.tsx
// Derive stepLog from server-persisted progress_log (survives F5 refresh)
const rawProgressLog = data?.result?.progress_log
const progressLog = Array.isArray(rawProgressLog) ? rawProgressLog : []
const stepLog: StepLogEntry[] = useMemo(
  () => progressLog.map(entry => ({
    phase: entry.phase,
    label: getPhaseLabel(entry.phase) ?? entry.phase,
    timestamp: new Date(entry.timestamp * 1000).toLocaleTimeString(),
  })),
  [progressLog],
)

// ... existing code ...

const params = data?.result?.request_params
const mainAi = params?.ai_provider && params?.ai_model
  ? `${params.ai_provider} / ${params.ai_model}`
  : null
const peers = params?.peer_ai_configs
const progressPhase = data?.result?.progress_phase
```

What the page shows in practice:

- `waiting` when the service is polling Jenkins for build completion
- `pending` when the job is queued for analysis
- `running` while AI analysis is in progress
- A refresh-safe progress panel driven by `result.progress_log`
- Server-reported phases such as `waiting_for_jenkins`, `analyzing`, `analyzing_failures`, `analyzing_child_jobs`, `enriching_jira`, and `saving`
- Peer-analysis phases such as `peer_review_round_1` and `orchestrator_revising_round_1`, with group suffixes like `(group 2/3)` when multiple failure groups are being debated
- The main AI provider/model and any configured peers when `result.request_params` is present
- A dedicated timeout-style terminal state for AI analysis timeouts, even though the underlying API status remains `failed`
- A terminal error view for other `failed`, `404`, or `403` cases
- The Jenkins build link, queued timestamp, and current status badge when that data is available

> **Note:** `/status/{job_id}` is a browser route served by the React app. For scripts and automation, keep polling `GET /results/{job_id}` instead.

## GET `/dashboard`

`/dashboard` is now a browser route in the React app, not a fully server-rendered HTML index. Opening `/` or `/dashboard` loads the SPA shell; once the client router is running, `/dashboard` redirects to `/`.

The dashboard data itself comes from `GET /api/dashboard`, which returns the precomputed summary rows used by the web UI and the CLI.

Codebase example:

```2097:2100:src/jenkins_job_insight/main.py
@app.get("/api/dashboard")
async def api_dashboard() -> list[dict]:
    """Return dashboard job list as JSON for the React frontend."""
    return await list_results_for_dashboard()
```

```1430:1435:src/jenkins_job_insight/storage.py
DEFAULT_DASHBOARD_LIMIT = 500

async def list_results_for_dashboard(
    limit: int = DEFAULT_DASHBOARD_LIMIT,
) -> list[dict]:
```

Response fields from `/api/dashboard`:

| Field | Meaning |
| --- | --- |
| `job_id` | Analysis job ID |
| `jenkins_url` | Jenkins build URL when one is available |
| `status` | Current job state |
| `created_at` | When the job record was created |
| `completed_at` | When the job completed or failed, if available |
| `analysis_started_at` | When analysis work started, if available |
| `reviewed_count` | Number of reviewed failures for the job |
| `comment_count` | Number of comments attached to the job |
| `job_name` | Jenkins job name when stored in the result payload |
| `build_number` | Jenkins build number when available |
| `failure_count` | Total failures, including nested child-job failures |
| `child_job_count` | Number of direct child job analyses when present |
| `summary` | Stored analysis summary when available |
| `error` | Stored terminal error message when available |

The dashboard page fetches that JSON on load, refreshes it every 10 seconds, and keeps sort state in `sessionStorage`, so a reload in the same browser tab keeps the selected sort column and direction.

```12:33:frontend/src/lib/useTableSort.ts
export function useTableSort(
  storagePrefix: string,
  defaultKey: string,
  defaultDir: SortDirection,
  descDefaultKeys: string[] = [],
) {
  const [sortKey, setSortKey] = useSessionState(`${storagePrefix}.sortKey`, defaultKey)
  const [sortDir, setSortDir] = useSessionState<SortDirection>(`${storagePrefix}.sortDir`, defaultDir)

  const handleSort = useCallback(
    (key: string) => {
      if (key === sortKey) {
        setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
      } else {
        setSortKey(key)
        setSortDir(descDefaultKeys.includes(key) ? 'desc' : 'asc')
      }
    },
    [sortKey, sortDir, setSortKey, setSortDir, descDefaultKeys],
  )

  return { sortKey, sortDir, handleSort }
}
```

What the browser dashboard shows:

- Client-side filtering by job name or job ID, plus a status filter with `All statuses`, `completed`, `running`, `waiting`, `pending`, `failed`, and a derived `timeout` view for failed AI timeouts; the API still returns `status: failed`
- Sortable `Job`, `Status`, `Failures`, `Reviewed`, `Comments`, `Children`, and `Created` columns
- Session-scoped sort persistence, so the selected sort order survives a refresh in the same browser tab
- Client-side pagination with 10, 20, or 50 rows per page
- Failure, review, comment, and child-job counts
- Relative creation timestamps with full timestamps in tooltips, plus analysis duration when both `analysis_started_at` and `completed_at` are available
- Row navigation to `/status/{job_id}` for `waiting`, `pending`, and `running` jobs, or `/results/{job_id}` for terminal jobs
- A delete action that calls `DELETE /results/{job_id}`

> **Note:** The old server-side `limit` query parameter and `Load last` control are gone. `/api/dashboard` currently returns the server's default dashboard batch (`500` rows), and the React app filters, sorts, and paginates that data in memory.

## Supporting Endpoints for Reports and Dashboard

The React report and dashboard routes are backed by several smaller JSON endpoints. The report page is interactive: comments, review state, classifications, issue previews, issue creation, and dashboard summary data are loaded or updated live.

| Method | Endpoint | What it does |
| --- | --- | --- |
| `GET` | `/api/dashboard` | Returns dashboard rows as JSON for the React UI and `jji results dashboard` |
| `GET` | `/results/{job_id}/comments` | Returns all comments and review state for a job |
| `POST` | `/results/{job_id}/comments` | Adds a comment to a specific failure |
| `DELETE` | `/results/{job_id}/comments/{comment_id}` | Deletes a comment |
| `PUT` | `/results/{job_id}/reviewed` | Marks a failure reviewed or unreviewed |
| `GET` | `/results/{job_id}/review-status` | Returns lightweight counts for failures, reviews, and comments |
| `POST` | `/results/{job_id}/enrich-comments` | Resolves GitHub PR and Jira status badges from comment text |
| `PUT` | `/results/{job_id}/override-classification` | Changes a failure group to `CODE ISSUE` or `PRODUCT BUG` |
| `GET` | `/history/classifications` | Returns visible classifications used for badges in the report UI |
| `GET` | `/ai-configs` | Returns distinct AI provider/model pairs from completed analyses |
| `GET` | `/api/capabilities` | Returns whether GitHub issue creation and Jira bug creation are enabled on this server |
| `POST` | `/results/{job_id}/preview-github-issue` | Generates a GitHub issue draft and duplicate suggestions |
| `POST` | `/results/{job_id}/preview-jira-bug` | Generates a Jira bug draft and duplicate suggestions |
| `POST` | `/results/{job_id}/create-github-issue` | Creates a GitHub issue and auto-adds its URL as a comment |
| `POST` | `/results/{job_id}/create-jira-bug` | Creates a Jira bug and auto-adds its URL as a comment |
| `DELETE` | `/results/{job_id}` | Deletes the job, related metadata, and discussion data |

### Comments and Review State

Use these endpoints when you want to layer human workflow onto an analysis report.

A lightweight review summary looks like this in the test suite:

```1746:1751:tests/test_main.py
response = test_client.get("/results/job-rs-1/review-status")
assert response.status_code == 200
data = response.json()
assert data["total_failures"] == 2
assert data["reviewed_count"] == 1
assert data["comment_count"] == 1
```

Useful details:

- `GET /results/{job_id}/comments` returns both `comments` and `reviews` in one response
- If nothing has been commented or reviewed yet, the response is simply empty collections
- `GET /results/{job_id}/review-status` is a good fit when you need counts but not the full comment list
- `POST /results/{job_id}/comments` validates that the `test_name` actually exists in the stored result
- `POST /results/{job_id}/enrich-comments` is what adds live status badges such as GitHub PR `OPEN` or `MERGED`, or Jira ticket status
- Child-job-scoped actions use `child_job_name` and `child_build_number`, and those two fields must be supplied together
- The React report page reads comments and review state live from these endpoints instead of rebuilding a separate HTML report

> **Note:** The report UI fetches comments and reviews after the page loads, and comment refreshes continue in the browser while the report is open.

### Classifications, Issue Previews, and Issue Creation

These endpoints turn a report into a workflow surface.

What they do:

- `PUT /results/{job_id}/override-classification` accepts only `CODE ISSUE` or `PRODUCT BUG`
- Overrides apply to the failure group for that job, not just a single row, so grouped failures stay in sync
- `GET /history/classifications?job_id=...` is how the report and dashboard render human and AI classification badges
- `GET /ai-configs` returns only provider/model pairs from completed analyses, which the report UI uses as known-working choices
- Preview endpoints return `title`, `body`, and `similar_issues`
- Create endpoints return created issue metadata and auto-add a comment linking back to the created issue
- Preview requests take `test_name` and optionally `child_job_name`, `child_build_number`, `include_links`, `ai_provider`, and `ai_model`
- Create requests take `test_name`, `title`, `body`, and optional child-job scope
- External GitHub or Jira API failures are surfaced as `502`, which makes integration outages easier to distinguish from local validation errors

The `ai-configs` endpoint is intentionally based on completed results only:

```2057:2082:src/jenkins_job_insight/storage.py
async def get_ai_configs() -> list[dict]:
    """Get distinct AI provider/model pairs from completed analysis results.

    Queries the results table for unique (ai_provider, ai_model) combinations
    from successfully completed analyses. These represent known-working configs.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT
                json_extract(result_json, '$.ai_provider') as ai_provider,
                json_extract(result_json, '$.ai_model') as ai_model
            FROM results
            WHERE status = 'completed'
              AND json_extract(result_json, '$.ai_provider') IS NOT NULL
              AND json_extract(result_json, '$.ai_provider') != ''
              AND json_extract(result_json, '$.ai_model') IS NOT NULL
              AND json_extract(result_json, '$.ai_model') != ''
            ORDER BY ai_provider, ai_model
            """
        )
        rows = await cursor.fetchall()
        return [{"ai_provider": row[0], "ai_model": row[1]} for row in rows]
```

A real create-issue test from the codebase shows the expected request shape and response behavior:

```1957:1969:tests/test_main.py
response = test_client.post(
    "/results/job-create-gh/create-github-issue",
    json={
        "test_name": "test_login_success",
        "title": "Bug: login fails",
        "body": "## Details\nLogin returns 500",
    },
)
assert response.status_code == 201
data = response.json()
assert "https://github.com" in data["url"]
assert data["comment_id"] > 0
```

Do not confuse the two classification systems:

- `override-classification` changes the main root-cause label between `CODE ISSUE` and `PRODUCT BUG`
- `/history/classifications` supplies broader tags such as `FLAKY`, `REGRESSION`, `KNOWN_BUG`, `INFRASTRUCTURE`, and `INTERMITTENT`

## Deleting Results

`DELETE /results/{job_id}` is the one destructive endpoint in this family. The dashboard uses it for its delete button.

What happens on delete:

- The job record is removed
- Related comments are removed
- Review state is removed
- Failure history is removed
- Test classifications are removed

Without a username cookie, delete returns `401` with `Please register a username first`. Missing jobs return `404`.

> **Warning:** The app treats `jji_username` as a convenience on a trusted network, not a real authentication boundary. Deletion and some other write actions check the cookie, but the code explicitly does not treat it as security.

If you are using the CLI, pass a username with `--user` or set `JJI_USERNAME` so delete actions have the cookie they expect.

## CLI Equivalents

Most of the core results and dashboard endpoints also have direct `jji` commands:

- `jji health`
- `jji results list`
- `jji results dashboard`
- `jji results show JOB_ID`
- `jji status JOB_ID`
- `jji results review-status JOB_ID`
- `jji results delete JOB_ID`
- `jji comments list JOB_ID`
- `jji ai-configs`

Useful CLI behavior:

- Set the server with `--server` or `JJI_SERVER`
- `--server` can be a full URL or a named profile from `~/.config/jji/config.toml`
- Add `--json` to print raw JSON instead of the default table output
- Use `--user` or `JJI_USERNAME` for delete and other user-attributed actions

## Configuration That Affects These Endpoints

The results, report, and dashboard endpoints depend on three main pieces of configuration: where data is stored, how the service is exposed, and whether optional GitHub/Jira integrations are enabled.

### Storage and Frontend Assets

The persistent data behind these endpoints lives in SQLite. By default the database is `/data/results.db`. The React UI is built ahead of time and shipped inside the container image at `/app/frontend/dist`; it is not generated lazily per job and there is no separate on-disk HTML report cache anymore.

Codebase examples:

```21:21:src/jenkins_job_insight/storage.py
DB_PATH = Path(os.getenv("DB_PATH", "/data/results.db"))
```

```87:88:Dockerfile
# Copy built frontend assets from frontend builder
COPY --chown=appuser:0 --from=frontend-builder /frontend/dist /app/frontend/dist
```

The default Compose file still persists `/data` on the host:

```32:41:docker-compose.yaml
# Ports: Web UI + API served on the same port
ports:
  - "8000:8000"   # Web UI (React) + REST API

# Persist SQLite database across container restarts
# The ./data directory on host maps to /data in container
volumes:
  - ./data:/data
```

Mounting `/data` preserves stored results, comments, reviews, failure history, and classifications. Rebuilding or redeploying the image updates the frontend assets.

### GitHub and Jira Integration

The result and report endpoints expose GitHub and Jira actions only when those integrations are actually configured. The explicit enable flags only override auto-detection; they do not bypass missing URLs or credentials. The same `GITHUB_TOKEN` is also used to enrich GitHub PR status in comments for private repositories.

| Integration | Required settings | Optional explicit toggle |
| --- | --- | --- |
| GitHub issue creation | `TESTS_REPO_URL` and `GITHUB_TOKEN` | `ENABLE_GITHUB_ISSUES` |
| Jira bug creation and Jira matching | `JIRA_URL`, valid Jira credentials, and `JIRA_PROJECT_KEY` | `ENABLE_JIRA` |

The enablement rules are enforced in configuration code:

```113:150:src/jenkins_job_insight/config.py
if self.enable_jira is False:
    return False
if not self.jira_url:
    # ... warning omitted ...
    return False
_, token_value = _resolve_jira_auth(self)
if not token_value:
    # ... warning omitted ...
    return False
if not self.jira_project_key:
    # ... warning omitted ...
    return False
return True

# ... GitHub toggle ...
if self.enable_github_issues is False:
    return False
tests_repo_url = str(self.tests_repo_url) if self.tests_repo_url else ""
github_token = self.github_token.get_secret_value() if self.github_token else ""
return bool(tests_repo_url and github_token)
```

> **Note:** `GET /results/{job_id}` includes a `capabilities` object with `github_issues` and `jira_bugs`, so the report UI can hide unavailable actions without making extra guesses.

### Port and Health Checks

By default, the service runs on port `8000`. The container entrypoint respects `PORT` if you override it, and the built-in Docker health check uses `/health` on that port.

## API Discovery

If you want to inspect the live schema instead of reading hand-written docs:

- `GET /docs` provides the Swagger UI
- `GET /openapi.json` provides the OpenAPI schema

Most people start at `/` or `/dashboard` and follow links into `/results/{job_id}` or `/status/{job_id}`. Most automation starts at `/results` and `/results/{job_id}`, with `/api/dashboard` as a convenient pre-aggregated summary feed when you do not need the full stored payload. The browser-only SPA routes are served outside the OpenAPI schema, so Swagger is mainly useful for the JSON endpoints.
