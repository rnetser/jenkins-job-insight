# Results, Reports, and Dashboard Endpoints

`jenkins-job-insight` serves results in two layers:

- JSON endpoints for scripts, integrations, and the `jji` CLI
- Browser routes for the built-in React UI

The key path is `GET /results/{job_id}`. It is intentionally dual-purpose: API-style requests get JSON, while browser-style HTML requests open the interactive report page.

> **Note:** Browser navigation without a `jji_username` cookie is redirected to `/register`. JSON clients are not redirected, and that cookie is used for attribution in the UI rather than full authentication.

## At a Glance

| Route | Type | Use it when you want to... |
| --- | --- | --- |
| `GET /health` | JSON | Probe the service or verify it is up |
| `GET /results` | JSON array | List recent analyses quickly |
| `GET /results/{job_id}` | JSON or browser route | Fetch one full result or open the report page |
| `GET /api/dashboard` | JSON array | Get the dashboard summary feed |
| `GET /dashboard` | Browser route | Open the dashboard in a browser |
| `GET /status/{job_id}` | Browser route | Watch a queued or running analysis |

```mermaid
flowchart TD
    A[Automation or jji CLI] --> B[GET /health]
    A --> C[GET /results]
    A --> D[GET /results/{job_id}]
    A --> E[GET /api/dashboard]

    F[Browser opens / or /dashboard] --> G[React SPA]
    G --> E

    H[Browser opens /results/{job_id}] --> I{Job still active?}
    I -- Yes --> J[/status/{job_id}/]
    J --> D
    I -- No --> K[Report SPA]
    K --> D
    K --> L[GET /results/{job_id}/comments]
    K --> M[POST /results/{job_id}/enrich-comments]
    K --> N[GET /ai-configs]
```

## GET `/health`

Use this endpoint for liveness checks, readiness checks, smoke tests, and simple monitoring.

What to expect:

- `200 OK` with `{"status": "healthy"}`
- `GET` only
- No username cookie required
- Safe for container and load-balancer probes

The container image uses this same route for its built-in health check:

```115:116:Dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1
```

## GET `/results`

Use `GET /results` when you want a lightweight list of recent analysis jobs without loading the full stored payload for each one.

Query parameters:

| Parameter | Default | Limit | Meaning |
| --- | --- | --- | --- |
| `limit` | `50` | max `100` | Number of recent jobs to return |

Each item contains only:

- `job_id`
- `jenkins_url`
- `status`
- `created_at`

That lightweight shape is enforced directly in storage:

```769:779:src/jenkins_job_insight/storage.py
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

- Results are ordered newest first
- An empty database returns `[]`
- Values above `limit=100` are rejected with `422`

> **Tip:** Start with `GET /results` for polling or simple automation. Only call `GET /results/{job_id}` when you actually need the stored analysis body.

## GET `/results/{job_id}`

This is the main results endpoint.

For API clients, it returns the full stored wrapper for one job. For browser requests that prefer HTML, the same path opens the built-in report UI. If the job is still active, the browser is sent to `GET /status/{job_id}` first.

The route handler shows that split behavior clearly:

```1332:1355:src/jenkins_job_insight/main.py
@app.get("/results/{job_id}", response_model=None)
async def get_job_result(request: Request, job_id: str, response: Response):
    """Retrieve stored result by job_id, or serve SPA for browser requests."""
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

Top-level response fields:

| Field | Meaning |
| --- | --- |
| `job_id` | Analysis job ID |
| `jenkins_url` | Jenkins build URL, when the job came from Jenkins |
| `status` | `waiting`, `pending`, `running`, `completed`, or `failed` |
| `created_at` | When the record was created |
| `analysis_started_at` | When AI analysis actually started, if available |
| `completed_at` | When the job finished or failed, if available |
| `base_url` | Trusted public base URL, or `""` when unset |
| `result_url` | Canonical result URL for this job |
| `capabilities` | Feature flags such as `github_issues` and `jira_bugs` |
| `result` | The stored analysis payload, or `null` if no body has been persisted yet |

