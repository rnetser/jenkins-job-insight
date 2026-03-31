# Configuration Reference

`jenkins-job-insight` reads service configuration from environment variables and automatically loads a local `.env` file from the project root. Many of the same settings can also be overridden per request on `/analyze` and `/analyze-failures`.

```16:20:src/jenkins_job_insight/config.py
model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
)
```

This page covers the FastAPI service and its analysis request overrides. CLI helper variables such as `JJI_SERVER` are client-side settings and are intentionally out of scope here.

> **Note:** Restart the service after changing environment variables. `get_settings()` is cached, and `AI_PROVIDER` / `AI_MODEL` are read when `src/jenkins_job_insight/main.py` is imported.

## Configuration Order

For request-tunable analysis settings, configuration resolves in this order:

1. Request body override, when that field is supported for the endpoint.
2. Environment variable.
3. Code default, if one exists.

Deployment-only settings do not have request-body equivalents. In this page, the main examples are `PORT`, `DB_PATH`, `DEBUG`, `LOG_LEVEL`, `PUBLIC_BASE_URL`, `ENABLE_GITHUB_ISSUES`, and `JJI_ENCRYPTION_KEY`.

The merge path in `src/jenkins_job_insight/main.py` applies only non-`None` request values:

```532:607:src/jenkins_job_insight/main.py
def _merge_settings(body: BaseAnalysisRequest, settings: Settings) -> Settings:
    """Create a copy of settings with per-request overrides applied."""
    overrides: dict = {}
    # ... direct fields omitted ...
    for field in direct_fields:
        value = getattr(body, field, None)
        if value is not None:
            overrides[field] = value
    # ... Jenkins-specific handling omitted ...
    if overrides:
        merged_data = settings.model_dump(mode="python") | overrides
        return Settings.model_validate(merged_data)
    return settings
```

`enable_jira` has one extra fallback: if you do not set `enable_jira` or `ENABLE_JIRA`, the app auto-enables Jira enrichment only when Jira is fully configured, including `JIRA_PROJECT_KEY`.

> **Warning:** Request overrides apply only to the current analysis call. They do not become new server defaults, and server-level issue creation still uses deployment config rather than a previous analysis request.

## Quick Start

The shipped environment template starts with Jenkins and AI basics:

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

> **Note:** `.env.example` focuses on the most common settings and shows Jenkins values as deployment defaults. They are no longer mandatory for `/analyze-failures`. The code also supports `WAIT_FOR_COMPLETION`, `POLL_INTERVAL_MINUTES`, `MAX_WAIT_MINUTES`, `PUBLIC_BASE_URL`, `ENABLE_JIRA`, `GET_JOB_ARTIFACTS`, `JENKINS_ARTIFACTS_MAX_SIZE_MB`, `JENKINS_ARTIFACTS_CONTEXT_LINES`, `PORT`, `DB_PATH`, and `JJI_ENCRYPTION_KEY`.

## Runtime And Startup

These settings are server-only. They do not have request-body equivalents.

| Environment variable | Request override | Default | What it does |
| --- | --- | --- | --- |
| `PORT` | — | `8000` | HTTP port for the service. Must be an integer from `1` to `65535`. |
| `DB_PATH` | — | `/data/results.db` | SQLite database path for stored analysis results, comments, reviews, and waiting-job state. |
| `DEBUG` | — | `false` | Enables `uvicorn` reload mode only when you launch the app through the `jenkins-job-insight` console script. It does not change log verbosity. |
| `LOG_LEVEL` | — | `INFO` | Application log level used across modules. Expected values are `DEBUG`, `INFO`, `WARNING`, and `ERROR`. |
| `PUBLIC_BASE_URL` | — | none | Trusted external origin used to build absolute result links and tracker/report links. When unset, the API returns relative URLs and ignores request forwarding headers. |
| `JJI_ENCRYPTION_KEY` | — | auto-generated file key | Secret used to encrypt stored sensitive request parameters. In production, set it explicitly; otherwise the app creates a local key file under `$XDG_DATA_HOME/jji/.encryption_key` or `~/.local/share/jji/.encryption_key`. |

`PUBLIC_BASE_URL` is intentionally trusted configuration, not something derived from incoming requests:

```142:160:src/jenkins_job_insight/main.py
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

> **Note:** Sensitive request fields that are persisted for waiting-job resumption are encrypted at rest and removed from later API responses. That includes `jenkins_user`, `jenkins_password`, `jira_email`, `jira_api_token`, `jira_pat`, and `github_token`.

If you use the provided Compose file, `.env` is wired in automatically and `./data` is mounted to `/data`, which matches the default `DB_PATH`.

> **Tip:** In the provided container image, the app is started with `uvicorn` directly, not through `jenkins-job-insight`. That means `DEBUG=true` does not automatically turn on reload in the stock Docker setup.

## Jenkins

The request overrides in this section belong to `AnalyzeRequest`, so they are available on `/analyze` only.

| Environment variable | Request override | Default | What it does |
| --- | --- | --- | --- |
| `JENKINS_URL` | `jenkins_url` | none | Base Jenkins URL used for build info, console output, test reports, artifacts, and generated build links. |
| `JENKINS_USER` | `jenkins_user` | none | Jenkins username. |
| `JENKINS_PASSWORD` | `jenkins_password` | none | Jenkins password or API token. |
| `JENKINS_SSL_VERIFY` | `jenkins_ssl_verify` | `true` | Controls TLS certificate verification for Jenkins API calls. |
| `WAIT_FOR_COMPLETION` | `wait_for_completion` | `true` | Wait for a running Jenkins build to finish before starting analysis. |
| `POLL_INTERVAL_MINUTES` | `poll_interval_minutes` | `2` | Minutes between Jenkins status polls while waiting for completion. |
| `MAX_WAIT_MINUTES` | `max_wait_minutes` | `0` | Maximum minutes to wait for completion. `0` means no limit. |
| `GET_JOB_ARTIFACTS` | `get_job_artifacts` | `true` | Downloads build artifacts and feeds extracted error/warning context into AI analysis. |
| `JENKINS_ARTIFACTS_MAX_SIZE_MB` | `jenkins_artifacts_max_size_mb` | `500` | Maximum size per artifact download, in MB. Oversized artifacts are skipped. |
| `JENKINS_ARTIFACTS_CONTEXT_LINES` | `jenkins_artifacts_context_lines` | `200` | Maximum number of artifact-context lines included in the AI prompt. This limits prompt context, not the number of files downloaded. |

`JENKINS_URL`, `JENKINS_USER`, and `JENKINS_PASSWORD` are now optional server defaults. You can leave them unset for deployments that only use `/analyze-failures`, or pass them per request when calling `/analyze`.

The wait-related request fields only override environment defaults when the caller actually sends them:

```591:602:src/jenkins_job_insight/main.py
# Monitoring fields have non-None defaults in the model.  Only
# apply them as overrides when explicitly sent by the caller
for field in (
    "wait_for_completion",
    "poll_interval_minutes",
    "max_wait_minutes",
):
    if field in body.model_fields_set:
        overrides[field] = getattr(body, field)
```

> **Note:** Waiting controls matter only for Jenkins-backed `/analyze` requests. When `wait_for_completion` is enabled and Jenkins is configured, the job enters a `waiting` state until the build finishes or `max_wait_minutes` is reached.

## AI

`AI_PROVIDER`, `AI_MODEL`, and `AI_CLI_TIMEOUT` can be set globally or overridden per request on `/analyze` and `/analyze-failures`. `ai_provider` and `ai_model` are also accepted on the preview issue endpoints for generated issue text.

| Environment variable | Request override | Default | What it does |
| --- | --- | --- | --- |
| `AI_PROVIDER` | `ai_provider` | none | AI provider. Valid analysis values are `claude`, `gemini`, or `cursor`. |
| `AI_MODEL` | `ai_model` | none | Model name passed to the chosen AI CLI. |
| `AI_CLI_TIMEOUT` | `ai_cli_timeout` | `10` | Timeout in minutes for AI CLI calls. This also applies to AI-assisted Jira relevance filtering. |

> **Note:** The service can start without `AI_PROVIDER` or `AI_MODEL`, but analysis endpoints return `400` until you set them in the environment or pass them in the request body.

The analysis request models also support `raw_prompt` for one-off extra AI instructions. It has no matching environment-variable equivalent.

### Provider Authentication

These variables are not request-overridable. They are consumed by the external provider CLIs.

```21:50:.env.example
# --- Claude CLI Options ---

# Option 1: Direct API key (simplest)
ANTHROPIC_API_KEY=your-anthropic-api-key

# Option 2: Vertex AI authentication
# CLAUDE_CODE_USE_VERTEX=1
# CLOUD_ML_REGION=us-east5
# ANTHROPIC_VERTEX_PROJECT_ID=your-project-id

# --- Gemini CLI Options ---

# Option 1: API key
GEMINI_API_KEY=your-gemini-api-key

# Option 2: OAuth (run: gemini auth login)
# No env vars needed for OAuth

# --- Cursor Agent CLI Options ---

