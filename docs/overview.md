# Overview

`jenkins-job-insight` is a FastAPI service for turning failed CI runs into structured, reviewable analysis. You can point it at a Jenkins build or send raw failure data or JUnit XML directly. The service gathers the available context, calls a configured AI CLI, stores the result, and exposes it through a REST API, a built-in React web UI, and the `jji` CLI.

Its main job is simple: help you answer "what failed, why did it fail, and what should happen next?" In practice that means classifying failures, preserving supporting evidence, grouping repeated failures by root cause, keeping history, and helping humans review or file follow-up issues.

> **Note:** `jenkins-job-insight` uses AI CLIs, not vendor SDKs. The selected provider CLI must be installed and authenticated in the environment where the service runs.

## Interfaces

| Interface | What you use it for |
| --- | --- |
| REST API | Queue Jenkins analyses, fetch JSON results, submit raw failures or JUnit XML, and drive review or issue workflows programmatically |
| React web UI | Register a username at `/register`, browse recent jobs at `/` or `/dashboard`, follow queued work at `/status/{job_id}`, open reports at `/results/{job_id}`, and explore recurring failures at `/history` |
| `jji` CLI | Analyze Jenkins jobs, check status, browse results, search history, manage comments and classifications, and preview or create tracker issues |

The API also exposes interactive docs at `/docs` and an OpenAPI schema at `/openapi.json`.

On the CLI side, the project ships commands such as `analyze`, `status`, `results`, `history`, `comments`, `classify`, `preview-issue`, `create-issue`, `override-classification`, and `ai-configs`.

## Main inputs

The service can start from either of these inputs:

- A Jenkins job reference: `job_name` plus `build_number`
- Direct failure data: either a `failures` list or a `raw_xml` JUnit document

Depending on what you provide and what Jenkins exposes, the service can also use:

- Structured Jenkins test reports
- Jenkins console output
- Jenkins build artifacts
- An optional cloned test repository from `tests_repo_url`
- Optional Jenkins monitoring controls such as `wait_for_completion`, `poll_interval_minutes`, and `max_wait_minutes`
- Optional Jira and GitHub configuration for enrichment and issue workflows

A Jenkins analysis request can include monitoring controls like this:

```json
{
  "job_name": "my-job",
  "build_number": 42,
  "wait_for_completion": true,
  "poll_interval_minutes": 2,
  "max_wait_minutes": 0
}
```

A direct failure analysis request can also enable peer analysis. The API tests use this request shape:

```json
{
  "failures": [
    {
      "test_name": "test_foo",
      "error_message": "assert False",
      "stack_trace": "File test.py, line 10"
    }
  ],
  "ai_provider": "claude",
  "ai_model": "test-model",
  "peer_ai_configs": [
    { "ai_provider": "gemini", "ai_model": "pro" }
  ],
  "peer_analysis_max_rounds": 7
}
```

> **Note:** Most analysis settings can be defined globally with environment variables and overridden per request. That includes the AI provider and model, peer analysis settings, Jira settings, AI timeouts, artifact handling, Jenkins monitoring controls, and the optional test repository URL. Jenkins connection settings can also be supplied per request instead of at server startup.

## Main outputs

Every analysis gets a `job_id` and is stored for later retrieval. For Jenkins jobs, `POST /analyze` returns a queued response by default and includes a canonical `result_url` pointing at `/results/{job_id}`. That same route serves JSON to API clients and the React report UI to browsers. Jobs move through `waiting`, `pending`, `running`, `completed`, or `failed`.

The core result is structured rather than free-form text. Each failure still carries structured `analysis`, and when peer analysis is enabled it can also include the debate trail from the participating AIs. The current model in `src/jenkins_job_insight/models.py` is:

```python
class FailureAnalysis(BaseModel):
    test_name: str = Field(description="Name of the failed test")
    error: str = Field(description="Error message or exception")
    analysis: AnalysisDetail = Field(description="Structured AI analysis output")
    error_signature: str = Field(
        default="",
        description="SHA-256 hash of error + stack trace for deduplication",
    )
    peer_debate: PeerDebate | None = Field(
        default=None,
        description="Peer debate trail (present only when peer analysis was used)",
    )
```

