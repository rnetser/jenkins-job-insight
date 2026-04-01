# Analyze Raw Failures and JUnit XML

`POST /analyze-failures` lets you use `jenkins-job-insight` without asking it to fetch a Jenkins build first. You send either a structured `failures` array or a `raw_xml` string containing JUnit XML, and JJI returns the final AI analysis in the same response.

The response still includes `job_id`, `base_url`, and `result_url`, so you can reopen the stored result later or open the report page at `result_url`. When `PUBLIC_BASE_URL` is not configured, `base_url` is empty and `result_url` is a relative path such as `/results/{job_id}`.

> **Note:** `POST /analyze-failures` is sync-only. It does not use the queued async flow from `POST /analyze`.

> **Note:** `enriched_xml` is only returned when you send `raw_xml`. In structured `failures` mode, the analysis is still stored and `base_url` plus `result_url` are still returned, but `enriched_xml` is `null`.

## Choose an Input Mode

### Send Structured Failures

Use this mode when another tool has already parsed your test results and you just want JJI to analyze the failures.

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

Each item in `failures` uses the `TestFailure` shape:
- `test_name` is required
- `error_message`, `stack_trace`, `duration`, and `status` are optional
- in practice, you should always send at least `error_message` and `stack_trace`

> **Note:** Input failures use `error_message`. Returned analyzed failures use `error`.

> **Tip:** Keep the full stack trace in the request. JJI deduplicates by `error_message` plus the first five stack-trace lines, but the full trace still gives the AI more context.

### Send Raw JUnit XML

Use this mode when you already have a JUnit XML artifact and want JJI to both analyze it and write the AI result back into XML.

The tests include a small JUnit example like this:

```618:626:tests/test_main.py
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

The bundled pytest/JUnit helper posts XML to the endpoint like this:

```103:110:examples/pytest-junitxml/conftest_junit_ai_utils.py
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

When JJI parses `raw_xml`, it:
- walks every `testcase`
- looks for `failure` and `error` children
- builds `test_name` as `classname.name` when `classname` exists
- skips a testcase if that would still leave the test name empty
- uses the XML `message` attribute as the error message when present
- falls back to the first line of the element text when `message` is missing
- marks `error` elements as `ERROR` and `failure` elements as `FAILED`

That behavior comes directly from the XML parser:

```35:60:src/jenkins_job_insight/xml_enrichment.py
for testcase in root.iter("testcase"):
    failure_elem = testcase.find("failure")
    error_elem = testcase.find("error")
    result_elem = failure_elem if failure_elem is not None else error_elem

    if result_elem is None:
        continue

    classname = testcase.get("classname", "")
    name = testcase.get("name", "")
    test_name = f"{classname}.{name}" if classname else name

    if not test_name:
        logger.warning("Skipping testcase with empty name attribute")
        continue

    failures.append(
        {
            "test_name": test_name,
            "error_message": result_elem.get("message", "")
            or ((result_elem.text or "").split("\n")[0].strip()),
            "stack_trace": result_elem.text or "",
            "status": "ERROR"
            if error_elem is not None and failure_elem is None
            else "FAILED",
        }
    )
```

> **Note:** Send the XML as a JSON string in `raw_xml`. This endpoint does not accept file upload form data. The request model caps `raw_xml` at `50,000,000` characters.

> **Warning:** `failures` and `raw_xml` are mutually exclusive. Sending both, sending neither, or sending an empty `failures` array returns HTTP 422. Malformed XML returns HTTP 400.

## What You Get Back

A successful `POST /analyze-failures` response includes:
- `job_id`
- `status`
- `summary`
- `ai_provider` and `ai_model`
- `failures`, with optional `peer_debate` data inside each item when peer analysis is enabled
- `base_url` and `result_url`
- `enriched_xml` only when `raw_xml` was supplied

A few behaviors are worth knowing:
- the initial response is final, because this endpoint is synchronous
- the result is still stored, so `GET /results/{job_id}` works afterward, and opening the same `result_url` in a browser shows the report page
- if `PUBLIC_BASE_URL` is set, `result_url` is absolute; otherwise it is a relative path and `base_url` is an empty string
- if `raw_xml` contains no failing testcases, JJI returns `completed`, sets `summary` to `No test failures found in the provided XML.`, and echoes the original XML back as `enriched_xml`
- if several tests share the same root cause, JJI analyzes one representative failure and applies that result to every matching test
- if some failure groups analyze successfully and others fail, the response can still be `completed`; the `summary` tells you how many were analyzed successfully

