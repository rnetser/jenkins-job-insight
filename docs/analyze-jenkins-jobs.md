# Analyze Jenkins Jobs

`POST /analyze` submits a Jenkins build for analysis as an async job and stores the result so you can revisit it later as JSON or in the web UI.

Most clients use one of these patterns:
- submit asynchronously, then poll `GET /results/{job_id}`
- submit asynchronously, then open `/status/{job_id}` in a browser while the job is queued, waiting, or running
- submit early and let the server wait for the Jenkins build to finish before analysis by leaving `wait_for_completion` enabled

A completed analysis can represent either:
- a failing Jenkins build that was analyzed successfully
- a successful Jenkins build with nothing to analyze

`status: "failed"` means the analysis process failed. It does not simply mean the Jenkins build failed.

## Before you call it

The server needs Jenkins access and an AI provider. The minimum settings shown in `.env.example` are:

```dotenv
JENKINS_URL=https://jenkins.example.com
JENKINS_USER=your-username
JENKINS_PASSWORD=your-api-token
JENKINS_SSL_VERIFY=true

# Choose AI provider (required): "claude", "gemini", or "cursor"
AI_PROVIDER=claude

# AI model to use (required, applies to any provider)
# Can also be set per-request in webhook body
AI_MODEL=your-model-name
```

Optional defaults in `.env.example` let you add repository context for every request:

```dotenv
# Tests repository URL
# TESTS_REPO_URL=https://github.com/org/test-repo
```

> **Note:** Request-body values override environment defaults. That includes `tests_repo_url`, `ai_provider`, `ai_model`, Jenkins connection settings, `wait_for_completion`, `poll_interval_minutes`, `max_wait_minutes`, artifact settings, and timeout settings. `PUBLIC_BASE_URL` is server-side only and controls whether returned `result_url` values are absolute or relative.

## Request body

At minimum, send `job_name` and `build_number`. One of the endpoint tests uses this exact JSON body:

```json
{
  "job_name": "test",
  "build_number": 123,
  "tests_repo_url": "https://github.com/example/repo"
}
```

The most useful request fields are:

| Field | Required | Purpose |
| --- | --- | --- |
| `job_name` | Yes | Jenkins job name. Folder-style names such as `folder/job-name` are supported. |
| `build_number` | Yes | Jenkins build number to analyze. |
| `tests_repo_url` | No | Repository to clone so the AI can inspect test code. |
| `ai_provider` | No | Per-request AI provider override. Valid values are `claude`, `gemini`, and `cursor`. |
| `ai_model` | No | Per-request AI model override. |
| `wait_for_completion` | No | If `true` (default), monitor a still-running Jenkins build and start analysis only after it reaches a terminal state. |
| `poll_interval_minutes` | No | Minutes between Jenkins status polls while waiting. Default is `2`. |
| `max_wait_minutes` | No | Maximum minutes to wait for build completion. `0` means no limit. |
| `jenkins_url`, `jenkins_user`, `jenkins_password`, `jenkins_ssl_verify` | No | Per-request Jenkins overrides. |
| `get_job_artifacts` | No | Controls whether build artifacts are downloaded for analysis. Default is `true`. |
| `jenkins_artifacts_max_size_mb` | No | Max total artifact size to process. |
| `jenkins_artifacts_context_lines` | No | Max artifact context lines to feed into the AI prompt. |
| `raw_prompt` | No | Extra instructions appended to the AI prompt. |

If `tests_repo_url` is provided, the repository is cloned once and reused for the main build analysis and any recursively analyzed child jobs.

Validation is strict:
- Missing required fields return `422`.
- A non-numeric `build_number` returns `422`.
- Invalid URLs such as a bad `tests_repo_url` return `422`.
- `poll_interval_minutes` must be greater than `0`, and `max_wait_minutes` cannot be negative.

## Async vs Sync

### Async mode

For Jenkins-backed analysis, `POST /analyze` is the submission flow. On successful submission, it returns `202 Accepted`, creates a stored job record, and schedules background work.

The queueing path currently looks like this:

```python
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

In practice, the submission response gives you:
- `status: "queued"`
- `job_id`
- `message`
- `base_url`
- `result_url`

The response no longer includes `html_report_url`. For browser flows, use `/status/{job_id}` while the job is in progress and `/results/{job_id}` for the final report page.

After submission, the stored status progresses through:
- `waiting` while the service is in the Jenkins monitoring phase before analysis begins
- `pending` when the job is queued without a waiting phase
- `running` while analysis is actively executing
- `completed` or `failed` when processing finishes

> **Note:** AI configuration is validated before the job is queued. If no AI provider or model is configured, `POST /analyze` returns `400` immediately instead of returning `202` and failing later.

> **Note:** Waiting jobs persist resumable request parameters. If the server restarts while a job is in the `waiting` phase, monitoring can resume when the app comes back up.

### Sync mode

The older `?sync=true` pattern is not part of the current Jenkins-backed flow. Treat `POST /analyze` as async-only and retrieve the final result from `GET /results/{job_id}`.

If you need a single request/response flow, the supported synchronous endpoint is `POST /analyze-failures`, which analyzes raw failures or JUnit XML instead of pulling data from Jenkins.

## Polling and Reports

Once you have a `job_id`, there are two main ways to follow the job.

### `GET /results/{job_id}`

This is the machine-friendly endpoint for both progress checks and final result retrieval.

The JSON response path is implemented like this:

```python
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

A stored result record includes:
- top-level job metadata such as `job_id`, `status`, `jenkins_url`, `created_at`, `analysis_started_at`, and `completed_at`
- a nested `result` object containing the latest stored payload, such as initial job metadata, a completed analysis, or failure details
- `base_url` and `result_url`, generated on each response
- `capabilities`, which tells the UI whether GitHub issue creation and Jira bug creation are enabled

Status meanings:
- `waiting`: accepted and stored, currently in the Jenkins monitoring phase before analysis begins
- `pending`: accepted and queued
- `running`: analysis is in progress
- `completed`: analysis finished and a result is available
- `failed`: the analysis process failed

> **Note:** `completed` refers to the analysis job, not the Jenkins build result. A Jenkins build can fail and still produce `status: "completed"` if the analysis itself finished successfully.

> **Note:** `GET /results/{job_id}` returns `202 Accepted` while the job is `waiting`, `pending`, or `running`. Once the job reaches `completed` or `failed`, the same endpoint returns `200 OK`.

### Browser flow

The browser UI now uses React routes instead of `GET /results/{job_id}.html`. There is no separate HTML report endpoint to poll or refresh.

For browser requests, the backend checks the `Accept` header and either redirects an in-progress job to `/status/{job_id}` or serves the report app:

```python
accept = request.headers.get("accept", "")
if "text/html" in accept and "application/json" not in accept:
    result = await get_result(job_id)
    if result and result.get("status") in IN_PROGRESS_STATUSES:
        return RedirectResponse(url=f"/status/{job_id}", status_code=302)
    return _serve_spa()
```

In practice:
- open `/status/{job_id}` while the job is `waiting`, `pending`, or `running`
- open `/results/{job_id}` once the analysis is finished
- if you hit `/results/{job_id}` too early in a browser, the server redirects you to `/status/{job_id}` automatically

The React status page then polls `GET /results/{job_id}` and switches to the report view when the analysis finishes:

```typescript
const res = await api.get<ResultResponse>(`/results/${jobId}`)
if (res.status === 'completed') {
  stopPolling()
  navigate(`/results/${jobId}`, { replace: true })
} else if (res.status === 'failed') {
  stopPolling()
  setTerminalErrorKind('failed')
  setError(res.result?.error ?? 'Analysis failed')
}
```

> **Note:** `result_url` and `base_url` come from `PUBLIC_BASE_URL` when that setting is configured. When it is not set, the service returns relative links such as `/results/{job_id}` and does not trust `Host` or `X-Forwarded-*` headers.

## Callbacks

Callbacks are no longer part of the `POST /analyze` flow.

> **Warning:** Legacy `CALLBACK_URL`, `CALLBACK_HEADERS`, `callback_url`, and `callback_headers` examples are outdated for this endpoint.

Use one of these supported patterns instead:
- poll `GET /results/{job_id}` from automation
- open `/status/{job_id}` in a browser while the job is still in progress
- treat the stored result at `GET /results/{job_id}` as the durable source of truth

## How Successful Builds Are Handled

The analyzer checks the Jenkins build result before it starts deeper failure analysis. If Jenkins reports `SUCCESS`, the service returns early with a normal completed result and an empty failure list:

```python
build_result = build_info.get("result")
if build_result == "SUCCESS":
    return AnalysisResult(
        job_id=job_id,
        job_name=request.job_name,
        build_number=request.build_number,
        jenkins_url=HttpUrl(jenkins_build_url),
        status="completed",
        summary="Build passed successfully. No failures to analyze.",
        ai_provider=ai_provider,
        ai_model=ai_model,
        failures=[],
    )
```

What this means for users:
- a passing build is not treated as an error
- `status` is still `completed`
- `failures` is empty
- the result is still stored and accessible from the results endpoints

> **Note:** Because the service short-circuits on `SUCCESS`, no failure parsing or AI classification runs for a passing build.

## Recursive Child-Job Analysis

`POST /analyze` does not stop at the top-level Jenkins build. If the build is a pipeline or orchestrator job, it also looks for failed downstream jobs and analyzes them recursively.

### What gets detected as a child job

The analyzer first checks structured Jenkins metadata:
- `subBuilds`
- `actions[].triggeredBuilds`

Only child jobs with `FAILURE` or `UNSTABLE` are included.

If no child jobs are found there, it falls back to console parsing. The console parser looks for lines like `Build folder » job-name #123 completed: FAILURE`:

```python
pattern = r"Build\s+(.+?)\s+#(\d+)\s+completed:\s*(FAILURE|UNSTABLE)"
matches = re.findall(pattern, console_output)

for match in matches:
    job_path = match[0].strip()
    build_num = int(match[1])
    job_name = job_path.replace(" » ", "/")
    failed_jobs.append((job_name, build_num))
```

### How recursion works

When child jobs are found, the service analyzes them in parallel with bounded concurrency and descends through nested failures.

Key behaviors:
- direct child analyses appear in `child_job_analyses`
- deeper nested children appear in `failed_children`
- if a pipeline wrapper has no direct test failures, its own `failures` list can be empty even though its child jobs contain failures
- if a child job cannot be inspected, or recursion reaches the limit, that child entry gets a `note` instead of failing the entire analysis

> **Note:** The maximum child-job recursion depth is `3`. When that limit is reached, the child entry is returned with a note explaining that analysis stopped to prevent infinite recursion.

### Leaf child jobs vs pipeline wrapper jobs

Leaf child jobs are analyzed like normal Jenkins builds:
- if a Jenkins test report exists, individual failed tests are extracted and analyzed
- if no structured test report exists, the service falls back to console-only analysis

Pipeline-style child jobs are treated differently:
- if they have failed children but no direct test failures, the service skips direct failure analysis for the wrapper job and returns nested child analyses instead

This keeps pipeline results focused on the actual failing jobs instead of a vague top-level pipeline failure.

## Result Shape to Expect

A completed analysis result contains:
- `job_id`, `job_name`, `build_number`, and `jenkins_url`
- `status` and `summary`
- `ai_provider` and `ai_model`
- `failures` for direct failures on the requested build
- `child_job_analyses` for direct child jobs

Each child job can contain:
- `job_name`, `build_number`, and `jenkins_url`
- `summary`
- `failures`
- nested `failed_children`
- `note` when recursion stops or a child job could not be processed

Each failure includes:
- `test_name`
- `error`
- `analysis.classification`
- `analysis.details`
- optional structured fields such as `code_fix` or `product_bug_report`

If the service has to fall back to console-only analysis, it creates a synthetic failure entry with:
- `test_name` set to `<job_name>#<build_number>`
- `error` set to `Console-only analysis`

## Common Response Codes

| Status code | When to expect it |
| --- | --- |
| `202` | A `POST /analyze` request was accepted and queued, or `GET /results/{job_id}` is reporting an in-progress job with status `waiting`, `pending`, or `running`. |
| `200` | `GET /results/{job_id}` returned a final stored result with status `completed` or `failed`. |
| `400` | AI provider or AI model is missing when you submit `POST /analyze`. The endpoint validates AI configuration before queuing work. |
| `404` | A results lookup requested an unknown `job_id`. If the Jenkins build itself cannot be found, the async job later moves to `failed` and the stored result carries the error. |
| `422` | The request body failed validation, such as a missing field, bad URL, non-numeric `build_number`, `poll_interval_minutes <= 0`, or `max_wait_minutes < 0`. |

> **Tip:** For automation, treat `GET /results/{job_id}` as the authoritative status endpoint. The initial `202` submission response is just the handoff; the stored result is the durable source of truth.