Inside `result`, you will usually see fields such as `job_name`, `build_number`, `summary`, `ai_provider`, `ai_model`, `failures`, and `child_job_analyses`. While a job is still active, the same payload can also carry `request_params`, `progress_phase`, and `progress_log`, which is how the status page survives refreshes. Direct analyses created through `/analyze-failures` use the same wrapper, but typically omit Jenkins-specific fields.

In the browser report, repeated failures are grouped by `error_signature`, so one report card can represent a whole root-cause group instead of repeating the same failure many times:

```25:28:frontend/src/lib/grouping.ts
export function groupingKey(failure: FailureAnalysis): string {
  return failure.error_signature || `unique-${failure.test_name}`
}
```

Useful status behavior:

- Active jobs return `202 Accepted` to JSON clients
- Completed and failed jobs return `200`
- Missing jobs return `404` to JSON clients
- Browsers are redirected to `/status/{job_id}` for `waiting`, `pending`, and `running` jobs

### About `GET /results/{job_id}.html`

> **Warning:** There is no special `GET /results/{job_id}.html` route in the current codebase. Use `GET /results/{job_id}` for both the browser report and the JSON API.

> **Tip:** If a browser-oriented HTTP client hands you the report UI instead of JSON, send `Accept: application/json`.

### Public URLs and `result_url`

`base_url` and `result_url` are built only from `PUBLIC_BASE_URL`. Forwarded headers are intentionally ignored.

```146:164:src/jenkins_job_insight/main.py
def _extract_base_url() -> str:
    """Extract the external base URL for building public-facing links."""
    settings = get_settings()
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")

    logger.debug(
        "PUBLIC_BASE_URL is not set; returning empty base URL (relative paths)"
    )
    return ""
```

What that means in practice:

- If `PUBLIC_BASE_URL` is set, `result_url` is absolute, such as `https://jji.example.com/results/<job_id>`
- If it is not set, `base_url` is `""` and `result_url` is relative, such as `/results/<job_id>`

## GET `/status/{job_id}`

`GET /status/{job_id}` is a browser route, not a JSON API. It exists to show progress while an analysis is still active.

The page polls `GET /results/{job_id}` every 10 seconds and automatically moves to the report once the job completes. While it is open, it can show:

- queued, waiting, and running state
- the Jenkins job name and build number
- the main AI and configured peers, when stored
- a persisted progress timeline from `result.progress_log`
- a user-friendly timeout state, even though the underlying API status remains `failed`

> **Note:** For scripts and CLI tooling, keep polling `GET /results/{job_id}` instead of trying to automate the browser status route.

## GET `/dashboard` and GET `/api/dashboard`

`GET /dashboard` is a browser route. It opens the React dashboard UI. The actual dashboard data comes from `GET /api/dashboard`.

Important distinction:

- Use `GET /dashboard` or `GET /` when a human is opening the web UI
- Use `GET /api/dashboard` when a script, integration, or CLI command needs the dashboard dataset

Once the SPA loads, the client router redirects `/dashboard` to `/`. The browser dashboard then refreshes `GET /api/dashboard` every 10 seconds.

`GET /api/dashboard` returns a richer summary row than `GET /results`. Each item can include:

| Field | Meaning |
| --- | --- |
| `job_id` | Analysis job ID |
| `jenkins_url` | Jenkins build URL, when available |
| `status` | Job status |
| `created_at` | Record creation time |
| `completed_at` | Finish time, if available |
| `analysis_started_at` | Analysis start time, if available |
| `reviewed_count` | Number of reviewed failures |
| `comment_count` | Number of comments on the job |
| `job_name` | Jenkins job name, when stored |
| `build_number` | Jenkins build number, when stored |
| `failure_count` | Total failures, including nested child-job failures |
| `child_job_count` | Number of direct child job analyses |
| `summary` | Stored summary, when available |
| `error` | Stored terminal error, when available |

The backend builds that summary by combining table counts with selected fields from the stored result body:

```1518:1545:src/jenkins_job_insight/storage.py
entry: dict = {
    "job_id": row["job_id"],
    "jenkins_url": row["jenkins_url"],
    "status": row["status"],
    "created_at": row["created_at"],
    "completed_at": row["completed_at"]
    if "completed_at" in row.keys()
    else None,
    "analysis_started_at": row["analysis_started_at"]
    if "analysis_started_at" in row.keys()
    else None,
    "reviewed_count": row["reviewed_count"],
    "comment_count": row["comment_count"],
}
result_data = parse_result_json(row["result_json"], job_id=row["job_id"])
if result_data:
    entry["job_name"] = result_data.get("job_name", "")
    if "build_number" in result_data:
        entry["build_number"] = result_data["build_number"]
    entry["failure_count"] = count_all_failures(result_data)
    child_jobs = result_data.get("child_job_analyses", [])
    if child_jobs:
        entry["child_job_count"] = len(child_jobs)
```

Current dashboard behavior that matters to users:

- The server returns a default dashboard batch of `500` jobs
- The React UI handles search, sorting, and pagination client-side
- Clicking an active job opens `GET /status/{job_id}`
- Clicking a finished job opens `GET /results/{job_id}`

> **Note:** The UI can show `timeout` as a convenience label, but the API still reports `status: failed`. That label is derived from the stored error or summary text on the client side.

## Supporting Endpoints For Interactive Reports

The browser report page is not just a static viewer. It uses smaller JSON endpoints for discussion, review workflow, issue creation, and feature detection.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/results/{job_id}/comments` | Load comments and review state together |
| `POST` | `/results/{job_id}/comments` | Add a comment to a failure |
| `DELETE` | `/results/{job_id}/comments/{comment_id}` | Delete a comment |
| `PUT` | `/results/{job_id}/reviewed` | Mark a failure reviewed or not reviewed |
| `GET` | `/results/{job_id}/review-status` | Return counts for total failures, reviewed failures, and comments |
| `POST` | `/results/{job_id}/enrich-comments` | Add live GitHub or Jira status badges to tracker links found in comments |
| `PUT` | `/results/{job_id}/override-classification` | Change the primary root-cause label shown in the report |
| `GET` | `/api/capabilities` | Report whether GitHub and Jira issue creation are enabled on this server |
| `GET` | `/ai-configs` | Return distinct provider/model pairs from completed analyses |
| `POST` | `/results/{job_id}/preview-github-issue` | Generate a GitHub issue draft and similar-issue suggestions |
| `POST` | `/results/{job_id}/preview-jira-bug` | Generate a Jira bug draft and similar-issue suggestions |
| `POST` | `/results/{job_id}/create-github-issue` | Create a GitHub issue and add a tracker comment to the report |
| `POST` | `/results/{job_id}/create-jira-bug` | Create a Jira bug and add a tracker comment to the report |
| `DELETE` | `/results/{job_id}` | Delete the job and all related stored data |

### Comments and review state

Use these endpoints when you want the report to double as a review surface.

Useful details:

- `GET /results/{job_id}/comments` returns both `comments` and `reviews` in one response
- `GET /results/{job_id}/review-status` is the lightweight counts-only version
- `total_failures` in that review summary follows the same full-failure counting used elsewhere, so nested child-job failures are included
- `POST /results/{job_id}/enrich-comments` is best-effort enrichment for tracker links found in comments
- The browser report refreshes comments while it is open, so new discussion and tracker badges show up without reloading the whole report

### Issue previews, issue creation, and feature flags

The report UI previews tracker content first, then creates the issue, then refreshes comments so the server-added tracker link appears immediately:

```65:105:frontend/src/pages/report/BugCreationDialog.tsx
api
  .post<PreviewIssueResponse>(`/results/${jobId}/${previewPath}`, {
    test_name: testName,
    include_links: includeLinks,
    ai_provider: aiProvider ?? '',
    ai_model: aiModel ?? '',
    // ... existing code ...
  })
  .then((res) => {
    setTitle(res.title)
    setBody(res.body)
    setSimilar(res.similar_issues ?? [])
    setPhase('preview')
  })

