# Troubleshooting

When something goes wrong, start with the `job_id`, not the browser report page. The JSON result at `/results/<job_id>` is the source of truth for whether the analysis is still `pending`, `waiting`, or `running`, finished `completed`, or stopped `failed`.

```bash
jji --server http://your-host:8000 health
jji --server http://your-host:8000 ai-configs
jji --server http://your-host:8000 status <job_id>
jji --server http://your-host:8000 results show <job_id> --full
curl -H 'Accept: application/json' http://your-host:8000/results/<job_id>
```

> **Tip:** The CLI uses `JJI_SERVER` or `--server`. If you configured a default profile in `~/.config/jji/config.toml`, `jji` can use that too, but passing `--server` explicitly is still the fastest way to diagnose a single instance.

If you are using the provided container setup, make sure the service is healthy and the data directory is persisted:

```26:50:docker-compose.yaml
# Persist SQLite database across container restarts
# The ./data directory on host maps to /data in container
volumes:
  - ./data:/data
  # Optional: Mount gcloud credentials for Vertex AI authentication
  # Uncomment if using CLAUDE_CODE_USE_VERTEX=1 with Application Default Credentials
  # - ~/.config/gcloud:/home/appuser/.config/gcloud:ro


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

# Environment variables
# Option 1: Use .env file (recommended)
env_file:
  - .env
```

The server still needs an AI provider and model, but Jenkins settings are now optional defaults. You can keep Jenkins URL and credentials in the environment, or pass them per request for `/analyze`.

```28:33:src/jenkins_job_insight/config.py
# Jenkins configuration (optional; can be provided per-request via API body).
# Empty string means "not configured"; checked with `if not self.jenkins_url`.
jenkins_url: str = ""
jenkins_user: str = ""
jenkins_password: str = Field(default="", repr=False)
jenkins_ssl_verify: bool = True
```

```11:19:.env.example
# ===================
# AI CLI Configuration
# ===================
# Choose AI provider (required): "claude", "gemini", or "cursor"
AI_PROVIDER=claude

# AI model to use (required, applies to any provider)
# Can also be set per-request in webhook body
AI_MODEL=your-model-name
```

## Jenkins Auth Failures

If `/analyze` returns `502`, the service is usually telling you exactly which Jenkins problem it hit: bad credentials, missing permissions, or general connectivity.

```474:505:src/jenkins_job_insight/analyzer.py
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
```

Check these first:

- `JENKINS_USER` and `JENKINS_PASSWORD`, or per-request `jenkins_user` and `jenkins_password`, should be credentials that can read build metadata and console output, not just log into the UI.
- A `401` means authentication failed. A `403` means the credential is valid but does not have access to that job or build.
- A `404` often means the `job_name` is wrong, especially for folder or multibranch jobs. Pass the Jenkins job path, such as `folder/job-name`, not a full Jenkins URL.
- If Jenkins uses a self-signed or internal certificate, set `JENKINS_SSL_VERIFY=false` only after you confirm the endpoint is trusted.
- If the build exists in the browser but JJI still cannot find it, verify the build number and job path carefully.

> **Warning:** Use `JENKINS_SSL_VERIFY=false` only for trusted internal Jenkins instances. It disables certificate verification for the Jenkins connection.

## Missing AI Configuration

There are two different failure modes here:

- The API rejects the request because no provider or model was configured.
- The request is accepted, but the runtime AI CLI is missing, unauthenticated, or otherwise unusable.

The API-level validation is explicit:

```288:300:src/jenkins_job_insight/main.py
provider = ai_provider or AI_PROVIDER
model = ai_model or AI_MODEL
if not provider:
    raise HTTPException(
        status_code=400,
        detail=f"No AI provider configured. Set AI_PROVIDER env var or pass ai_provider in request body. Valid providers: {', '.join(sorted(VALID_AI_PROVIDERS))}",
    )
if not model:
    raise HTTPException(
        status_code=400,
        detail="No AI model configured. Set AI_MODEL env var or pass ai_model in request body.",
    )
return provider, model
```

Provider authentication examples in the repository look like this:

```21:44:.env.example
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
```

