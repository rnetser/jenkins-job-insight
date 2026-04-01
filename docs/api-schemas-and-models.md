# Schemas and Data Models

`jenkins-job-insight` has two main input models and two main analysis result models:

| Use case | Request model | Main result model |
| --- | --- | --- |
| Analyze a Jenkins build | `AnalyzeRequest` | `AnalysisResult` |
| Analyze a failure list or raw JUnit XML | `AnalyzeFailuresRequest` | `FailureAnalysisResult` |

The shared building block underneath both flows is `FailureAnalysis`. That is the object that tells you which test failed, what error was seen, and what the AI concluded.

A useful mental model is:

- `AnalyzeRequest` -> `AnalysisResult`
- `AnalyzeFailuresRequest` -> `FailureAnalysisResult`
- `AnalysisResult.failures[]` and `FailureAnalysisResult.failures[]` -> `FailureAnalysis`
- `FailureAnalysis.analysis` -> `AnalysisDetail`
- `AnalysisDetail.code_fix` -> `CodeFix`
- `AnalysisDetail.product_bug_report` -> `ProductBugReport`
- `ProductBugReport.jira_matches[]` -> `JiraMatch`
- `BaseAnalysisRequest.peer_ai_configs[]` -> `AiConfigEntry`
- `FailureAnalysis.peer_debate` -> `PeerDebate`
- `PeerDebate.rounds[]` -> `PeerRound`
- `AnalysisResult.child_job_analyses[]` -> recursive `ChildJobAnalysis`

## Shared Request Fields

Both `AnalyzeRequest` and `AnalyzeFailuresRequest` inherit `BaseAnalysisRequest`, so both endpoints support the same cross-cutting overrides.

| Group | Fields | What they are for |
| --- | --- | --- |
| AI selection | `ai_provider`, `ai_model`, `ai_cli_timeout`, `raw_prompt` | Choose the AI backend and control how analysis is generated |
| Peer analysis | `peer_ai_configs`, `peer_analysis_max_rounds` | Ask additional AI providers to review the main analysis and control how many debate rounds are allowed |
| Source-code context | `tests_repo_url` | Let the service clone a repository and use it as extra context |
| Jira enrichment | `enable_jira`, `jira_url`, `jira_email`, `jira_api_token`, `jira_pat`, `jira_project_key`, `jira_ssl_verify`, `jira_max_results` | Attach likely Jira matches to `PRODUCT BUG` findings |
| GitHub access | `github_token` | Optional token used by related GitHub-aware features, especially for private repos |

`ai_provider` accepts `claude`, `gemini`, or `cursor`.

`peer_ai_configs` is a list of `AiConfigEntry` objects, each with `ai_provider` and `ai_model`. Each entry must use a supported provider and a non-blank model name. `peer_analysis_max_rounds` controls how many review and revision cycles are allowed, and it must be between `1` and `10`.

`peer_ai_configs` has three useful modes:

- omit the field to inherit the server's `PEER_AI_CONFIGS` default
- send `[]` to disable peer analysis for that request
- send a non-empty list to choose the peer reviewers explicitly

A request example from the test suite:

```840:847:tests/test_models.py
req = AnalyzeRequest(
    job_name="j",
    build_number=1,
    peer_ai_configs=[
        AiConfigEntry(ai_provider="cursor", ai_model="gpt-5.4-xhigh"),
        AiConfigEntry(ai_provider="gemini", ai_model="pro"),
    ],
)
```

Because both request models inherit `BaseAnalysisRequest`, the same peer-analysis fields are available on `AnalyzeFailuresRequest` too.

> **Note:** Most of these fields are optional because the server can load defaults from environment variables. If you send a non-`null` value in the request body, that value overrides the server default for that request.

> **Warning:** `ai_provider` and `ai_model` are optional in the schema, but not optional at runtime. If neither the request nor the server environment provides them, the API returns `400`.

## `AnalyzeRequest`

Use `AnalyzeRequest` with `POST /analyze` when you want the server to fetch Jenkins data itself.

Required fields:

- `job_name`: Jenkins job name. Folder-style paths such as `folder/job-name` are supported.
- `build_number`: build number to analyze.

Important optional fields added by `AnalyzeRequest`:

| Field | Purpose |
| --- | --- |
| `wait_for_completion` | Wait for a running Jenkins build to finish before analysis starts |
| `poll_interval_minutes` | Control how often Jenkins is polled while waiting |
| `max_wait_minutes` | Stop waiting after a fixed time (`0` means no limit) |
| `jenkins_url`, `jenkins_user`, `jenkins_password`, `jenkins_ssl_verify` | Per-request Jenkins connection overrides |
| `jenkins_artifacts_max_size_mb` | Limit how many Jenkins artifacts are pulled |
| `jenkins_artifacts_context_lines` | Limit how much artifact context is sent to AI |
| `get_job_artifacts` | Turn artifact collection on or off |

An actual request example from the test suite:

```2314:2322:tests/test_main.py
body = AnalyzeRequest(
    job_name="my-job",
    build_number=1,
    wait_for_completion=True,
    poll_interval_minutes=1,
    max_wait_minutes=5,
    ai_provider="claude",
    ai_model="test-model",
)
```

Response behavior is important here:

- `POST /analyze` is asynchronous and returns `202` with queue metadata, not a full `AnalysisResult`.
- The response gives you a `job_id`; poll `GET /results/{job_id}` or follow `result_url` to fetch the eventual `AnalysisResult`.
- When `wait_for_completion` is enabled and Jenkins connection settings are available, the stored job can spend time in `waiting` before it moves to `running`.

> **Note:** The helper link fields are still HTTP-layer extras rather than Pydantic model fields. `base_url` is only populated when the server is configured with `PUBLIC_BASE_URL`; otherwise it is an empty string and `result_url` is relative.

> **Tip:** If you want better AI context without hard-coding repository details into the server, pass `tests_repo_url` in the request. That gives the analysis step access to the test repository for just that run.

## `AnalyzeFailuresRequest`

Use `AnalyzeFailuresRequest` with `POST /analyze-failures` when you already have failure data and do not want the service to fetch from Jenkins.

It supports exactly one of these input styles:

| Input style | Field | What happens |
| --- | --- | --- |
| Structured failures | `failures` | The service analyzes your supplied failure list |
| Raw JUnit XML | `raw_xml` | The service extracts failures first, then analyzes them and can return `enriched_xml` |

The nested `TestFailure` objects are intentionally simple:

| Field | Meaning |
| --- | --- |
| `test_name` | Fully qualified test name |
| `error_message` | Failure message or error text |
| `stack_trace` | Full stack trace, if available |
| `duration` | Test duration in seconds, defaults to `0.0` |
| `status` | Failure status, defaults to `FAILED` |

A real JSON example from the tests:

```401:413:tests/test_main.py
response = test_client.post(
    "/analyze-failures",
    json={
        "failures": [
            {
                "test_name": "test_foo",
                "error_message": "assert False",
                "stack_trace": "File test.py, line 10",
            }
        ],
        "ai_provider": "claude",
        "ai_model": "test-model",
    },
)
```

The repository also includes a practical `raw_xml` example in its pytest/JUnit integration:

```102:120:examples/pytest-junitxml/conftest_junit_ai_utils.py
try:
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
except Exception as ex:
    logger.exception(f"Failed to enrich JUnit XML, original preserved. {ex}")
    return

if enriched_xml := result.get("enriched_xml"):
    xml_path.write_text(enriched_xml)
    logger.info("JUnit XML enriched with AI analysis: %s", xml_path)
```

What to expect from `raw_xml` mode:

- `raw_xml` is limited to 50,000,000 characters.
- Invalid XML returns `400`.
- If the XML contains no failures, the response still succeeds and returns the original XML in `enriched_xml`.
- If you use `failures` mode instead of `raw_xml`, `enriched_xml` is `null`.

> **Warning:** Provide either `failures` or `raw_xml`, not both. Supplying both, or neither, is treated as a validation error.

When XML enrichment is used, the returned `enriched_xml` is more than a copy of the original report. The code and tests show that it adds:

- a testsuite-level `report_url` property pointing back to the JJI report
- extra testcase properties such as `ai_bug_title` and `ai_bug_severity` for product bugs
- human-readable AI analysis text in `system-out`

## Result Models

### `AnalysisResult`

`AnalysisResult` is the full Jenkins-oriented result shape. Its main fields are:

- `job_id`
- `job_name`
- `build_number`
- `jenkins_url`
- `status`
- `summary`
- `ai_provider`
- `ai_model`
- `failures`
- `child_job_analyses`

`status` can be `pending`, `waiting`, `running`, `completed`, or `failed`.

The updated model and tests explicitly allow the `waiting` state for Jenkins jobs that have been queued but are still waiting for the Jenkins build to finish:

```101:105:tests/test_models.py
result = AnalysisResult(
    job_id="test-id",
    status="waiting",
    summary="Waiting for Jenkins job to complete",
)
```

### `FailureAnalysisResult`

`FailureAnalysisResult` is the direct-failures response shape returned by `POST /analyze-failures`. Its main fields are:

- `job_id`
- `status`
- `summary`
- `ai_provider`
- `ai_model`
- `failures`
- `enriched_xml`

Here, `status` is only `completed` or `failed`, because this endpoint runs synchronously.

### Side-by-side comparison

| Field | `AnalysisResult` | `FailureAnalysisResult` |
| --- | --- | --- |
| `job_id` | Yes | Yes |
| `status` | `pending`, `waiting`, `running`, `completed`, `failed` | `completed`, `failed` |
| `summary` | Yes | Yes |
| `ai_provider`, `ai_model` | Yes | Yes |
| `failures` | Yes | Yes |
| `job_name`, `build_number`, `jenkins_url` | Yes | No |
| `child_job_analyses` | Yes | No |
| `enriched_xml` | No | Yes |

> **Note:** The HTTP layer also adds `base_url` and `result_url`. Those are helpful API response fields, but they are not fields on the Pydantic model classes themselves. `base_url` is only populated when `PUBLIC_BASE_URL` is configured; otherwise it is empty and `result_url` is relative.

> **Tip:** Treat `summary` as display text for humans. If you are automating against the API, use structured fields like `failures`, `analysis.classification`, `code_fix`, `product_bug_report`, and `jira_matches` instead.

## `FailureAnalysis`

`FailureAnalysis` is the most important model in the whole system. Every analyzed failure, whether it came from Jenkins or from a direct XML upload, ends up here.

Its fields are:

- `test_name`: the failed test
- `error`: the result-side error message or exception text
- `analysis`: the structured AI output
- `error_signature`: a SHA-256 hash of the error plus stack trace, used for deduplication
- `peer_debate`: an optional multi-AI debate trail attached when peer analysis is used

A small but useful naming detail: request-side failure inputs use `error_message`, but result-side failure outputs use `error`.

Inside `analysis`, the service returns an `AnalysisDetail` object with:

- `classification`
- `affected_tests`
- `details`
- `artifacts_evidence`
- exactly one of `code_fix` or `product_bug_report`

In practice, the expected classifications are `CODE ISSUE` and `PRODUCT BUG`.

A real `CODE ISSUE` example from the tests:

```19:35:tests/test_bug_creation.py
def code_issue_failure() -> FailureAnalysis:
    """A CODE ISSUE failure with a code fix."""
    return FailureAnalysis(
        test_name="tests.auth.test_login.TestLogin.test_valid_credentials",
        error="AssertionError: Expected status 200, got 500",
        error_signature="abc123def456",  # pragma: allowlist secret
        analysis=AnalysisDetail(
            classification="CODE ISSUE",
            affected_tests=["tests.auth.test_login.TestLogin.test_valid_credentials"],
            details="The test fails because the login endpoint handler does not catch ValueError from the password validator.",
            code_fix=CodeFix(
                file="src/auth/handlers.py",
                line="42",
                change="Add try/except around password_validator.validate(password)",
            ),
        ),
    )
```

A real `PRODUCT BUG` example from the tests:

```41:58:tests/test_bug_creation.py
return FailureAnalysis(
    test_name="tests.network.test_dns.TestDNS.test_resolve",
    error="TimeoutError: DNS resolution timed out after 30s",
    error_signature="xyz789ghi012",
    analysis=AnalysisDetail(
        classification="PRODUCT BUG",
        affected_tests=["tests.network.test_dns.TestDNS.test_resolve"],
        details="DNS resolution is failing intermittently on the internal resolver.",
        product_bug_report=ProductBugReport(
            title="DNS resolution timeout on internal resolver",
            severity="high",
            component="networking",
            description="Internal DNS resolver fails to resolve hostnames within 30s",
            evidence="TimeoutError at dns_client.py:88 - socket.timeout after 30000ms",
            jira_search_keywords=["DNS", "timeout", "resolver"],
        ),
    ),
)
```

What those nested models mean:

| Nested model | Fields | When you see it |
| --- | --- | --- |
| `CodeFix` | `file`, `line`, `change` | When the failure is classified as `CODE ISSUE` |
| `ProductBugReport` | `title`, `severity`, `component`, `description`, `evidence`, `jira_search_keywords`, `jira_matches` | When the failure is classified as `PRODUCT BUG` |