# Choose ONE of the following authentication methods:

# API key
# CURSOR_API_KEY=your-cursor-api-key

# --- AI CLI Timeout ---

# Timeout for AI CLI calls in minutes (default: 10)
# Increase for slower models like gpt-5.2
# AI_CLI_TIMEOUT=10
```

| Environment variable | Request override | Default | What it does |
| --- | --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | none | Claude CLI direct API key. |
| `CLAUDE_CODE_USE_VERTEX` | — | none | Tells the Claude CLI to use Vertex AI instead of direct API-key auth. |
| `CLOUD_ML_REGION` | — | none in app code | Vertex AI region for Claude. The example Compose file uses `us-east5`. |
| `ANTHROPIC_VERTEX_PROJECT_ID` | — | none | GCP project ID for Claude via Vertex AI. |
| `GEMINI_API_KEY` | — | none | Gemini CLI API key. |
| `CURSOR_API_KEY` | — | none | Cursor Agent CLI API key. |

> **Tip:** Gemini also supports OAuth login with `gemini auth login`, so `GEMINI_API_KEY` is optional if you use that flow.

## Callbacks

The current codebase no longer exposes callback delivery settings. `CALLBACK_URL` and `CALLBACK_HEADERS` are not part of the active service configuration, and `/analyze` now returns a `job_id` plus `result_url` for polling instead.

```970:976:src/jenkins_job_insight/main.py
response: dict = {
    "status": "queued",
    "job_id": job_id,
    "message": message,
}
return _attach_result_links(response, base_url, job_id)
```

Poll `GET /results/{job_id}` or follow `result_url` instead of configuring a callback webhook.

## Repository And GitHub

`TESTS_REPO_URL` and `GITHUB_TOKEN` can be passed as analysis request overrides on both analysis endpoints, but GitHub issue workflows remain server-level operations.

| Environment variable | Request override | Default | What it does |
| --- | --- | --- | --- |
| `TESTS_REPO_URL` | `tests_repo_url` | none | Repository to clone so the AI can inspect real code while analyzing failures. If cloning fails, analysis still continues without repo context. |
| `GITHUB_TOKEN` | `github_token` | none | GitHub token used when the server talks to GitHub for private-repo status lookups, duplicate search, and issue creation. The request override exists on analysis requests, but issue workflows use deployment settings rather than a previous analysis request override. |
| `ENABLE_GITHUB_ISSUES` | — | auto-detect | Explicitly enables or disables GitHub issue creation. When unset, the server enables GitHub issues only when both `TESTS_REPO_URL` and `GITHUB_TOKEN` are configured. |

For GitHub issue creation from the app, you need both `TESTS_REPO_URL` and `GITHUB_TOKEN` configured at the server level. `ENABLE_GITHUB_ISSUES` is only a feature gate: `true` does not bypass missing repo or token, and `false` disables the feature even when both are present.

> **Warning:** GitHub issue preview/create does not reuse an earlier analysis request's `tests_repo_url` or `github_token`. Configure those on the server if you want issue workflows to work consistently.

```409:413:src/jenkins_job_insight/models.py
# NOTE: Preview/create request models intentionally do NOT inherit
# BaseAnalysisRequest. These are server-level operations that use deployment
# config (GITHUB_TOKEN, TESTS_REPO_URL, Jira credentials), not per-request
# analysis overrides. The caller identifies *which* failure to act on, but
# the credentials and target repos are fixed at the server level.
```

## Jira

Jira enrichment can run on both analysis endpoints. If you do not set `ENABLE_JIRA` or `enable_jira`, the app auto-enables Jira enrichment only when Jira is fully configured: URL, credentials, and `JIRA_PROJECT_KEY`.

| Environment variable | Request override | Default | What it does |
| --- | --- | --- | --- |
| `ENABLE_JIRA` | `enable_jira` | auto-detect | Explicitly turns Jira enrichment on or off. |
| `JIRA_URL` | `jira_url` | none | Base Jira URL. |
| `JIRA_EMAIL` | `jira_email` | none | Email address used for Jira Cloud Basic auth. By itself it does not enable Cloud mode. |
| `JIRA_API_TOKEN` | `jira_api_token` | none | Jira Cloud API token. With `JIRA_EMAIL`, this enables Cloud mode. Without `JIRA_EMAIL`, it is a Server/Data Center fallback credential. |
| `JIRA_PAT` | `jira_pat` | none | Personal access token for Jira Server/Data Center. |
| `JIRA_PROJECT_KEY` | `jira_project_key` | none | Required to enable Jira integration. It scopes searches and is also used as the target `project.key` when creating Jira bugs. |
| `JIRA_SSL_VERIFY` | `jira_ssl_verify` | `true` | Controls TLS certificate verification for Jira HTTP requests. |
| `JIRA_MAX_RESULTS` | `jira_max_results` | `5` | Maximum number of Jira candidates returned per search before AI filtering. |

Cloud and Server/Data Center authentication are chosen by configuration, not by URL pattern:

- Cloud mode: set `JIRA_EMAIL` and `JIRA_API_TOKEN`.
- Server/Data Center mode: omit `JIRA_EMAIL`; the code prefers `JIRA_PAT` and falls back to `JIRA_API_TOKEN`.
- `JIRA_EMAIL` plus `JIRA_PAT` stays in Server/Data Center mode. It does not switch to Cloud mode.

```153:188:src/jenkins_job_insight/config.py
def _resolve_jira_auth(settings: Settings) -> tuple[bool, str]:
    """Resolve Jira authentication mode and token value."""
    has_api_token = bool(
        settings.jira_api_token and settings.jira_api_token.get_secret_value()
    )
    has_pat = bool(settings.jira_pat and settings.jira_pat.get_secret_value())
    has_email = bool(settings.jira_email)

    is_cloud = has_email and has_api_token

    # Cloud: jira_api_token only
    # Server/DC: prefer PAT, fall back to API token