## How Deduplication Works

`POST /analyze-failures` reuses the same deduplication logic as Jenkins-backed analysis. JJI hashes the `error_message` plus the first five stack-trace lines, analyzes one representative failure per unique signature, and then applies that result to every failure in the group.

```124:142:src/jenkins_job_insight/analyzer.py
def get_failure_signature(failure: TestFailure) -> str:
    # Use error message and first 5 lines of stack trace for deduplication.
    # Intentionally limited to 5 lines: different stack depths for the same
    # root cause should still collapse into one group.
    stack_lines = failure.stack_trace.split("\n")[:5]
    signature_text = f"{failure.error_message}|{'|'.join(stack_lines)}"
    return hashlib.sha256(signature_text.encode()).hexdigest()
```

> **Tip:** This is especially useful for parameterized tests or outage-style failures where many testcases break for the same reason. You get one shared analysis instead of repeated AI calls.

## Understanding `enriched_xml`

When you send `raw_xml`, the response includes `enriched_xml`: the original JUnit report plus AI annotations written back into it.

JJI adds:
- `report_url` on the first `testsuite`
- `ai_classification` and `ai_details` on each enriched `testcase`
- `ai_affected_tests` when one analysis applies to multiple tests
- `ai_code_fix_file`, `ai_code_fix_line`, and `ai_code_fix_change` for `CODE ISSUE` results
- `ai_bug_title`, `ai_bug_severity`, `ai_bug_component`, and `ai_bug_description` for `PRODUCT BUG` results
- `ai_jira_match_<n>_key`, `ai_jira_match_<n>_summary`, `ai_jira_match_<n>_status`, `ai_jira_match_<n>_url`, `ai_jira_match_<n>_priority`, and `ai_jira_match_<n>_score` when Jira matching is enabled and relevant matches are found
- a human-readable analysis summary in `system-out`

The XML enrichment happens in two places: JJI first adds the top-level report link, then it enriches each matching testcase with structured AI properties and `system-out` text.

```131:142:src/jenkins_job_insight/xml_enrichment.py
# Add report_url to the first testsuite only
if report_url:
    first_testsuite = next(root.iter("testsuite"), None)
    # If root itself is a testsuite, use it
    if first_testsuite is None and root.tag == "testsuite":
        first_testsuite = root
    if first_testsuite is not None:
        ts_props = first_testsuite.find("properties")
        if ts_props is None:
            ts_props = ET.Element("properties")
            first_testsuite.insert(0, ts_props)
        _add_property(ts_props, "report_url", report_url)
```

```237:297:src/jenkins_job_insight/xml_enrichment.py
properties = testcase.find("properties")
if properties is None:
    properties = ET.SubElement(testcase, "properties")

_add_property(properties, "ai_classification", analysis.get("classification", ""))
_add_property(properties, "ai_details", analysis.get("details", ""))

# ... ai_affected_tests, ai_code_fix_*, ai_bug_*, ai_jira_match_* ...

text = _format_analysis_text(analysis)
if text:
    system_out = testcase.find("system-out")
    if system_out is None:
        system_out = ET.SubElement(testcase, "system-out")
        system_out.text = text
    else:
        existing = system_out.text or ""
        system_out.text = (
            f"{existing}\n\n--- AI Analysis ---\n{text}" if existing else text
        )
```

This makes `enriched_xml` useful in two ways:
- machines can read the added XML properties
- humans can open the same JUnit report and read the AI summary in `system-out`

> **Note:** If peer analysis is enabled, `enriched_xml` still contains only the final `analysis` fields. The peer debate trail is returned in JSON under `failures[].peer_debate`, not written into the XML.

> **Note:** `report_url` is added to the first `testsuite` element, not every suite.

> **Note:** If `PUBLIC_BASE_URL` is set, `report_url` is absolute. Otherwise it is a relative path like `/results/{job_id}`.

> **Note:** If a testcase already has `system-out`, JJI appends the AI section after `--- AI Analysis ---` instead of replacing the original text.

## Configuration and Request Overrides

`ai_provider` and `ai_model` can come from either the request body or environment variables. If neither source provides them, the request fails with HTTP 400.

The example env file shows the core AI settings:

```14:19:.env.example
# Choose AI provider (required): "claude", "gemini", or "cursor"
AI_PROVIDER=claude

# AI model to use (required, applies to any provider)
# Can also be set per-request in webhook body
AI_MODEL=your-model-name
```

Useful per-request overrides for `POST /analyze-failures` are:
- `tests_repo_url`: clone the tests repository and let the AI inspect real code
- `raw_prompt`: append extra instructions to the AI prompt
- `ai_cli_timeout`: override the AI CLI timeout in minutes
- `peer_ai_configs`: JSON array of `{ai_provider, ai_model}` peer reviewers for consensus analysis. Omit it to inherit the server default from `PEER_AI_CONFIGS`; send `[]` to disable peer analysis for just this request
- `peer_analysis_max_rounds`: maximum peer-review rounds for this request. Valid values are `1` through `10`. If you do not send it, JJI keeps the server default; the built-in default is `3`
- `enable_jira`: turn Jira matching on or off for this request
- `jira_url`, `jira_email`, `jira_api_token`, `jira_pat`, `jira_project_key`, `jira_ssl_verify`, `jira_max_results`: override Jira settings for this request

The test suite includes a direct `POST /analyze-failures` example with peer reviewers:

```3081:3097:tests/test_main.py
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
        "peer_ai_configs": [
            {"ai_provider": "gemini", "ai_model": "pro"},
        ],
        "peer_analysis_max_rounds": 7,
    },
)
```

Relevant optional config from `.env.example` includes:

```52:57:.env.example
# Peer Analysis (Optional)
# ===================
# Enable multi-AI consensus by configuring peer AI providers
# PEER_AI_CONFIGS=cursor:gpt-5.4-xhigh,gemini:gemini-2.5-pro
# PEER_ANALYSIS_MAX_ROUNDS=3
```

```69:100:.env.example
# TESTS_REPO_URL=https://github.com/org/test-repo

# JIRA_URL=https://your-org.atlassian.net
# JIRA_PAT=your-personal-access-token
# JIRA_EMAIL=your-email@example.com
# JIRA_API_TOKEN=your-jira-api-token
# JIRA_PROJECT_KEY=PROJ
# JIRA_SSL_VERIFY=true
# JIRA_MAX_RESULTS=5
```

If Jira is enabled for the request, matching Jira issues are added to:
- the JSON response under `failures[].analysis.product_bug_report.jira_matches`
- `enriched_xml` as `ai_jira_match_<n>_*` testcase properties

> **Note:** `POST /analyze-failures` does not require `JENKINS_URL`, `JENKINS_USER`, or `JENKINS_PASSWORD` at startup. Those are only optional server defaults for Jenkins-backed `POST /analyze` requests.

> **Note:** Jira matching only turns on when Jira is fully configured for the request or environment. In practice, that means setting `jira_project_key` or `JIRA_PROJECT_KEY` along with the Jira URL and credentials.

> **Note:** `PUBLIC_BASE_URL` is a server-side setting, not a request override. JJI does not derive these links from request headers, so when it is unset, returned `result_url` values and XML `report_url` properties stay relative.

## Pytest and JUnit XML Automation

The repository already includes a ready-to-copy example under `examples/pytest-junitxml/`. Its flow is:
1. run pytest with `--junitxml`
2. send the generated XML to `POST /analyze-failures`
3. write `enriched_xml` back to the same file

The rewrite step in the example helper is:

```111:115:examples/pytest-junitxml/conftest_junit_ai_utils.py
if enriched_xml := result.get("enriched_xml"):
    xml_path.write_text(enriched_xml)
    logger.info("JUnit XML enriched with AI analysis: %s", xml_path)
else:
    logger.info("No enriched XML returned (no failures or analysis failed)")
```

This is the easiest way to keep your existing JUnit-based tooling while attaching AI analysis directly to the XML artifact other systems already consume.

## Common Failure Cases

- Missing `ai_provider` in both the request and environment returns HTTP 400.
- Missing `ai_model` in both the request and environment returns HTTP 400.
- Invalid `tests_repo_url` returns HTTP 422 because it is validated as a URL. Use an HTTP(S) URL.
- Malformed `raw_xml` returns HTTP 400.
- Sending both `failures` and `raw_xml`, sending neither, or sending `failures: []` returns HTTP 422.
- `raw_xml` with no failures returns the original XML unchanged as `enriched_xml`.

If you only need analyzed JSON, send `failures`. If you want the AI annotations embedded back into a JUnit artifact, send `raw_xml`.
