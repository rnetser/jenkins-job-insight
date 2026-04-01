# Pytest JUnit XML Integration

`jenkins-job-insight` includes a bundled pytest example in `examples/pytest-junitxml/`. It is meant for teams that already generate JUnit XML in CI and want that same XML artifact rewritten with AI annotations after the test run finishes.

This flow uses the direct `/analyze-failures` API. It uploads raw JUnit XML, not Jenkins job metadata, so it works even when you are outside a Jenkins-native workflow.

> **Note:** This is a copy-and-adapt example, not an installable pytest plugin package. Copy `examples/pytest-junitxml/conftest_junit_ai.py` into your test repository as `conftest.py`, and keep `examples/pytest-junitxml/conftest_junit_ai_utils.py` alongside it.

## What You Need

- A running `jenkins-job-insight` server.
- A pytest job that already writes `--junitxml` output.
- `requests` and `python-dotenv` installed where pytest runs.
- Client-side environment variables:
  - `JJI_SERVER`
  - `JJI_AI_PROVIDER`
  - `JJI_AI_MODEL`
  - `JJI_TIMEOUT` optional, default `600` seconds

Install the helper dependencies in the environment where pytest runs with `pip install requests python-dotenv`.

If you use the bundled container setup, the service is exposed on `http://localhost:8000`. The server still needs its own AI configuration and provider credentials. The checked-in environment template shows the required server-side model settings:

```14:19:.env.example
# Choose AI provider (required): "claude", "gemini", or "cursor"
AI_PROVIDER=claude

# AI model to use (required, applies to any provider)
# Can also be set per-request in webhook body
AI_MODEL=your-model-name
```

If you want the same pytest-uploaded XML to use multi-AI consensus, the same template now also includes optional server-side peer-analysis defaults:

```52:57:.env.example
# ===================
# Peer Analysis (Optional)
# ===================
# Enable multi-AI consensus by configuring peer AI providers
# PEER_AI_CONFIGS=cursor:gpt-5.4-xhigh,gemini:gemini-2.5-pro
# PEER_ANALYSIS_MAX_ROUNDS=3
```

> **Note:** When `PEER_AI_CONFIGS` is set on the server, the bundled pytest helper does not need any extra client-side peer settings. It still posts `raw_xml` to `POST /analyze-failures`, and the server applies its default peer-analysis settings.

> **Warning:** The current pytest helper does not actually fall back to built-in client-side defaults for `JJI_AI_PROVIDER` or `JJI_AI_MODEL`. If either one is missing, the helper disables enrichment.

## How The Example Hooks Into Pytest

The example adds an opt-in `--analyze-with-ai` flag, loads its configuration at session start, and then tries to enrich the XML at the end of the run:

```33:67:examples/pytest-junitxml/conftest_junit_ai.py
def pytest_addoption(parser):
    """Add --analyze-with-ai CLI option."""
    group = parser.getgroup("jenkins-job-insight", "AI-powered failure analysis")
    group.addoption(
        "--analyze-with-ai",
        action="store_true",
        default=False,
        help="Enrich JUnit XML with AI-powered failure analysis from jenkins-job-insight",
    )


def pytest_sessionstart(session):
    """Set up AI analysis if --analyze-with-ai is passed."""
    if session.config.option.analyze_with_ai:
        setup_ai_analysis(session)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    if session.config.option.analyze_with_ai:
        if exitstatus == 0:
            logger.info(
                "No test failures (exit code %d), skipping AI analysis", exitstatus
            )
        else:
            try:
                enrich_junit_xml(session)
            except Exception:
                logger.exception("Failed to enrich JUnit XML, original preserved")
```

This has a few practical consequences:

- You must opt in with `--analyze-with-ai`.
- You must also pass `--junitxml=...`, because the helper rewrites the XML file pytest created.
- Passing runs are skipped because the helper only enriches when pytest exits non-zero.
- Dry-run modes such as `--collectonly` and `--setupplan` disable the feature during setup.