A few practical notes about `AnalysisDetail`:

- `artifacts_evidence` is intended to hold verbatim supporting lines from build artifacts, not a reworded summary.
- `code_fix` and `product_bug_report` are mutually exclusive.
- When one branch is empty, it is omitted from serialized output rather than emitted as `false`.

> **Tip:** `error_signature` is not just metadata. The system uses it to deduplicate identical failures, connect matching historical failures, and keep related comments or overrides aligned across the same underlying error.

### `PeerDebate`, `PeerRound`, and `AiConfigEntry`

When peer analysis is enabled, each `FailureAnalysis` can include `peer_debate`. This object records which models participated, how many rounds were used, and whether the group reached consensus. Like the main classification, the debate is produced once per deduplicated error signature and then attached to every matching failure in that group.

| Model | Fields | When you see it |
| --- | --- | --- |
| `AiConfigEntry` | `ai_provider`, `ai_model` | In request-side `peer_ai_configs` lists and in `PeerDebate.ai_configs` |
| `PeerDebate` | `consensus_reached`, `rounds_used`, `max_rounds`, `ai_configs`, `rounds` | When peer analysis was enabled for that failure group |
| `PeerRound` | `round`, `ai_provider`, `ai_model`, `role`, `classification`, `details`, `agrees_with_orchestrator` | One debate entry from either the main AI (`orchestrator`) or a reviewing `peer`; `agrees_with_orchestrator` can be `true`, `false`, or `null` |

A real `PeerDebate` example from the tests:

```690:696:tests/test_models.py
d = PeerDebate(
    consensus_reached=True,
    rounds_used=1,
    max_rounds=3,
    ai_configs=[{"ai_provider": "claude", "ai_model": "opus"}],
    rounds=[],
)
```

A `FailureAnalysis` attaches that debate trail through `peer_debate`:

```807:820:tests/test_models.py
debate = PeerDebate(
    consensus_reached=True,
    rounds_used=1,
    max_rounds=3,
    ai_configs=[],
    rounds=[],
)
fa = FailureAnalysis(
    test_name="t",
    error="e",
    analysis=AnalysisDetail(details="d"),
    error_signature="sig",
    peer_debate=debate,
)
```

> **Note:** When peer analysis is not used, `peer_debate` is not populated. The test suite checks this with `data.get("peer_debate") is None`.

## `JiraMatch`

`JiraMatch` appears inside `ProductBugReport.jira_matches`. It represents a Jira issue that may already track the same problem.

Its fields are:

| Field | Meaning |
| --- | --- |
| `key` | Jira issue key such as `PROJ-123` |
| `summary` | Issue title |
| `status` | Jira workflow status |
| `priority` | Jira priority |
| `url` | Full Jira URL |
| `score` | Relevance score for the match |

A real example from the tests:

```326:333:tests/test_models.py
match = JiraMatch(
    key="PROJ-456",
    summary="Login fails",
    status="Open",
    priority="High",
    url="https://jira.example.com/browse/PROJ-456",
    score=0.85,
)
```

`score` is used as a relevance signal. In practice, higher scores mean a more convincing match.

`JiraMatch` is tied to `ProductBugReport` in two steps:

- the AI first produces `jira_search_keywords`
- post-processing then searches Jira and attaches the filtered `jira_matches`

> **Note:** `jira_matches` is best-effort enrichment. It only appears when a failure is classified as `PRODUCT BUG` and Jira is both enabled and correctly configured.

## `ChildJobAnalysis`

`ChildJobAnalysis` is how `jenkins-job-insight` represents failures from downstream jobs in a pipeline.

Each child analysis can have:

- `job_name`
- `build_number`
- `jenkins_url`
- `summary`
- `failures`
- `failed_children`
- `note`

The important part is that `failed_children` is recursive. A child job can itself have nested failed children.

The recursion shows up directly in the analyzer when a job fails because downstream jobs failed:

```1035:1088:src/jenkins_job_insight/analyzer.py
if failed_children:
    # Recursively analyze failed children IN PARALLEL with bounded concurrency
    child_tasks = [
        analyze_child_job(
            child_name,
            child_num,
            jenkins_client,
            jenkins_base_url,
            depth + 1,
            max_depth,
            repo_path,
            ai_provider,
            ai_model,
            ai_cli_timeout,
            custom_prompt,
            artifacts_context="",
            server_url=server_url,
            job_id=job_id,
        )
        for child_name, child_num in failed_children
    ]
    child_results = await run_parallel_with_limit(child_tasks)

    # Handle exceptions in results
    child_analyses = []
    for i, result in enumerate(child_results):
        if isinstance(result, Exception):
            child_name, child_num = failed_children[i]
            child_analyses.append(
                ChildJobAnalysis(
                    job_name=child_name,
                    build_number=child_num,
                    jenkins_url="",
                    note=f"Analysis failed: {format_exception_with_type(result)}",
                )
            )
        else:
            child_analyses.append(result)

    # This job failed because children failed - skip Claude CLI analysis
    # Count failures from child analyses
    total_failures = sum(len(child.failures) for child in child_analyses)
    summary = f"Pipeline failed due to {len(child_analyses)} child job(s)."
    if total_failures > 0:
        summary += f" Total: {total_failures} failure(s) analyzed. See child analyses below."

    return ChildJobAnalysis(
        job_name=job_name,
        build_number=build_number,
        jenkins_url=jenkins_url,
        summary=summary,
        failures=[],  # Pipeline has no direct failures
        failed_children=child_analyses,
    )
```

This means a top-level `AnalysisResult` can contain:

- failures from the parent job in `failures`
- failures from downstream jobs in `child_job_analyses`
- failures from deeper descendants in each child’s `failed_children`

> **Tip:** If you are building a report or integration for pipeline jobs, walk `child_job_analyses` recursively. Looking only at the top-level `failures` array will miss downstream failures.

## Configuration and Defaults

The server-side `Settings` model still provides defaults for many request fields. Important built-in defaults now include:

- `jenkins_ssl_verify = true`
- `jira_ssl_verify = true`
- `jira_max_results = 5`
- `ai_cli_timeout = 10`
- `peer_ai_configs` is empty by default (no peer reviewers configured)
- `peer_analysis_max_rounds = 3`
- `jenkins_artifacts_max_size_mb = 500`
- `jenkins_artifacts_context_lines = 200`
- `get_job_artifacts = true`
- `wait_for_completion = true`
- `poll_interval_minutes = 2`
- `max_wait_minutes = 0`

Server-side peer defaults can be configured in `.env`:

```53:57:.env.example
# Peer Analysis (Optional)
# ===================
# Enable multi-AI consensus by configuring peer AI providers
# PEER_AI_CONFIGS=cursor:gpt-5.4-xhigh,gemini:gemini-2.5-pro
# PEER_ANALYSIS_MAX_ROUNDS=3
```

A few configuration rules to remember:

- Server-level Jenkins settings can be left blank and supplied per request through `AnalyzeRequest`.
- `tests_repo_url` is validated as a URL in request payloads.
- `ai_cli_timeout`, `jira_max_results`, `jenkins_artifacts_max_size_mb`, `jenkins_artifacts_context_lines`, and `poll_interval_minutes` must be positive.
- `peer_analysis_max_rounds` must be between `1` and `10`.
- `max_wait_minutes` accepts `0`, which means "wait indefinitely".
- At the server level, `PEER_AI_CONFIGS` uses a `provider:model,provider:model` string, but API requests use a JSON list of `AiConfigEntry` objects.
- Omitting `peer_ai_configs` lets a request inherit `PEER_AI_CONFIGS`; sending `[]` disables peer analysis for that request.
- `jira_api_token` and `jira_pat` now represent different Jira auth paths: use `jira_api_token` with `jira_email` for Jira Cloud, and `jira_pat` for Jira Server/Data Center.
- `PUBLIC_BASE_URL` affects only the HTTP helper links such as `base_url` and `result_url`; it is not part of any Pydantic response model.
- `github_token` is a request field on the main analysis models, but `enable_github_issues` lives in server configuration rather than in `BaseAnalysisRequest`.
- Jira enrichment now needs the full Jira configuration: `JIRA_URL`, valid credentials, and a `JIRA_PROJECT_KEY`.

> **Note:** Sensitive request override fields such as `jenkins_user`, `jenkins_password`, `jira_email`, `jira_api_token`, `jira_pat`, and `github_token` are encrypted at rest when stored for waiting-job resumption and stripped from API responses before result data is returned to clients.

> **Warning:** Setting `enable_jira: true` in a request does not enable Jira by itself. Jira enrichment only turns on when the server also has `JIRA_URL`, a `JIRA_PROJECT_KEY`, and valid credentials available.