If the provider and model are present but the CLI still cannot run, the analyzer fails early and stores the pre-flight error in the job summary:

```1279:1294:src/jenkins_job_insight/analyzer.py
# Pre-flight: verify AI CLI is reachable before spawning parallel tasks
ok, err = await check_ai_cli_available(
    ai_provider, ai_model, cli_flags=PROVIDER_CLI_FLAGS.get(ai_provider, [])
)
if not ok:
    return AnalysisResult(
        job_id=job_id,
        job_name=request.job_name,
        build_number=request.build_number,
        jenkins_url=HttpUrl(jenkins_build_url),
        status="failed",
        summary=err,
        ai_provider=ai_provider,
        ai_model=ai_model,
        failures=[],
    )
```

Use this checklist:

- In Docker Compose, `AI_PROVIDER` and `AI_MODEL` are required.
- The valid providers are `claude`, `gemini`, and `cursor`.
- Provider authentication is handled by the provider CLI itself. A valid app config is not enough if the CLI is not logged in or lacks its API key.
- If a job finishes with `status: failed`, inspect the `summary` field in `/results/<job_id>` before rerunning anything.
- Use `jji ai-configs` to list provider/model pairs from successful analyses. If it returns nothing, no completed analysis has recorded a working pair yet.
- You can test a one-off change without redeploying by sending `ai_provider` and `ai_model` in the request body.

> **Tip:** If you are using a custom image instead of the provided one, make sure the provider CLI itself is installed and on `PATH`, not just the environment variables.

## XML Parse Errors

When you use `/analyze-failures` with `raw_xml`, the request must contain exactly one input source: either `raw_xml` or `failures`.

```297:314:src/jenkins_job_insight/models.py
class AnalyzeFailuresRequest(BaseAnalysisRequest):
    """Request payload for direct failure analysis (no Jenkins)."""

    failures: list[TestFailure] | None = Field(
        default=None, description="Raw test failures to analyze"
    )
    raw_xml: Annotated[str, Field(max_length=50_000_000)] | None = Field(
        default=None,
        description="Raw JUnit XML content to extract failures from and enrich with analysis results",
    )

    @model_validator(mode="after")
    def check_input_source(self) -> "AnalyzeFailuresRequest":
        if self.failures and self.raw_xml:
            raise ValueError("Provide either 'failures' or 'raw_xml', not both")
        if not self.failures and not self.raw_xml:
            raise ValueError("Either 'failures' or 'raw_xml' must be provided")
        return self
```

Malformed XML becomes a `400` error:

```653:660:src/jenkins_job_insight/main.py
if raw_xml := body.raw_xml:
    try:
        test_failures = extract_test_failures(raw_xml)
    except ParseError as e:
        raise HTTPException(status_code=400, detail=f"Invalid XML: {e}")

    if not test_failures:
        job_id = str(uuid.uuid4())
```

A valid fixture from the test suite looks like this:

```13:26:tests/test_xml_enrichment.py
JUNIT_XML_WITH_FAILURES = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite" tests="3" failures="2" errors="0">
    <testcase classname="com.example.Tests" name="test_pass" time="0.1"/>
    <testcase classname="com.example.Tests" name="test_fail_with_message" time="0.5">
        <failure message="Expected true but got false" type="AssertionError">
            at com.example.Tests.test_fail_with_message(Tests.java:42)
        </failure>
    </testcase>
    <testcase classname="com.example.Tests" name="test_fail_no_message" time="1.2">
        <failure type="Failure">tests/storage/datavolume.go:229
Timed out after 500.055s.
Expected Running but got Scheduling</failure>
    </testcase>
</testsuite>"""
```

If XML analysis is failing:

- `400 Invalid XML: ...` means the XML could not be parsed at all.
- `422` usually means you sent both `raw_xml` and `failures`, sent neither, or exceeded the request model limits.
- If the response says `No test failures found in the provided XML.`, the XML parsed successfully but did not contain `<failure>` or `<error>` elements under `<testcase>`.
- The extractor accepts both `<failure>` and `<error>`.
- If a `<failure>` has no `message` attribute, the service falls back to the first line of the failure body, so multiline failure bodies are fine.

