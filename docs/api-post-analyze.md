# POST /analyze

Use `POST /analyze` to submit a Jenkins build for analysis by job name and build number. The service fetches the Jenkins build, analyzes failing tests, follows failed child jobs in pipelines, optionally clones your test repository for code context, and stores the result so you can poll it later or open it in the web UI.

This endpoint is asynchronous and returns immediately with a `job_id`. Use `wait_for_completion`, `poll_interval_minutes`, and `max_wait_minutes` in the JSON body to control Jenkins build monitoring before analysis starts; the HTTP response is still `202 Accepted`.

## Query Parameters

`POST /analyze` does not use query parameters in the current API contract. Submit all options in the JSON body.

> **Note:** The older `?sync=true` pattern is not part of the current `POST /analyze` schema. The endpoint always queues work and returns `202 Accepted`.

## Request Body

Only `job_name` and `build_number` are required. Everything else is optional and acts as a per-request override of server defaults.

### Required Fields

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `job_name` | string | Yes | Jenkins job name. It can include folders, such as `folder/job-name`. |
| `build_number` | integer | Yes | Jenkins build number to analyze. |

### Core Analysis Fields

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `tests_repo_url` | URL | No | Repository to clone for code context during analysis. |
| `ai_provider` | string | No | AI provider for this request. Valid values are `claude`, `gemini`, and `cursor`. |
| `ai_model` | string | No | AI model for this request. |
| `ai_cli_timeout` | integer | No | AI CLI timeout in minutes for this request. Must be greater than `0`. |
| `raw_prompt` | string | No | Additional instructions appended to the AI analysis prompt. |
| `enable_jira` | boolean | No | Enable or disable Jira matching for this request. If omitted, the server uses request overrides, then environment defaults, then Jira auto-detection from configured credentials. |

### Callback Fields

`POST /analyze` does not accept `callback_url` or `callback_headers` in the current request schema. Use the returned `job_id` with `GET /results/{job_id}` or open `/status/{job_id}` in the web UI instead.

### Jenkins, Monitoring, And Artifact Overrides

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `wait_for_completion` | boolean | No | Wait for a running Jenkins build to finish before analysis starts. Defaults to `true`. |
| `poll_interval_minutes` | integer | No | Minutes between Jenkins status polls while waiting. Must be greater than `0`. The default is `2`. |
| `max_wait_minutes` | integer | No | Maximum minutes to wait for build completion. `0` means no limit. |
| `jenkins_url` | string | No | Override the Jenkins base URL for this request. |
| `jenkins_user` | string | No | Override the Jenkins username. |
| `jenkins_password` | string | No | Override the Jenkins password or API token. |
| `jenkins_ssl_verify` | boolean | No | Override Jenkins SSL verification. |
| `get_job_artifacts` | boolean | No | Enable or disable build artifact download for AI context. The server default is `true`. |
| `jenkins_artifacts_max_size_mb` | integer | No | Maximum artifact size to process for this request. Must be greater than `0`. |
| `jenkins_artifacts_context_lines` | integer | No | Maximum artifact context lines passed into analysis. Must be greater than `0`. |

### Jira And GitHub Overrides

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `jira_url` | string | No | Jira base URL for this request. |
| `jira_email` | string | No | Jira Cloud email address. |
| `jira_api_token` | string | No | Jira Cloud API token used with `jira_email` for Basic auth. |
| `jira_pat` | string | No | Jira Server/Data Center personal access token used for Bearer auth. |
| `jira_project_key` | string | No | Jira project key to scope searches. It must be configured if you want Jira matching enabled. |
| `jira_ssl_verify` | boolean | No | Override Jira SSL verification. |
| `jira_max_results` | integer | No | Maximum Jira matches to retrieve. Must be greater than `0`. |
| `github_token` | string | No | GitHub token used for private-repo PR status enrichment in comments and for GitHub issue creation. |

> **Note:** `tests_repo_url` is optional. If you omit it and the server has no default `TESTS_REPO_URL`, analysis still runs, but the AI will not have repository code context.

## Example Request Bodies

The examples below are taken directly from the repository's tests and README.

### Queued Request With Explicit AI Config

```json
{
  "job_name": "test",
  "build_number": 123,
  "tests_repo_url": "https://github.com/example/repo",
  "ai_provider": "claude",
  "ai_model": "test-model"
}
```

### Request With Jenkins Monitoring Controls

```json
{
  "job_name": "my-job",
  "build_number": 42,
  "wait_for_completion": true,
  "poll_interval_minutes": 2,
  "max_wait_minutes": 0
}
```

