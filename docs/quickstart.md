# Quickstart

`jenkins-job-insight` analyzes a Jenkins build, stores the result, and gives you one canonical `result_url` back. API clients use that URL for JSON, and browsers use it for the React status/report UI. This page walks through the shortest path to a first successful request.

## Start the service

The repo includes `docker-compose.yaml`, `.env.example`, and a container image that installs the supported AI CLIs, so Docker Compose is the fastest way to get started.

1. Copy `.env.example` to `.env`.
2. Fill in your Jenkins credentials.
3. Choose one AI provider and configure its authentication.
4. Start the service on port `8000`.

```dotenv
JENKINS_URL=https://jenkins.example.com
JENKINS_USER=your-username
JENKINS_PASSWORD=your-api-token
JENKINS_SSL_VERIFY=true

# Choose AI provider (required): "claude", "gemini", or "cursor"
AI_PROVIDER=claude

# AI model to use (required, applies to any provider)
AI_MODEL=your-model-name

# --- Claude CLI Options ---

# Option 1: Direct API key (simplest)
ANTHROPIC_API_KEY=your-anthropic-api-key
```

```bash
docker compose up -d
curl http://localhost:8000/health
```

The health check returns:

```json
{"status":"healthy"}
```

If you prefer a local process instead of Docker, set the same environment variables, run `uv sync`, build the frontend with `cd frontend && npm install && npm run build`, then start the API with `uv run jenkins-job-insight`. Without `frontend/dist`, the JSON API still works but the browser UI returns `Frontend not built`.

> **Warning:** `JENKINS_URL`, `JENKINS_USER`, and `JENKINS_PASSWORD` no longer have to be server-wide settings. For the quickest first run, put them in `.env`; otherwise, send `jenkins_url`, `jenkins_user`, and `jenkins_password` in the `POST /analyze` body. If your Jenkins uses a self-signed certificate, set `JENKINS_SSL_VERIFY=false` or send `"jenkins_ssl_verify": false` in the request.

## Submit your first request

Use a failed build number for your first run. If the build actually passed, the service returns a completed result with the summary `Build passed successfully. No failures to analyze.` You can also submit a still-running build: with the default `"wait_for_completion": true`, the service watches Jenkins until the build finishes, then starts analysis.

If Jenkins is already configured in `.env`, the smallest useful request body is just `job_name` and `build_number`. `tests_repo_url` is optional, but when you provide it the service clones the repository and gives the AI code context. If Jenkins is not configured on the server, add `jenkins_url`, `jenkins_user`, and `jenkins_password` to this same request body.

> **Note:** The JSON snippets below are taken from the test suite and fixtures, so the field names and shapes match the implementation exactly. Replace the sample values with your own Jenkins job, build number, and model.

This request shape is used directly in the tests:

```json
{
  "job_name": "test",
  "build_number": 123,
  "tests_repo_url": "https://github.com/example/repo"
}
```

Send it to `POST /analyze`:

```bash
curl -X POST "http://localhost:8000/analyze" \
  -H "Content-Type: application/json" \
  -d '{
    "job_name": "test",
    "build_number": 123,
    "tests_repo_url": "https://github.com/example/repo"
  }'
```

`POST /analyze` returns `202 Accepted` right away. The response gives you:

- `status`, which starts as `queued`
- `job_id`, the analysis identifier
- `message`, which tells you to poll the stored result
- `result_url`, the canonical URL for both the JSON result and the browser UI
- `base_url`, which is either the configured `PUBLIC_BASE_URL` or an empty string

A queued response looks like this:

```json
{
  "status": "queued",
  "job_id": "<job_id>",
  "message": "Analysis job queued. Poll /results/<job_id> for status.",
  "base_url": "",
  "result_url": "/results/<job_id>"
}
```

Poll `result_url` until the stored job reaches `completed` or `failed`. If you submit a build that is still running and leave the default `"wait_for_completion": true`, the stored job may move through `waiting` before analysis starts. To opt out for one request, include:

```json
{
  "wait_for_completion": false
}
```

> **Note:** `job_name` can include Jenkins folders, such as `folder/job-name`.

> **Tip:** If `AI_PROVIDER` and `AI_MODEL` are already configured in the server environment, you do not need to send `ai_provider` or `ai_model` in each request.

> **Tip:** If you want absolute `result_url` values in API responses, set `PUBLIC_BASE_URL` on the server. Otherwise the API returns relative URLs such as `/results/<job_id>`.

## Read the returned JSON

A completed analysis result contains the Jenkins build identity, a summary, the AI provider/model used, and one `failures[]` entry per analyzed failure.

The test fixtures include this complete example:

```json
{
  "job_id": "test-job-123",
  "job_name": "my-job",
  "build_number": 123,
  "jenkins_url": "https://jenkins.example.com/job/my-job/123/",
  "status": "completed",
  "summary": "1 failure analyzed: 1 product bug found",
  "ai_provider": "claude",
  "ai_model": "test-model",
  "failures": [
    {
      "test_name": "test_login_success",
      "error": "AssertionError: Expected 200, got 500",
      "analysis": {
        "classification": "PRODUCT BUG",
        "affected_tests": [
          "test_login_success"
        ],
        "details": "The authentication service is returning an error.",
        "product_bug_report": {
          "title": "Login fails with valid credentials",
          "severity": "high",
          "component": "auth",
          "description": "Users cannot log in even with correct username and password",
          "evidence": "Error: Authentication service returned 500"
        }
      }
    }
  ]
}
```

The fields most people read first are:

- `summary`: the shortest human-readable answer.
- `failures[].test_name`: which test failed.
- `failures[].error`: the raw failure text the analysis is based on.
- `failures[].analysis.classification`: `CODE ISSUE` or `PRODUCT BUG`.
- `failures[].analysis.details`: the main explanation.
- `failures[].analysis.code_fix`: present for `CODE ISSUE` results.
- `failures[].analysis.product_bug_report`: present for `PRODUCT BUG` results.
- `failures[].error_signature`: the deduplication key used to group identical failures.
- `child_job_analyses`: present when a pipeline failed because child jobs failed.

If the summary says something like `2 failure(s) analyzed (1 unique error type(s))`, multiple failing tests shared the same underlying error and were analyzed as one root cause.

## Use the `result_url`

The returned `result_url` is the stored-result endpoint you poll until the job finishes:

```bash
curl "http://localhost:8000/results/<job_id>"
```

This endpoint returns a wrapper object with top-level metadata such as:

- `job_id`
- `jenkins_url`
- `status`
- `created_at`
- `base_url`
- `result_url`

Once analysis is complete, the full analysis JSON appears inside the `result` field. `GET /results/{job_id}` is the shape to expect when you poll: the wrapper stays at the top level, and the completed analysis lives under `result`.

The `status` value can move through this lifecycle:

- `waiting`: the request was accepted and the service is waiting for Jenkins to finish the build
- `pending`: the request was accepted and queued for analysis
- `running`: analysis is in progress
- `completed`: the final JSON is ready
- `failed`: the analysis did not finish successfully

While the job is still in progress, `GET /results/{job_id}` returns HTTP `202`. Keep polling the same `result_url` until the status becomes `completed` or `failed`.

> **Tip:** `result_url` is absolute only when `PUBLIC_BASE_URL` is configured. Otherwise the API intentionally returns relative URLs such as `/results/<job_id>` and does not trust forwarded host headers.

## Open the report page

Open the same `result_url` in a browser. There is no separate `html_report_url`: the React web app uses `/results/{job_id}` for completed analyses and `/status/{job_id}` while work is still in progress.

A few details matter on a first run:

- Once you are registered, a browser request to `/results/{job_id}` redirects to `/status/{job_id}` whenever the stored job is `pending`, `waiting`, or `running`
- The status page polls automatically every 10 seconds and returns you to `/results/{job_id}` when the job completes
- API clients can call that same `result_url` and get JSON instead of HTML

Once the analysis is complete, the report page shows:

- the job name, build number, status, AI provider/model, and failure counts
- grouped failures and child-job analyses
- comments and reviewed toggles
- classification overrides
- `Open GitHub Issue` or `Open Jira Bug` actions when those integrations are configured

> **Warning:** If opening the report sends you to `/register`, enter a username once and reopen the result. The username is stored as a browser cookie; no account or password is required.

> **Note:** In the Docker Compose setup, `./data` is mounted to `/data`, and the SQLite database lives at `/data/results.db`.

## Use the CLI instead

The repo also ships a `jji` CLI that talks to the same API.

Set the server URL once:

```bash
export JJI_SERVER=http://localhost:8000
```

Queue an analysis:

```bash
jji analyze --job-name test --build-number 123
```

Check its status:

```bash
jji status <job_id>
```

Show the stored result as JSON:

```bash
jji results show <job_id> --full --json
```

If Jenkins is not configured on the server, add `--jenkins-url`, `--jenkins-user`, and `--jenkins-password` to the `jji analyze` command. If you do not want to export `JJI_SERVER`, pass `--server http://localhost:8000` on each command instead. If you already have `~/.config/jji/config.toml`, `jji` can use the default server profile from that file.

## What a first successful run looks like

A good first run usually ends with three things:

1. A queued response from `POST /analyze` containing `job_id` and `result_url`.
2. A completed JSON result at `GET /results/{job_id}`.
3. A readable browser report at the same `result_url`, with `/status/{job_id}` handling the in-progress view until the report is ready.

Once you have that working, the next natural step is to open `http://localhost:8000/` after registering once to browse the dashboard, or use `POST /analyze-failures` if you already have raw failures or JUnit XML instead of a Jenkins build.
