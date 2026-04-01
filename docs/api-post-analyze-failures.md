# POST /analyze-failures

Use this endpoint when you already have failing test data and want AI analysis without talking to Jenkins. It accepts either a list of direct failure records or a raw JUnit XML document. In both cases, the response is synchronous JSON. When you send `raw_xml`, the response also includes `enriched_xml`, which is the original JUnit XML with the analysis written back into it.

> **Note:** `POST /analyze-failures` is synchronous only. It does not queue background work, does not use `callback_url`, and does not support the `?sync=` query style used by `POST /analyze`.

## Input Modes

Send exactly one of these fields:

- `failures`: best when your CI or test harness already knows which tests failed.
- `raw_xml`: best when you already produce JUnit XML and want the service to parse it and return enriched XML.

> **Tip:** If your pipeline already writes JUnit XML, prefer `raw_xml`. You get the normal structured JSON response plus an XML report you can write back to disk.

### `failures`

Each item in `failures` follows this shape:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `test_name` | string | Yes | Fully qualified test name |
| `error_message` | string | No | Defaults to `""` |
| `stack_trace` | string | No | Defaults to `""` |
| `duration` | number | No | Defaults to `0.0` |
| `status` | string | No | Defaults to `FAILED` |

At minimum, only `test_name` is required, but you should send `error_message` and `stack_trace` whenever you have them. The service uses those fields to group identical failures and avoid re-analyzing the same error multiple times.

Actual request example from the test suite:

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

### `raw_xml`

`raw_xml` is a string containing JUnit XML. The service looks for `<testcase>` elements with either a `<failure>` or `<error>` child, builds failure records from them, analyzes the results, and returns `enriched_xml`.

Actual XML sample used by the endpoint tests:

```685:693:tests/test_main.py
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

When extracting failures from XML, the service uses these rules:

- It reads both `<failure>` and `<error>` elements.
- It builds `test_name` from `classname + "." + name` when `classname` is present, or just `name` when `classname` is missing.
- It sets `status` to `FAILED` for `<failure>` and `ERROR` for `<error>`.
- It uses the XML element's `message` attribute as `error_message` when available.
- If `message` is missing, it falls back to the first line of the element text.
- Malformed XML is rejected with `400`.

Actual extraction behavior from the XML tests:

```75:78:tests/test_xml_enrichment.py
failures = extract_failures_from_xml(JUNIT_XML_WITH_ERROR)
assert len(failures) == 1
assert failures[0]["status"] == "ERROR"
assert failures[0]["error_message"] == "NullPointerException"
```

`raw_xml` is limited to `50,000,000` characters.

## Configuration

These shared request fields are available in both modes:

| Field | Type | Notes |
| --- | --- | --- |
| `ai_provider` | `claude` \| `gemini` \| `cursor` | Required unless configured globally with `AI_PROVIDER` |
| `ai_model` | string | Required unless configured globally with `AI_MODEL` |
| `tests_repo_url` | URL | If set, the service clones the repo and lets the AI inspect the code |
| `raw_prompt` | string | Extra instructions appended to the AI prompt |
| `ai_cli_timeout` | positive integer | Timeout in minutes for AI CLI calls |
| `peer_ai_configs` | array of objects | Optional peer reviewers for consensus analysis. Each item must include `ai_provider` and `ai_model`; valid peer providers are `claude`, `gemini`, and `cursor`. Omit the field to inherit the server default from `PEER_AI_CONFIGS`; send `[]` to disable peer analysis for this request. |
| `peer_analysis_max_rounds` | integer `1-10` | Maximum debate rounds when peer analysis is enabled. Model default is `3`; when omitted, the server may use `PEER_ANALYSIS_MAX_ROUNDS` instead. |
| `enable_jira` | boolean | Enables or disables Jira matching for this request |
| `jira_url`, `jira_email`, `jira_api_token`, `jira_pat`, `jira_project_key`, `jira_ssl_verify`, `jira_max_results` | various | Per-request Jira overrides that take precedence over environment defaults |

Global defaults come from environment variables. The shipped `.env.example` shows the core AI settings:

```14:19:.env.example
# Choose AI provider (required): "claude", "gemini", or "cursor"
AI_PROVIDER=claude

