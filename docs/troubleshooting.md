# Troubleshooting

Most operator problems in `jenkins-job-insight` fall into one of these buckets:

| What you see | Usually means | Check first |
| --- | --- | --- |
| `502 Jenkins authentication failed...` | Jenkins rejected the credential | `JENKINS_USER`, `JENKINS_PASSWORD`, per-request Jenkins overrides |
| `400 No AI provider configured` or `No AI model configured` | Main AI is not configured | `AI_PROVIDER`, `AI_MODEL`, or your `jji` profile |
| `400 Invalid XML: ...` | `raw_xml` could not be parsed | The XML payload itself |
| `403 Jira integration is disabled on this server` | Jira is incomplete or explicitly disabled | `JIRA_URL`, Jira credential, `JIRA_PROJECT_KEY` |
| A job stays in `waiting` | JJI is still polling Jenkins, or polling cannot finish cleanly | Jenkins build state, `wait_for_completion`, `max_wait_minutes` |
| A legacy callback flow never fires | The current API is poll-based, not callback-based | `job_id`, `result_url`, `/status/{job_id}` |
| `404 Frontend not built` | The runtime image does not contain `frontend/dist` | Frontend build step or Docker image |
| Enriched JUnit XML was not written back | The pytest helper skipped enrichment or preserved the original file | `--junitxml`, `JJI_SERVER`, `JJI_AI_PROVIDER`, `JJI_AI_MODEL` |

Use these first:

```bash
jji health
jji status <job_id>
jji results show <job_id> --full
jji ai-configs
jji capabilities
```

> **Tip:** Start with the JSON or CLI view before debugging the browser. The JSON result shows the stored server state, while the browser adds redirects, cookies, and frontend packaging on top.

```mermaid
flowchart TD
    A[POST /analyze] --> B[Store job_id and request_params]
    B --> C{wait_for_completion and Jenkins configured?}
    C -- Yes --> D[status = waiting<br/>poll Jenkins]
    C -- No --> E[status = running]
    D -->|Build finishes| E
    D -->|Timeout or poll error| F[status = failed]

    E --> G[Analyze failures]
    G --> H{Jira enabled and configured?}
    H -- Yes --> I[Search Jira matches]
    H -- No --> J[Save result]
    I --> J

    J --> K[GET /results/{job_id}]
    K -->|Browser + in progress| L[/status/{job_id}]
    K -->|Browser + completed| M[Report page]
    K -->|CLI or JSON caller| N[JSON payload]

    O[POST /analyze-failures] --> P[Parse failures or raw_xml]
    P --> Q[Return completed or failed response immediately]
```

## Jenkins Auth Failures

For Jenkins-backed analysis, JJI turns Jenkins client errors into clear HTTP responses instead of returning a generic failure.

```604:654:src/jenkins_job_insight/analyzer.py
if isinstance(e, jenkins.NotFoundException):
    raise HTTPException(
        status_code=404,
        detail=f"Job '{job_name}' build #{build_number} not found in Jenkins",
    )

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
...
raise HTTPException(
    status_code=502,
    detail=f"Failed to connect to Jenkins: {e!s}",
)
```

A few practical rules help here:

- `401` means the credential itself is wrong.
- `403` means the credential is valid, but it cannot read that job or build.
- `404` often means the `job_name` is wrong. Use the Jenkins job path such as `folder/job-name`, not a full Jenkins build URL.
- JJI uses the same Jenkins settings for both build lookup and optional waiting/polling, so a credential that can browse Jenkins in a browser is not always enough.
- If Jenkins uses an internal or self-signed certificate, set `JENKINS_SSL_VERIFY=false` only after you confirm you trust that endpoint.

> **Warning:** Only disable `JENKINS_SSL_VERIFY` for trusted internal Jenkins instances. It turns off TLS certificate verification for that connection.

## Missing AI Configuration

The main AI provider and model are always required. Peer analysis is optional, but it does not replace the main provider.

```15:31:config.example.toml
[defaults]
# Global defaults -- all servers inherit these
jenkins_url = "https://jenkins.example.com"
jenkins_user = "your-jenkins-user"
jenkins_password = "your-jenkins-token"  # pragma: allowlist secret
jenkins_ssl_verify = true
tests_repo_url = "https://github.com/your-org/your-tests"
ai_provider = "claude"
ai_model = "claude-opus-4-6[1m]"
ai_cli_timeout = 10
# Peer analysis (multi-AI consensus)
# peers = "cursor:gpt-5.4-xhigh,gemini:gemini-2.5-pro"
# peer_analysis_max_rounds = 3
# Monitoring
wait_for_completion = true
poll_interval_minutes = 2
max_wait_minutes = 0  # 0 = no limit (wait forever)
```