In practice, that means a result can include:

- A top-level summary
- Per-failure classification as `CODE ISSUE` or `PRODUCT BUG`
- Supporting details and verbatim artifact evidence
- A `code_fix` suggestion for code issues
- A structured `product_bug_report` for product bugs
- An `error_signature` used to group identical failures
- A `peer_debate` trail with participating AI configs, round-by-round feedback, and `consensus_reached`
- `child_job_analyses` when a pipeline failed because child jobs failed

For direct JUnit XML analysis, the service can also return the XML with AI data added back into it:

```python
class FailureAnalysisResult(BaseModel):
    job_id: str = Field(description="Unique identifier for the analysis job")
    status: Literal["completed", "failed"] = Field(description="Analysis status")
    summary: str = Field(description="Summary of the analysis findings")
    ai_provider: str = Field(default="", description="AI provider used")
    ai_model: str = Field(default="", description="AI model used")
    failures: list[FailureAnalysis] = Field(
        default_factory=list, description="Analyzed failures"
    )
    enriched_xml: str | None = Field(
        default=None,
        description="Enriched JUnit XML with analysis results (only when raw_xml was provided in request)",
    )
```

Direct failure analyses are stored under a `job_id` too, so you can reopen them through the same results endpoints.

> **Tip:** The service groups failures by `error_signature`, so if several tests are failing for the same underlying reason, the AI can analyze that root cause once and apply the result to every affected test.

## Supported AI providers

The service supports three providers:

- `claude`
- `gemini`
- `cursor`

The provider and model can be configured globally with environment variables or passed in the request body. Optional peer analysis uses the same provider names for additional reviewer models. Relevant lines from `.env.example` now include:

```bash
AI_PROVIDER=claude

# AI model to use (required, applies to any provider)
# Can also be set per-request in webhook body
AI_MODEL=your-model-name

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

# ===================
# Peer Analysis (Optional)
# ===================
# Enable multi-AI consensus by configuring peer AI providers
# PEER_AI_CONFIGS=cursor:gpt-5.4-xhigh,gemini:gemini-2.5-pro
# PEER_ANALYSIS_MAX_ROUNDS=3
```

For server-wide peer defaults, use `PEER_AI_CONFIGS` as a comma-separated `provider:model` list. For per-request overrides, send `peer_ai_configs` as a JSON array and `peer_analysis_max_rounds` as an integer.

Jira is optional and supports both Cloud and Server/DC:

- Jira Cloud: set `JIRA_URL`, `JIRA_EMAIL`, and `JIRA_API_TOKEN`
- Jira Server/DC: set `JIRA_URL` and `JIRA_PAT`
- Set `JIRA_PROJECT_KEY` when you want Jira issue matching and creation enabled

When Jira is enabled, `PRODUCT BUG` failures can include AI-generated Jira search keywords and matched Jira issues.

## Primary workflows

### Analyze a Jenkins build

Use `POST /analyze` when Jenkins is your source of truth. You send `job_name` and `build_number`, and the service:

- Retrieves build info and console output
- Uses structured Jenkins test reports when available
- Falls back to console-only analysis when no test report exists
- Downloads build artifacts for extra context by default
- Optionally clones your test repository for source-level context
- Recursively analyzes failed child jobs in pipeline or orchestrator builds

By default, Jenkins analysis returns a `job_id` immediately. With `wait_for_completion` enabled (the default), the service can accept a build that is still running, store the job in `waiting`, poll Jenkins until the build reaches a terminal state, and then start the analysis automatically.

You can then:

- Poll `GET /results/{job_id}` for JSON status
- Open `/results/{job_id}` in a browser; if the analysis is still in progress, the app redirects you to `/status/{job_id}`
- Set `wait_for_completion` to `false` when you do not want the service to monitor a running build

If you need the full result immediately, use `?sync=true`.

For pipeline-style jobs, the top-level result may mostly be a summary, with the actual failure details stored under `child_job_analyses`.

### Analyze raw failures or JUnit XML

Use `POST /analyze-failures` when you already have failure data outside Jenkins, or when your test runner emits JUnit XML.

The built-in pytest example posts raw XML like this:

```python
response = requests.post(
    f"{server_url.rstrip('/')}/analyze-failures",
    json={
        "raw_xml": raw_xml,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
    },
    timeout=timeout_value,
)
```

That endpoint is synchronous and accepts exactly one of:

- `failures`
- `raw_xml`

If you send `raw_xml`, the response can include `enriched_xml`, which is the same report with AI analysis embedded back into it.

The example pytest integration in `examples/pytest-junitxml` documents this test command:

```shell
pytest --junitxml=report.xml --analyze-with-ai
```

> **Tip:** If your CI already produces JUnit XML, `raw_xml` mode is the easiest way to add AI analysis without changing how your tests run.

### Add peer analysis when you want consensus

Single-AI analysis remains the default. When you want a second opinion, you can add peer providers to either `POST /analyze` or `POST /analyze-failures`. The main AI analyzes first, peer AIs review that result in parallel, and the service records whether consensus was reached within the configured number of rounds.

The CLI exposes the same workflow:

```shell
jji analyze --job-name my-job --build-number 1 --peers cursor:gpt-5,gemini:2.5-pro --peer-analysis-max-rounds 5
```

In stored results, each affected failure can include `peer_debate`, and the report UI shows both a Peer Analysis summary and a per-failure round-by-round timeline. While a Jenkins-backed run is still processing, the status page also shows peer-review and main-AI revision phases as they happen.

> **Note:** Peer analysis runs on grouped failures when structured test data is available. Console-only fallback analysis does not use peer review.

### Review, comment, classify, and search

Once a result exists, the service becomes a shared review surface.

The main places people work are:

- `/` or `/dashboard` for recent jobs
- `/results/{job_id}` for a full report, with `/status/{job_id}` handling queued, waiting, or running analyses in the web app
- `/history` and `/history/test/{test_name}` for recurring-failure analysis and per-test drill-down
- The `jji` CLI for terminal-based result browsing and triage

The review workflow includes:

- Comments on individual failures
- Reviewed and unreviewed state
- Manual override of AI classification (`CODE ISSUE` vs `PRODUCT BUG`)
- Historical labels such as `FLAKY`, `REGRESSION`, `INFRASTRUCTURE`, `KNOWN_BUG`, and `INTERMITTENT`
- Searching by error signature to see whether different tests are really failing for the same reason

This is especially useful when a failure keeps resurfacing across builds: you can inspect test history, compare classifications over time, and find earlier comments or linked bugs.

### Preview or create tracker issues

The report UI and API can turn an analysis into a follow-up issue.

The issue workflow is classification-aware:

- `CODE ISSUE` failures are meant for GitHub issue workflows
- `PRODUCT BUG` failures are meant for Jira bug workflows

Before creating anything, the service can preview issue content and search for duplicates:

- GitHub duplicate search uses the configured test repository and GitHub token
- Jira search uses AI-generated keywords and can attach matching Jira bugs to the result

The issue content itself is AI-generated, with fallback templates when content generation fails.

GitHub issue creation requires server-side GitHub configuration: `TESTS_REPO_URL` plus `GITHUB_TOKEN`, with `ENABLE_GITHUB_ISSUES` available if you want to override auto-detection. Jira bug creation requires Jira to be configured on the server, including `JIRA_PROJECT_KEY`.

> **Warning:** The web UI uses a `jji_username` cookie for attribution and convenience, not full authentication or authorization. Deploy the service on a trusted network.

## Basic container configuration

The repository includes a `docker-compose.yaml` that exposes the service on port `8000`, loads `.env`, and persists data under `./data`:

```yaml
services:
  jenkins-job-insight:
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    env_file:
      - .env
    environment:
      - JENKINS_URL=${JENKINS_URL:-https://jenkins.example.com}
      - JENKINS_USER=${JENKINS_USER:-your-username}
      - JENKINS_PASSWORD=${JENKINS_PASSWORD:-your-api-token}
      - AI_PROVIDER=${AI_PROVIDER:?AI_PROVIDER is required}
      - AI_MODEL=${AI_MODEL:?AI_MODEL is required}
```

Once the service is running, the most useful entry points are:

- `/health` to confirm it is up
- `/docs` to explore the API
- `/dashboard` to browse stored analyses