```

> **Tip:** Set exactly one Jira credential path for the deployment you use. That makes it much easier to predict whether the server will talk to Jira in Cloud or Server/Data Center mode.

> **Warning:** `ENABLE_JIRA=true` does not bypass missing Jira configuration. If `JIRA_URL`, credentials, or `JIRA_PROJECT_KEY` are missing, Jira still stays effectively disabled and the server logs a warning.

## SSL And Certificates

Only two environment variables control SSL verification directly: `JENKINS_SSL_VERIFY` and `JIRA_SSL_VERIFY`. Both default to `true`.

- `JENKINS_SSL_VERIFY=false` affects Jenkins API traffic, including build polling while waiting for completion, console requests, test report lookups, and artifact downloads.
- `JIRA_SSL_VERIFY=false` affects Jira search, duplicate detection, Jira ticket-status lookups in comment enrichment, and Jira bug creation.
- GitHub API calls use the default TLS verification behavior of `httpx`; there is no dedicated GitHub SSL toggle.
- Provider CLI TLS behavior is managed by the provider CLIs themselves, not by this application.

> **Warning:** Setting either SSL flag to `false` disables certificate verification for that integration. Use this only for controlled internal environments where you understand the risk.

## Timeouts

Two analysis-time controls are now user-configurable: `AI_CLI_TIMEOUT` and the Jenkins wait settings. `WAIT_FOR_COMPLETION` turns the wait loop on or off, while `POLL_INTERVAL_MINUTES` and `MAX_WAIT_MINUTES` tune that loop when it is enabled.

| Operation | Environment variable | Request override | Value | Notes |
| --- | --- | --- | --- | --- |
| AI CLI calls | `AI_CLI_TIMEOUT` | `ai_cli_timeout` | `10` minutes by default | Applies to failure analysis and AI-based Jira match filtering. |
| Jenkins wait poll interval | `POLL_INTERVAL_MINUTES` | `poll_interval_minutes` | `2` minutes by default | Delay between Jenkins status checks when waiting for a build to finish. |
| Jenkins wait deadline | `MAX_WAIT_MINUTES` | `max_wait_minutes` | `0` minutes by default | `0` means no deadline; positive values fail the job if the build does not finish in time. |
| Comment enrichment and GitHub duplicate search | — | — | `10` seconds | Fixed in code for GitHub PR/issue status lookups, Jira ticket status lookups, and GitHub duplicate search. |
| Jira search client | — | — | `30` seconds | Fixed in code. |
| GitHub and Jira issue creation | — | — | `15` seconds | Fixed in code. |
| Jenkins artifact download | — | — | `60` seconds per request | Fixed in code. |

The Jenkins wait deadline is enforced directly in the polling loop:

```684:731:src/jenkins_job_insight/main.py
if max_wait_minutes > 0:
    deadline: float | None = _time.monotonic() + max_wait_minutes * 60
else:
    deadline = None  # No limit
# ... polling omitted ...
error_msg = (
    f"Timed out waiting for Jenkins job {job_name} #{build_number} "
    f"after {max_wait_minutes} minutes"
)
```

> **Note:** The current code does not expose separate environment variables or request fields for tracker HTTP timeouts or Jenkins artifact download timeouts.