## Jira Issues

Jira integration is still optional, but the server now treats it as enabled only when the full Jira configuration is present: `JIRA_URL`, working Jira credentials, and `JIRA_PROJECT_KEY`. A Jenkins or XML analysis can still succeed even if Jira lookups fail or return no matches.

The enablement check now requires the project key as well as credentials:

```110:132:src/jenkins_job_insight/config.py
@property
def jira_enabled(self) -> bool:
    """Check if Jira integration is enabled and configured with valid credentials."""
    if self.enable_jira is False:
        return False
    if not self.jira_url:
        if self.enable_jira is True:
            logger.warning("enable_jira is True but JIRA_URL is not configured")
        return False
    _, token_value = _resolve_jira_auth(self)
    if not token_value:
        if self.enable_jira is True:
            logger.warning(
                "enable_jira is True but no Jira credentials are configured"
            )
        return False
    if not self.jira_project_key:
        if self.enable_jira is True:
            logger.warning(
                "enable_jira is True but JIRA_PROJECT_KEY is not configured"
            )
        return False
    return True
```

Credential selection is also stricter now:

```170:186:src/jenkins_job_insight/config.py
has_api_token = bool(
    settings.jira_api_token and settings.jira_api_token.get_secret_value()
)
has_pat = bool(settings.jira_pat and settings.jira_pat.get_secret_value())
has_email = bool(settings.jira_email)

is_cloud = has_email and has_api_token

if is_cloud:
    # Cloud: jira_api_token only (has_api_token already confirms truthiness)
    return True, settings.jira_api_token.get_secret_value()  # type: ignore[union-attr]

# Server/DC: prefer PAT, fall back to API token
if has_pat and settings.jira_pat:
    return False, settings.jira_pat.get_secret_value()
if has_api_token and settings.jira_api_token:
    return False, settings.jira_api_token.get_secret_value()
```

Use this checklist:

- For Atlassian Cloud, set `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, and `JIRA_PROJECT_KEY`. Cloud mode requires `JIRA_EMAIL` together with `JIRA_API_TOKEN`.
- `JIRA_EMAIL` plus `JIRA_PAT` does not switch the client into Cloud mode. Without `JIRA_API_TOKEN`, that combination stays on the Server/Data Center auth path.
- For Server/Data Center, set `JIRA_URL`, `JIRA_PAT`, and `JIRA_PROJECT_KEY`. If `JIRA_PAT` is absent, `JIRA_API_TOKEN` can still be used as a fallback only when `JIRA_EMAIL` is not set.
- If Jira is optional in your deployment, missing Jira matches do not mean the main analysis failed.
- If the UI or API says `Jira must be configured to create Jira bugs`, the server does not currently see a complete Jira config.
- If the API says `Cannot create Jira bug for a CODE ISSUE classification. Use GitHub instead.`, the current failure classification is the blocker, not Jira connectivity.
- `JIRA_MAX_RESULTS` defaults to `5`. Increase it if expected matches are being cut off.
- For internal Jira endpoints with self-signed certificates, use `JIRA_SSL_VERIFY=false` only on trusted networks.
- A `502 Jira API error: ...` means Jira responded with an error status. A `502 Jira API unreachable: ...` means the network path, DNS, TLS, or proxy path between JJI and Jira is broken.

> **Note:** Jira enrichment is best-effort. The analysis pipeline continues even when Jira search or relevance filtering fails.

## Waiting for Completion

Earlier callback-based delivery troubleshooting no longer applies. The current service stores analysis results locally and, by default, waits for running Jenkins builds to finish before analysis starts. During that period, the job status is `waiting`, not `failed`.

The waiting behavior is configured directly in settings:

```65:68:src/jenkins_job_insight/config.py
# Jenkins job monitoring (wait for completion before analysis)
wait_for_completion: bool = True
poll_interval_minutes: int = Field(default=2, gt=0)
max_wait_minutes: int = Field(default=0, ge=0)
```

And the analysis flow updates the job to `waiting` before polling Jenkins:

```753:771:src/jenkins_job_insight/main.py
# Wait for Jenkins job to finish if requested and Jenkins is configured
if settings.wait_for_completion and not settings.jenkins_url:
    logger.info(
        f"Wait requested for job {job_id} but jenkins_url not configured, skipping wait"
    )