```499:534:src/jenkins_job_insight/main.py
provider = ai_provider or AI_PROVIDER
model = ai_model or AI_MODEL
if not provider:
    raise HTTPException(
        status_code=400,
        detail=f"No AI provider configured. Set AI_PROVIDER env var or pass ai_provider in request body. Valid providers: {', '.join(sorted(VALID_AI_PROVIDERS))}",
    )
if provider not in VALID_AI_PROVIDERS:
    raise HTTPException(
        status_code=400,
        detail=(
            f"Unsupported AI provider: {provider}. "
            f"Valid providers: {', '.join(sorted(VALID_AI_PROVIDERS))}"
        ),
    )
if not model:
    raise HTTPException(
        status_code=400,
        detail="No AI model configured. Set AI_MODEL env var or pass ai_model in request body.",
    )
```

Use this checklist:

- Set a main `ai_provider` and `ai_model` either in server environment or in the request body.
- Valid providers are `claude`, `gemini`, and `cursor`.
- If you use peer analysis, the format is `provider:model,provider:model`.
- `peer_ai_configs: []` explicitly disables peers for a single request.
- `jji ai-configs` shows provider/model pairs from successful completed analyses. On a fresh server, it may legitimately return nothing.
- A correct app config is not enough if the provider CLI itself is missing, not on `PATH`, or not authenticated.

The provided Docker image installs all three supported CLIs at build time. If you use a custom image or VM, you must do that work yourself.

```64:73:Dockerfile
# Install Claude Code CLI (installs to ~/.local/bin)
RUN /bin/bash -o pipefail -c "curl -fsSL https://claude.ai/install.sh | bash"

# Install Cursor Agent CLI (installs to ~/.local/bin)
RUN /bin/bash -o pipefail -c "curl -fsSL https://cursor.com/install | bash"

# Configure npm for non-root global installs and install Gemini CLI
RUN mkdir -p /home/appuser/.npm-global \
    && npm config set prefix '/home/appuser/.npm-global' \
    && npm install -g @google/gemini-cli
```

## XML Parse and Enrichment Problems

`POST /analyze-failures` accepts exactly one input source: either structured `failures` or `raw_xml`.

```409:426:src/jenkins_job_insight/models.py
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

```1140:1145:src/jenkins_job_insight/main.py
if raw_xml := body.raw_xml:
    try:
        test_failures = extract_test_failures(raw_xml)
    except ParseError as e:
        raise HTTPException(status_code=400, detail=f"Invalid XML: {e}") from e
```

A valid JUnit example from the test suite looks like this:

```648:656:tests/test_main.py
SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite" tests="2" failures="1" errors="0">
    <testcase classname="tests.test_auth" name="test_login" time="0.5">
        <failure message="assert False" type="AssertionError">
            at tests/test_auth.py:42
        </failure>
    </testcase>
    <testcase classname="tests.test_auth" name="test_logout" time="0.1"/>