# AI model to use (required, applies to any provider)
# Can also be set per-request in webhook body
AI_MODEL=your-model-name
```

Peer-analysis defaults can also come from `.env.example`:

```52:57:.env.example
# ===================
# Peer Analysis (Optional)
# ===================
# Enable multi-AI consensus by configuring peer AI providers
# PEER_AI_CONFIGS=cursor:gpt-5.4-xhigh,gemini:gemini-2.5-pro
# PEER_ANALYSIS_MAX_ROUNDS=3
```

If you want repository context or Jira enrichment by default, the same `.env.example` also defines `TESTS_REPO_URL` and the `JIRA_*` variables. If you want absolute links in responses and enriched XML, also set `PUBLIC_BASE_URL`.

## Validation

| Case | Status | What happens |
| --- | --- | --- |
| Valid request | `200` | Returns a completed or failed analysis body |
| Missing AI provider and no `AI_PROVIDER` configured | `400` | Returns an error explaining that an AI provider is required |
| Missing AI model and no `AI_MODEL` configured | `400` | Returns an error explaining that an AI model is required |
| Invalid XML in `raw_xml` | `400` | Returns `Invalid XML: ...` |
| Both `failures` and `raw_xml` provided | `422` | Request is rejected by validation |
| Neither `failures` nor `raw_xml` provided | `422` | Request is rejected by validation |
| `failures: []` | `422` | Treated as missing input and rejected |
| Invalid `peer_ai_configs` entry | `422` | Each entry must include a supported `ai_provider` and a non-empty `ai_model` |
| `peer_analysis_max_rounds` outside `1-10` | `422` | Request body validation fails |
| Invalid `tests_repo_url` or other field types | `422` | Request body validation fails |

> **Warning:** `failures` and `raw_xml` are mutually exclusive. Send one or the other, never both.

## Processing

This endpoint reuses the same core failure-analysis logic as the Jenkins flow, but without any Jenkins API calls.

- Failures are grouped by an error signature built from `error_message` plus the first five lines of the stack trace.
- Each unique signature is analyzed once.
- The same analysis result is then applied to every failure in that group.
- If `tests_repo_url` is set, the repository is cloned and made available to the AI for code lookups.
- If Jira is enabled and a failure is classified as `PRODUCT BUG`, the service can attach matching Jira issues to the structured bug report.

That means multiple tests can legitimately return the same analysis if they failed for the same underlying reason.

## Responses

### Completed response

A successful run returns `200` with these top-level fields:

| Field | Meaning |
| --- | --- |
| `job_id` | Unique ID for this analysis |
| `status` | `completed` in this shape |
| `summary` | High-level summary, including total failures and unique error count |
| `ai_provider` | The provider used for analysis |
| `ai_model` | The model used for analysis |
| `failures` | Array of analyzed failures |
| `enriched_xml` | String when `raw_xml` was sent, `null` when `failures` was sent |
| `base_url` | Trusted public base URL from `PUBLIC_BASE_URL`, or `""` when no public base URL is configured |
| `result_url` | Canonical stored-result URL (`/results/{job_id}`). API clients get JSON from this path, and browsers use the same path for the report UI |

> **Note:** `status: completed` means the request finished. If some unique error groups analyze successfully and others fail, the response can still be `completed`; use `summary` and the `failures` array to see how many analyses succeeded.

Each item in `failures` contains:

| Field | Meaning |
| --- | --- |
| `test_name` | Failed test name |
| `error` | Error message stored on the analyzed failure |
| `error_signature` | SHA-256 signature used for deduplication |
| `analysis.classification` | `CODE ISSUE` or `PRODUCT BUG` |
| `analysis.affected_tests` | Other tests covered by the same analysis |
| `analysis.details` | Free-form explanation |
| `analysis.artifacts_evidence` | Evidence text when the AI returns it |
| `analysis.code_fix` | Present for `CODE ISSUE` results |
| `analysis.product_bug_report` | Present for `PRODUCT BUG` results |
| `peer_debate` | Peer-review consensus trail when peer analysis was enabled |

`analysis.code_fix` and `analysis.product_bug_report` are mutually exclusive.

When peer analysis runs, `peer_debate` adds:

- `consensus_reached`
- `rounds_used`
- `max_rounds`
- `ai_configs`
- `rounds`

Each `rounds` entry records the round number, provider/model, participant role (`orchestrator` or `peer`), that round's classification and details, and `agrees_with_orchestrator` when a peer vote counted toward consensus.

A structured `PRODUCT BUG` analysis looks like this in the test fixtures:

```74:88:tests/conftest.py
return FailureAnalysis(
    test_name="test_login_success",
    error="AssertionError: Expected 200, got 500",
    analysis=AnalysisDetail(
        classification="PRODUCT BUG",
        affected_tests=["test_login_success"],
        details="The authentication service is returning an error.",
        product_bug_report=ProductBugReport(
            title="Login fails with valid credentials",
            severity="high",
            component="auth",
            description="Users cannot log in even with correct username and password",
            evidence="Error: Authentication service returned 500",
        ),
    ),
)
```

For `PRODUCT BUG` results, `analysis.product_bug_report` can also contain:

- `title`
- `severity`
- `component`
- `description`
- `evidence`
- `jira_search_keywords`
- `jira_matches`

When Jira matching runs, each `jira_matches` entry includes `key`, `summary`, `status`, `priority`, `url`, and `score`.

### No failures found in raw XML

If the XML is valid but contains no failing testcases, the endpoint still returns `200`, but it skips AI analysis.

In that case:

- `status` is `completed`
- `summary` says no test failures were found
- `failures` is empty
- `enriched_xml` is the original XML string
- `ai_provider` and `ai_model` are left empty because no AI analysis ran

### Analysis failure after validation

If the request validates but analysis itself fails, the endpoint still returns `200`, with a body like this at a high level:

- `status`: `failed`
- `summary`: `Analysis failed: ...`
- `failures`: empty list
- `base_url` and `result_url` present
- `enriched_xml`: `null`

Validation problems still use `400` or `422`; this `failed` response is for runtime analysis errors after the request has already been accepted.

> **Note:** `base_url` comes only from `PUBLIC_BASE_URL`. When `PUBLIC_BASE_URL` is unset, `base_url` is `""` and `result_url` is returned as a relative path such as `/results/{job_id}`. Forwarded host and proto headers are intentionally ignored.

## Enriched XML

`enriched_xml` is only produced when you send `raw_xml`.

For each matched `<testcase>`, the service injects structured properties under `<properties>`:

- `ai_classification`
- `ai_details`
- `ai_affected_tests`
- `ai_code_fix_file`
- `ai_code_fix_line`
- `ai_code_fix_change`
- `ai_bug_title`
- `ai_bug_severity`
- `ai_bug_component`
- `ai_bug_description`
- `ai_jira_match_<n>_key`
- `ai_jira_match_<n>_summary`
- `ai_jira_match_<n>_status`
- `ai_jira_match_<n>_url`
- `ai_jira_match_<n>_priority`
- `ai_jira_match_<n>_score`

It also adds a human-readable summary to `<system-out>`:

- If no `<system-out>` exists, one is created.
- If one already exists, the AI output is appended under `--- AI Analysis ---`.

At the suite level, the service adds a `report_url` property to the first `<testsuite>` in the document. It points to `/results/{job_id}` by default, or to an absolute URL when `PUBLIC_BASE_URL` is configured.

Actual XML enrichment test coverage:

```124:133:tests/test_xml_enrichment.py
enriched = apply_analysis_to_xml(
    JUNIT_XML_WITH_FAILURES, analysis_map, "http://server/results/job-1"
)
root = ET.fromstring(enriched)
for testsuite in root.iter("testsuite"):
    ts_props = testsuite.find("properties")
    assert ts_props is not None
    report_props = [p for p in ts_props if p.get("name") == "report_url"]
    assert len(report_props) == 1
    assert report_props[0].get("value") == "http://server/results/job-1"
```

> **Note:** Peer-analysis metadata is not written into XML. Even when `failures[].peer_debate` is present in the JSON response, `enriched_xml` still contains only the final analysis properties listed above.

In `failures` mode, none of this XML enrichment happens, so `enriched_xml` is `null`.

## Integration Example

The repository includes a working pytest/JUnit XML example that reads a JUnit report, posts it to `POST /analyze-failures`, and writes the returned `enriched_xml` back to the same file:

```93:122:examples/pytest-junitxml/conftest_junit_ai_utils.py
server_url = os.environ.get("JJI_SERVER", "")
raw_xml = xml_path.read_text()

try:
    timeout_value = int(os.environ.get("JJI_TIMEOUT", "600"))
except ValueError:
    logger.warning("Invalid JJI_TIMEOUT value, using default 600 seconds")
    timeout_value = 600

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
else:
    logger.info("No enriched XML returned (no failures or analysis failed)")
```

This is a good pattern to follow in CI:

- Keep the original XML if the request fails.
- Only overwrite the report when `enriched_xml` is present.
- Use `raw_xml` mode when you want enriched machine-readable test reports, not just a JSON analysis response.