> **Tip:** The hook uses `trylast=True`, so the enrichment step runs at the very end of pytest session shutdown, after pytest has had a chance to write the XML artifact.

## Running It

Once the two example files are in your test repository, run pytest normally with JUnit XML enabled:

`pytest --junitxml=report.xml --analyze-with-ai`

The helper calls `load_dotenv()`, so a local `.env` file in your test repository is a convenient place to set `JJI_SERVER`, `JJI_AI_PROVIDER`, `JJI_AI_MODEL`, and `JJI_TIMEOUT`.

The client-side timeout comes from `JJI_TIMEOUT` and is measured in seconds. That is separate from the server's `AI_CLI_TIMEOUT`, which is a server setting measured in minutes.

## What Gets Sent To The Service

The helper reads the JUnit XML file from pytest, uploads it to `/analyze-failures`, and only rewrites the file when the response includes `enriched_xml`:

```60:122:examples/pytest-junitxml/conftest_junit_ai_utils.py
def enrich_junit_xml(session) -> None:
    xml_path_raw = getattr(session.config.option, "xmlpath", None)
    if not xml_path_raw:
        logger.warning(
            "xunit file not found; pass --junitxml. Skipping AI analysis enrichment"
        )
        return

    xml_path = Path(xml_path_raw)
    if not xml_path.exists():
        logger.warning(
            "xunit file not found under %s. Skipping AI analysis enrichment",
            xml_path_raw,
        )
        return

    ai_provider = os.environ.get("JJI_AI_PROVIDER", "")
    ai_model = os.environ.get("JJI_AI_MODEL", "")
    if not ai_provider or not ai_model:
        logger.warning(
            "JJI_AI_PROVIDER and JJI_AI_MODEL must be set, skipping AI analysis enrichment"
        )
        return

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
```

The bundled helper sends only three fields:

- `raw_xml`
- `ai_provider`
- `ai_model`

That is enough for the standard flow, but it is deliberately minimal.

The shared request model behind `/analyze-failures` also supports optional peer-analysis overrides:

```95:105:src/jenkins_job_insight/models.py
peer_ai_configs: list[AiConfigEntry] | None = Field(
    default=None,
    description=(
        "List of peer AI configs for consensus analysis. "
        "Omit to inherit the server default; send [] to disable peer analysis "
        "for this request. Each peer reviews the main AI's analysis."
    ),
)
peer_analysis_max_rounds: Annotated[int, Field(ge=1, le=10)] = Field(
    default=3,
    description="Maximum debate rounds for peer analysis",
)
```

> **Tip:** The `/analyze-failures` request model also supports optional fields such as `tests_repo_url`, `enable_jira`, `raw_prompt`, `peer_ai_configs`, and `peer_analysis_max_rounds`. If you want pytest-driven analysis to use those features, extend the JSON payload in `conftest_junit_ai_utils.py`.

## What The Server Does With The XML

On the server side, JUnit XML is parsed, failures and errors are extracted, repeated failures are deduplicated by signature, AI analysis runs, and the response includes a new `enriched_xml` string.

A few endpoint details are worth knowing:

- `/analyze-failures` accepts either `raw_xml` or a `failures` array, but not both.
- The bundled pytest example always uses `raw_xml`.
- The parser analyzes both `<failure>` and `<error>` elements.
- In raw XML mode, malformed XML returns HTTP `400`.
- If the XML contains no failures, the service returns the original XML unchanged as `enriched_xml`.
- The `raw_xml` field is capped at `50,000,000` characters.

## What The Server Writes Back Into The XML

When enrichment happens, `jenkins-job-insight` adds AI properties to the affected `<testcase>` elements and appends a readable summary to `system-out`:

```226:297:src/jenkins_job_insight/xml_enrichment.py
def _inject_analysis(testcase: Element, analysis: dict[str, Any]) -> None:
    properties = testcase.find("properties")
    if properties is None:
        properties = ET.SubElement(testcase, "properties")

    _add_property(properties, "ai_classification", analysis.get("classification", ""))
    _add_property(properties, "ai_details", analysis.get("details", ""))

    affected = analysis.get("affected_tests", [])
    if affected:
        _add_property(properties, "ai_affected_tests", ", ".join(affected))

    code_fix = analysis.get("code_fix")
    if code_fix and isinstance(code_fix, dict):
        _add_property(properties, "ai_code_fix_file", code_fix.get("file", ""))
        _add_property(properties, "ai_code_fix_line", str(code_fix.get("line", "")))
        _add_property(properties, "ai_code_fix_change", code_fix.get("change", ""))

    bug_report = analysis.get("product_bug_report")
    if bug_report and isinstance(bug_report, dict):
        _add_property(properties, "ai_bug_title", bug_report.get("title", ""))
        _add_property(properties, "ai_bug_severity", bug_report.get("severity", ""))
        _add_property(properties, "ai_bug_component", bug_report.get("component", ""))
        _add_property(
            properties, "ai_bug_description", bug_report.get("description", "")
        )
# ... Jira match properties omitted for brevity ...
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

In practice, the rewritten XML can include:

- `ai_classification` and `ai_details`
- `ai_affected_tests` when one root cause affects multiple tests
- `ai_code_fix_file`, `ai_code_fix_line`, and `ai_code_fix_change` for `CODE ISSUE`
- `ai_bug_title`, `ai_bug_severity`, `ai_bug_component`, and `ai_bug_description` for `PRODUCT BUG`
- `ai_jira_match_<n>_*` fields when Jira matching is enabled and relevant matches are found
- a human-readable summary in `system-out`

The service also adds a `report_url` property to the first `<testsuite>` so the artifact can link back to the full JJI report:

```131:148:src/jenkins_job_insight/xml_enrichment.py
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
        else:
            logger.warning(
                "Could not add report_url: no testsuite element found in XML"
            )

    return ET.tostring(root, encoding="unicode", xml_declaration=True)
```

> **Note:** Only the first `<testsuite>` gets `report_url`. That matters if your test runner emits a multi-suite JUnit document.

## Absolute Links vs Relative Links

Whether `report_url` is an absolute URL or a relative path depends on the server's `PUBLIC_BASE_URL` setting:

```105:109:src/jenkins_job_insight/config.py
    # Trusted public base URL — used for result_url and tracker links.
    # When set, _extract_base_url() returns this value verbatim.
    # When unset, _extract_base_url() returns an empty string (relative
    # URLs only) — request Host / X-Forwarded-* headers are never trusted.
    public_base_url: str | None = None
```

If `PUBLIC_BASE_URL` is set, rewritten XML will contain fully qualified links such as `https://your-jji.example.com/results/<job_id>`. If it is not set, the XML gets a relative `report_url` like `/results/<job_id>`.

> **Tip:** Set `PUBLIC_BASE_URL` on the JJI server if you archive JUnit XML outside the service itself and want the embedded report link to be clickable everywhere.

## Troubleshooting

If the XML is not being rewritten, check these first:

- `--analyze-with-ai` was passed.
- `--junitxml=...` was passed and the file exists where pytest wrote it.
- `JJI_SERVER`, `JJI_AI_PROVIDER`, and `JJI_AI_MODEL` are all set in the pytest environment.
- The JJI server is reachable from the machine running pytest.
- The server has working AI provider authentication configured.
- `JJI_TIMEOUT` is high enough for your XML size and chosen model.

If you still see an unchanged XML file, that is usually by design. The bundled helper leaves the existing file alone when:

- the request to `/analyze-failures` fails
- the response does not include `enriched_xml`
- the run was fully green and pytest exited `0`
- the run was a dry run such as `--collectonly` or `--setupplan`

## Summary

Use this bundled example when you want your existing pytest JUnit XML artifact to carry AI analysis with it. The flow is intentionally simple: pytest writes XML, the helper posts `raw_xml` to `/analyze-failures`, and the service returns a rewritten XML artifact containing AI annotations and a backlink to the full JJI report.