> **Note:** The monitoring example matches the repository README and assumes AI defaults are already configured on the server. If they are not, include `ai_provider` and `ai_model` in the request body too.

### CLI Equivalents

```bash
jji analyze --job-name my-job --build-number 42
jji analyze --job-name my-job --build-number 42 --no-wait
jji analyze --job-name my-job --build-number 27 --provider claude --model opus-4 --jira
```

## Async Behavior

`POST /analyze` always returns quickly with `202 Accepted` and a server-generated `job_id`. Use that `job_id` to poll `GET /results/{job_id}` or open the matching route in the web UI.

The lifecycle is:

1. The server validates AI configuration before queueing the job.
2. It creates a `job_id`.
3. It stores an initial row with status `waiting` when Jenkins monitoring is enabled and Jenkins is configured, or `pending` otherwise.
4. It returns a submit response with `status` set to `queued`.
5. The background task moves the job to `running` after any Jenkins wait completes.
6. The stored job ends as `completed` or `failed`.

> **Note:** `wait_for_completion` controls whether the service waits for the Jenkins build to finish before analysis starts. It does not turn `POST /analyze` into a synchronous request.

## Background Job Behavior

Async analysis still uses FastAPI background tasks inside the application process. It is simple and fast, but it is not a separate external worker queue.

> **Warning:** Restart behavior now depends on the in-flight status. `pending` and `running` jobs are marked `failed` on startup because their background task is gone. `waiting` jobs are resumed on startup when the stored request contains enough information to rebuild Jenkins monitoring.

Polling works immediately because the job row is written before the background task starts:

- `GET /results/{job_id}` returns the current stored JSON state.
- Opening `GET /results/{job_id}` in a browser serves the React UI. If the job is still in progress, the server redirects the browser to `/status/{job_id}` until the result is ready.

> **Tip:** Set `PUBLIC_BASE_URL` if you want absolute `result_url` values in API responses. When it is unset, the API returns relative paths such as `/results/{job_id}` and ignores `Host` and `X-Forwarded-*` headers.

## Response Schema

### `202 Accepted` Submit Response

This is the response you get from `POST /analyze`.

| Field | Type | Description |
| --- | --- | --- |
| `status` | string | Always `queued` for the initial submit response. |
| `job_id` | string | Server-generated job identifier. |
| `message` | string | Human-readable queue message. |
| `base_url` | string | Trusted public base URL from `PUBLIC_BASE_URL`, or an empty string when the server is returning relative links. |
| `result_url` | string | URL for `GET /results/{job_id}`. It is absolute only when `PUBLIC_BASE_URL` is configured. |

### Polling Response From `GET /results/{job_id}`

This is the JSON shape you poll after submission.

| Field | Type | Description |
| --- | --- | --- |
| `job_id` | string | Job identifier. |
| `jenkins_url` | string | Jenkins build URL stored for the analysis. |
| `status` | string | Current stored state. Possible values are `pending`, `waiting`, `running`, `completed`, and `failed`. |
| `result` | object or `null` | Minimal stored metadata while queued, the analysis payload when available, or an error payload when the job fails. |
| `created_at` | string | Timestamp when the job row was created. |
| `analysis_started_at` | string or `null` | Timestamp when analysis execution started, if it has started. |
| `completed_at` | string or `null` | Timestamp when the job reached `completed` or `failed`, if available. |
| `base_url` | string | Trusted public base URL from `PUBLIC_BASE_URL`, or an empty string when links are relative. |
| `result_url` | string | URL for this same result resource. |
| `capabilities` | object | Server-advertised UI capabilities for this result, currently `github_issues` and `jira_bugs`. |

### Analysis Payload In `result`

When `result` contains a full analysis payload, it uses the `AnalysisResult` shape below.

| Field | Type | Description |
| --- | --- | --- |
| `job_id` | string | Unique analysis job ID. |
| `job_name` | string | Jenkins job name. |
| `build_number` | integer | Jenkins build number. |
| `jenkins_url` | URL or `null` | URL of the analyzed Jenkins build. |
| `status` | string | Status stored inside the analysis payload. Completed jobs normally use `completed`; some analysis-level failures can use `failed`. |
| `summary` | string | Summary of what was analyzed. |
| `ai_provider` | string | AI provider used for the run. |
| `ai_model` | string | AI model used for the run. |
| `failures` | array | Top-level failed tests and their analyses. |
| `child_job_analyses` | array | Failed child jobs for pipeline-style builds. |