async function handleCreate() {
  const res = await api.post<CreateIssueResponse>(`/results/${jobId}/${createPath}`, {
    test_name: testName,
    title,
    body,
    // ... existing code ...
  })
  setCreatedUrl(res.url)

  api.get<CommentsAndReviews>(`/results/${jobId}/comments`)
    .then((commentsRes) => dispatch({ type: 'SET_COMMENTS_AND_REVIEWS', payload: commentsRes }))
    .catch(() => {})
```

Practical rules:

- `GET /api/capabilities` is the quickest way to ask whether GitHub and Jira creation are enabled
- `GET /results/{job_id}` already includes the same `capabilities` object, so the report page does not need a second lookup
- Preview endpoints return `title`, `body`, and `similar_issues`
- Create endpoints return the created tracker URL, the tracker identifier (`number` for GitHub or `key` for Jira), and a `comment_id`
- GitHub issue creation only works for results currently classified as `CODE ISSUE`
- Jira bug creation only works for results currently classified as `PRODUCT BUG`
- These tracker actions depend on server-side deployment settings, not request-supplied credentials
- `GET /ai-configs` only returns provider/model pairs from completed analyses

### Deleting results

`DELETE /results/{job_id}` removes the result itself and the stored review workflow around it.

That includes:

- comments
- review state
- failure history
- saved classifications
- the result row itself

> **Warning:** Deleting a result is permanent. Without a registered username in the built-in UI, the server returns `401`.

## Configuration That Changes What Users See

A few settings directly affect the results, report, and dashboard experience.

| Setting | What it affects |
| --- | --- |
| `PUBLIC_BASE_URL` | Controls `base_url` and `result_url` in API responses |
| `DB_PATH` | Controls where results are stored |
| `PORT` | Controls the port that serves both the API and the browser UI |
| `TESTS_REPO_URL`, `GITHUB_TOKEN`, optional `ENABLE_GITHUB_ISSUES` | Enable GitHub issue preview and creation |
| `JIRA_URL`, Jira credentials, `JIRA_PROJECT_KEY`, optional `ENABLE_JIRA` | Enable Jira preview and creation |

The default database file is `/data/results.db`. In the provided container setup, `docker-compose.yaml` mounts `./data:/data`, so results survive restarts. The same deployment also serves both the API and the browser UI on port `8000`.

The browser UI is shipped as built frontend assets inside the container image, which is why routes such as `/dashboard` and `/results/{job_id}` work without a separate frontend deployment.

> **Note:** If you run the backend without the built `frontend/dist` assets present, browser routes return `404` with `Frontend not built`.

## CLI Equivalents

The `jji` CLI wraps the same core endpoints:

| CLI command | Endpoint |
| --- | --- |
| `jji health` | `GET /health` |
| `jji results list` | `GET /results` |
| `jji results show JOB_ID` | `GET /results/{job_id}` |
| `jji results dashboard` | `GET /api/dashboard` |
| `jji status JOB_ID` | `GET /results/{job_id}` |
| `jji results review-status JOB_ID` | `GET /results/{job_id}/review-status` |
| `jji results delete JOB_ID` | `DELETE /results/{job_id}` |

Helpful CLI details:

- Add `--json` if you want the raw API response instead of table output
- Use a configured server profile or `--server <url>` to target a specific deployment
- If you need username-backed actions such as delete, configure a username in your CLI profile or pass `--user`

## Live API Discovery

If you want the live schema as served by your current deployment:

- `GET /docs` opens Swagger UI
- `GET /openapi.json` returns the OpenAPI schema

> **Tip:** Use Swagger for the JSON endpoints. Browser-only SPA routes such as `GET /dashboard` and `GET /status/{job_id}` are part of the built-in UI, not the OpenAPI contract.


## Related Pages

- [HTML Reports and Dashboard](html-reports-and-dashboard.html)
- [Storage and Result Lifecycle](storage-and-result-lifecycle.html)
- [API Overview](api-overview.html)
- [POST /analyze](api-post-analyze.html)
- [POST /analyze-failures](api-post-analyze-failures.html)