if settings.wait_for_completion and settings.jenkins_url:
    await update_status(job_id, "waiting")

    completed, wait_error = await _wait_for_jenkins_completion(
        jenkins_url=settings.jenkins_url,
        job_name=body.job_name,
        build_number=body.build_number,
        jenkins_user=settings.jenkins_user,
        jenkins_password=settings.jenkins_password,
        jenkins_ssl_verify=settings.jenkins_ssl_verify,
        poll_interval_minutes=settings.poll_interval_minutes,
        max_wait_minutes=settings.max_wait_minutes,
    )
```

Use this checklist:

- `CALLBACK_URL` and `CALLBACK_HEADERS` are not part of the current service anymore. Use `/results/<job_id>` or `/status/<job_id>` to track work instead of waiting for a webhook.
- `WAIT_FOR_COMPLETION` defaults to `true` for `/analyze`.
- `POLL_INTERVAL_MINUTES` defaults to `2`, and `MAX_WAIT_MINUTES=0` means there is no timeout.
- Use `--no-wait` or `"wait_for_completion": false` if you want to skip Jenkins monitoring.
- `waiting` means Jenkins monitoring is active. It is not the same as `failed`.
- If a job never leaves `waiting`, verify that the Jenkins build is still running and that the configured Jenkins URL and credentials can poll build status.
- Timeouts surface as `Timed out waiting for Jenkins job ...`, and polling problems surface as `Jenkins poll failed: ...`.
- If the server has no default Jenkins URL configured, the wait step is skipped, but `/analyze` still needs Jenkins connectivity later to fetch the finished build.

> **Tip:** Triggering analysis immediately after starting a Jenkins build is now supported. `waiting` is the expected state while the service monitors that build.

## Report UI Problems

Older `.html` report URLs no longer apply. Browser requests to `/results/<job_id>` now load the React report UI, while API and CLI callers still get JSON from that same endpoint.

The result endpoint now switches behavior based on the request type:

```1146:1169:src/jenkins_job_insight/main.py
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
    return result
```

If the browser route fails entirely, the backend is usually missing the built frontend assets:

```2282:2287:src/jenkins_job_insight/main.py
def _serve_spa() -> HTMLResponse:
    """Read and serve the React SPA index.html."""
    index_file = _FRONTEND_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="Frontend not built")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
```

```84:88:Dockerfile
# Copy source code
COPY --chown=appuser:0 --from=builder /app/src /app/src

# Copy built frontend assets from frontend builder
COPY --chown=appuser:0 --from=frontend-builder /frontend/dist /app/frontend/dist
```

Use this checklist:

- Open `/results/<job_id>` in the browser, not `/results/<job_id>.html`.
- If the job is still `pending`, `waiting`, or `running`, the browser route redirects to `/status/<job_id>` and the JSON endpoint returns `202`.
- `404 Frontend not built` means the runtime image does not have `frontend/dist/index.html`. Rebuild the frontend with `cd frontend && npm install && npm run build`, or use the provided Docker build that copies `frontend/dist` into the final image.
- If the report page hides Jira or GitHub bug actions, inspect the `capabilities` object in `/results/<job_id>` or `GET /api/capabilities` before debugging the frontend.
- If generated `result_url` or tracker links point to the wrong scheme or hostname behind a proxy, set `PUBLIC_BASE_URL`. When it is unset, JJI intentionally returns relative URLs instead of trusting proxy headers.
- Pipeline jobs may still show a `Child Job Analyses` section instead of top-level failures. That is expected when the parent job failed because downstream jobs failed.
- A report page with no failures is still valid when the stored analysis contains none.

> **Tip:** If you only need the authoritative state, use `jji results show <job_id> --full` or `curl -H 'Accept: application/json' http://your-host:8000/results/<job_id>` first, then debug the browser UI second. The cached HTML stores integration availability at render time.