> **Note:** `base_url` and `result_url` are wrapper fields on the `GET /results/{job_id}` response. They are not part of the nested analysis payload.

> **Note:** A failed stored job can also return a smaller error object in `result`, typically with `job_name`, `build_number`, and `error`, for cases such as wait timeouts or startup recovery failures.

> **Note:** A successful Jenkins build still returns a completed analysis result. In that case, `summary` is `Build passed successfully. No failures to analyze.` and `failures` is empty.

### `failures[]`

Each item in `failures` has this shape:

| Field | Type | Description |
| --- | --- | --- |
| `test_name` | string | Name of the failed test. |
| `error` | string | Error message or exception. |
| `error_signature` | string | SHA-256 signature of the error and stack trace, used for deduplication. |
| `analysis` | object | Structured AI analysis for this failure. |

### `failures[].analysis`

| Field | Type | Description |
| --- | --- | --- |
| `classification` | string | Normally `CODE ISSUE` or `PRODUCT BUG`. |
| `affected_tests` | array of strings | All tests believed to be affected by the same issue. |
| `details` | string | Human-readable root-cause analysis. |
| `artifacts_evidence` | string | Verbatim evidence lines from build artifacts when available. |
| `code_fix` | object or omitted | Present for `CODE ISSUE` analyses. |
| `product_bug_report` | object or omitted | Present for `PRODUCT BUG` analyses. |

`code_fix` contains:

- `file`
- `line`
- `change`

`product_bug_report` contains:

- `title`
- `severity`
- `component`
- `description`
- `evidence`
- `jira_search_keywords`
- `jira_matches`

Each item in `jira_matches` contains:

- `key`
- `summary`
- `status`
- `priority`
- `url`
- `score`

> **Note:** `code_fix` and `product_bug_report` are mutually exclusive. A failure gets one or the other, not both.

### `child_job_analyses[]`

Pipeline jobs can return failed child jobs recursively.

| Field | Type | Description |
| --- | --- | --- |
| `job_name` | string | Child job name. |
| `build_number` | integer | Child build number. |
| `jenkins_url` | string or `null` | Child build URL. |
| `summary` | string or `null` | Summary for the child job. |
| `failures` | array | Direct failures in that child job. |
| `failed_children` | array | Nested child jobs below this one. |
| `note` | string or `null` | Extra note, such as max-depth protection or fetch failures. |

## Callbacks

`POST /analyze` no longer supports callback delivery fields in the request body.

> **Note:** Use the returned `job_id` with `GET /results/{job_id}` for machine-readable polling, or open `/status/{job_id}` and `/results/{job_id}` in the web UI for human-readable progress and results.

## Configuration Snippets

The repository ships with environment-based defaults that `POST /analyze` can use when the request body omits fields.

### AI Defaults From `.env.example`

```dotenv
AI_PROVIDER=claude

# AI model to use (required, applies to any provider)
# Can also be set per-request in webhook body
AI_MODEL=your-model-name
```

### Repository Defaults From `.env.example`

```dotenv
# Tests repository URL
# TESTS_REPO_URL=https://github.com/org/test-repo
```

### Waiting Defaults From `config.example.toml`

```toml
# Monitoring
wait_for_completion = true
poll_interval_minutes = 2
max_wait_minutes = 0  # 0 = no limit (wait forever)
```

> **Note:** Set `PUBLIC_BASE_URL` on the server if you want absolute `result_url` values in API responses. When it is unset, the API returns relative paths.

> **Note:** Jenkins connection settings can come from environment variables or from the `POST /analyze` request body for this call.

## Common Status Codes

| HTTP Status | When You See It |
| --- | --- |
| `200 OK` | `GET /results/{job_id}` returned a completed or failed stored result. |
| `202 Accepted` | `POST /analyze` was accepted and queued. `GET /results/{job_id}` also returns `202` while the job is still `pending`, `waiting`, or `running`. |
| `400 Bad Request` | No AI provider or AI model could be resolved from the request body or server defaults. |
| `404 Not Found` | `GET /results/{job_id}` was called with an unknown `job_id`. |
| `422 Unprocessable Entity` | Request validation failed, such as a missing required field, invalid URL, or wrong type. |

> **Note:** Jenkins lookup, authentication, wait-timeout, and similar analysis-time failures are stored on the job with `status` set to `failed` instead of changing the initial `POST /analyze` submit response.

> **Tip:** The live machine-readable schema is also available from the app itself at `/docs` and `/openapi.json`.