</testsuite>"""
```

Use this checklist when XML analysis fails:

- `400 Invalid XML: ...` means the XML itself could not be parsed.
- `422` usually means the payload shape was rejected before analysis started, for example because both `failures` and `raw_xml` were sent.
- `No test failures found in the provided XML.` means the XML parsed successfully but did not contain failing `<testcase>` elements.
- The extractor accepts both `<failure>` and `<error>`.
- If a `<failure>` has no `message` attribute, JJI falls back to the first line of the element body.

> **Note:** `POST /analyze-failures` is synchronous. It returns a finished response immediately and never enters `waiting`.

If you use the example pytest integration, missing environment variables disable enrichment cleanly, and request errors preserve the original XML file instead of corrupting it.

```40:58:examples/pytest-junitxml/conftest_junit_ai_utils.py
if not os.environ.get("JJI_SERVER"):
    logger.warning(
        "JJI_SERVER is not set. Analyze with AI features will be disabled."
    )
    session.config.option.analyze_with_ai = False
else:
    if not os.environ.get("JJI_AI_PROVIDER"):
        logger.warning(
            "JJI_AI_PROVIDER is not set. Set it explicitly (e.g., 'claude', 'gemini', 'cursor')."
        )
        session.config.option.analyze_with_ai = False
        return

    if not os.environ.get("JJI_AI_MODEL"):
        logger.warning(
            "JJI_AI_MODEL is not set. Set it explicitly to the desired model name."
        )
        session.config.option.analyze_with_ai = False
```

```102:122:examples/pytest-junitxml/conftest_junit_ai_utils.py
response = requests.post(
    f"{server_url.rstrip('/')}/analyze-failures",
    json={
        "raw_xml": raw_xml,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
    },
    timeout=timeout_value,
)
response.raise_for_status()
result = response.json()
...
if enriched_xml := result.get("enriched_xml"):
    xml_path.write_text(enriched_xml)
    logger.info("JUnit XML enriched with AI analysis: %s", xml_path)
else:
    logger.info("No enriched XML returned (no failures or analysis failed)")
```

If your downstream tooling expects a link back to JJI, the XML writer adds a `report_url` property to the first `<testsuite>` when one is available.

```131:148:src/jenkins_job_insight/xml_enrichment.py
if report_url:
    first_testsuite = next(root.iter("testsuite"), None)
    if first_testsuite is None and root.tag == "testsuite":
        first_testsuite = root
    if first_testsuite is not None:
        ts_props = first_testsuite.find("properties")
        if ts_props is None:
            ts_props = ET.Element("properties")
            first_testsuite.insert(0, ts_props)
        _add_property(ts_props, "report_url", report_url)
```

## Jira Problems

Jira is optional during analysis, but the server only treats it as enabled when it has a full Jira setup.

```184:206:src/jenkins_job_insight/config.py
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

The auth mode is stricter than many teams expect:

- Jira Cloud is only detected when `JIRA_EMAIL` and `JIRA_API_TOKEN` are both set.
- `JIRA_EMAIL` plus `JIRA_PAT` does **not** switch the client into Cloud mode.
- Server/Data Center prefers `JIRA_PAT`, and only falls back to `JIRA_API_TOKEN` when no PAT is set.

When you preview or create Jira bugs, JJI returns different errors depending on the failure mode:

```1843:1847:src/jenkins_job_insight/main.py
if not settings.jira_enabled:
    raise HTTPException(
        status_code=403,
        detail="Jira integration is disabled on this server",
    )
```

```2063:2094:src/jenkins_job_insight/main.py
if failure.analysis.classification == "CODE ISSUE":
    raise HTTPException(
        status_code=422,
        detail="Cannot create Jira bug for a CODE ISSUE classification. Use GitHub instead.",
    )

...
except httpx.HTTPStatusError as exc:
    raise HTTPException(
        status_code=502,
        detail=f"Jira API error: {exc.response.status_code}",
    ) from exc
except httpx.RequestError as exc:
    raise HTTPException(
        status_code=502,
        detail=f"Jira API unreachable: {exc}",
    ) from exc
```

Use this checklist:

- For Cloud, set `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, and `JIRA_PROJECT_KEY`.
- For Server/Data Center, set `JIRA_URL`, `JIRA_PAT`, and `JIRA_PROJECT_KEY`.
- `403 Jira integration is disabled on this server` usually means the server does not currently see a complete Jira config.
- `502 Jira API error: ...` means Jira responded, but with an error status.
- `502 Jira API unreachable: ...` means DNS, routing, TLS, proxying, or firewalling is broken between JJI and Jira.
- Jira bug creation only works for failures currently classified as `PRODUCT BUG`.

> **Note:** Jira search during analysis is best-effort. Jira lookup failures are swallowed so the main analysis can still complete.

> **Warning:** Only disable `JIRA_SSL_VERIFY` for trusted internal Jira endpoints.

## Callback Expectations and Long-Running Jobs

If you are troubleshooting an old callback-based integration, the first thing to know is that the current service is poll-based.

`POST /analyze` creates a `job_id` and returns a poll URL:

```1078:1118:src/jenkins_job_insight/main.py
job_id = str(uuid.uuid4())
...
message = f"Analysis job queued. Poll /results/{job_id} for status."

response: dict = {
    "status": "queued",
    "job_id": job_id,
    "message": message,
}

return _attach_result_links(response, base_url, job_id)
```

The CLI mirrors that behavior:

```706:718:src/jenkins_job_insight/cli/main.py
data = client.analyze(job_name, build_number, **extras)

if _state.get("json", False):
    print_output(data, columns=[], as_json=True)
else:
    typer.echo(f"Job queued: {data.get('job_id', '')}")
    typer.echo(f"Status: {data.get('status', '')}")
    typer.echo(f"Poll: {data.get('result_url', '')}")
```

That means:

- Use `GET /results/{job_id}`, `jji status <job_id>`, or the browser status page instead of waiting for a callback.
- `waiting` is normal when Jenkins monitoring is enabled and the build is still running.
- Use `--no-wait` in `jji analyze` or `"wait_for_completion": false` in the API request if you want to skip that polling step.
- A timeout appears as `Timed out waiting for Jenkins job ...`.
- A polling failure appears as `Jenkins poll failed: ...`.

JJI also persists in-progress state so long waits survive browser refreshes, and `waiting` jobs can resume after a restart if they still have enough request data.

```2096:2169:src/jenkins_job_insight/storage.py
async def mark_stale_results_failed() -> list[dict]:
    """Mark orphaned pending/running jobs as failed. Return waiting jobs for resumption."""
    waiting_jobs: list[dict] = []
...
    # Mark pending/running as failed (background task is gone)
    cursor = await db.execute(
        "UPDATE results SET status = 'failed' "
        "WHERE status IN ('pending', 'running')"
    )
...
    # Collect waiting jobs for resumption instead of failing them
    cursor = await db.execute(
        "SELECT job_id, result_json FROM results WHERE status = 'waiting'"
    )
...
    if waiting_jobs:
        logger.info(f"Found {len(waiting_jobs)} waiting job(s) to resume")
```

The status page labels the main phases explicitly:

```12:18:frontend/src/pages/StatusPage.tsx
const phaseLabels: Record<string, string> = {
  waiting_for_jenkins: 'Waiting for Jenkins build to complete...',
  analyzing: 'Analyzing test failures with AI...',
  analyzing_child_jobs: 'Analyzing child job failures...',
  analyzing_failures: 'Analyzing test failures...',
  enriching_jira: 'Searching Jira for matching bugs...',
  saving: 'Saving results...',
}
```

## Report Page and Report Generation Problems

The browser report and the JSON API share the same `GET /results/{job_id}` route. What you get depends on the request type.

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

    logger.debug(f"GET /results/{job_id}")
    result = await get_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")
```

If the frontend bundle is missing, the API is still fine, but the browser UI is not:

```2468:2473:src/jenkins_job_insight/main.py
def _serve_spa() -> HTMLResponse:
    """Read and serve the React SPA index.html."""
    index_file = _FRONTEND_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="Frontend not built")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
```

The production image expects the built frontend files to be copied in:

```84:88:Dockerfile
# Copy source code
COPY --chown=appuser:0 --from=builder /app/src /app/src

# Copy built frontend assets from frontend builder
COPY --chown=appuser:0 --from=frontend-builder /frontend/dist /app/frontend/dist
```

Use this checklist for report problems:

- If a browser request goes to `/status/{job_id}`, the analysis is still in progress. That redirect is expected.
- If the browser returns `404 Frontend not built`, the runtime image is missing `frontend/dist`.
- If the browser keeps sending you to `/register`, set a username once in the UI. The browser workflow uses the `jji_username` cookie.
- If `result_url` or other generated links use the wrong scheme or hostname behind a proxy, set `PUBLIC_BASE_URL`. JJI intentionally ignores forwarded headers and falls back to relative links when this setting is absent.
- The same `PUBLIC_BASE_URL` behavior also affects `report_url` inside enriched JUnit XML.

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

To reproduce frontend packaging problems with the same commands the repo uses for validation, run the frontend tox environment or the equivalent commands directly:

```9:30:tox.toml
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

> **Tip:** If the browser and the API disagree, trust `jji results show <job_id> --full` first. If the JSON looks correct but the page is wrong, you are usually dealing with a frontend build, routing, cookie, or `PUBLIC_BASE_URL` problem rather than a backend analysis problem.


## Related Pages

- [AI Provider Setup](ai-provider-setup.html)
- [Analyze Jenkins Jobs](analyze-jenkins-jobs.html)
- [Analyze Raw Failures and JUnit XML](direct-failure-analysis.html)
- [Jira Integration](jira-integration.html)
- [Reverse Proxy and Base URL Handling](reverse-proxy-and-base-urls.html)