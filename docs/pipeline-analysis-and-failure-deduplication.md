# Pipeline Analysis and Failure Deduplication

`jenkins-job-insight` is designed to answer two practical questions fast:

- Which part of my Jenkins pipeline actually failed?
- Are all these failing tests really different problems, or the same root cause repeated?

To do that, it follows failed child jobs through a pipeline, groups identical failures by signature, and then computes summaries and history counts from that normalized result.

## What JJI Does During Analysis

At a high level, a Jenkins analysis run looks like this:

- Read the main build metadata and console output.
- Discover failed child jobs from Jenkins pipeline metadata, with a console fallback.
- Recursively analyze those child jobs until it reaches jobs that actually contain failures.
- For jobs with structured test reports, group matching failures by signature so one analysis can cover many tests.
- Return one `FailureAnalysis` per failed test, while still preserving the shared `error_signature`.
- Flatten the final result into `failure_history` so recurring failures can be searched later by signature, test name, job, and trend.

## How Failure Deduplication Works

When JJI has a structured Jenkins test report, it does not analyze every failed test independently. Instead, it builds a stable signature from the error message plus the first five lines of the stack trace.

```124:142:src/jenkins_job_insight/analyzer.py
def get_failure_signature(failure: TestFailure) -> str:
    """Create a signature for grouping identical failures.

    Uses error message and first few lines of stack trace to identify
    failures that are essentially the same issue.
    """
    # Use error message and first 5 lines of stack trace for deduplication.
    # Intentionally limited to 5 lines: different stack depths for the same
    # root cause (e.g., varying call-site depth) should still collapse into
    # one group so the AI analyzes each unique error only once.
    stack_lines = failure.stack_trace.split("\n")[:5]
    signature_text = f"{failure.error_message}|{'|'.join(stack_lines)}"
    return hashlib.sha256(signature_text.encode()).hexdigest()
```

That signature is then used to group failures before JJI starts any per-group investigation. Each signature group still fans back out to one `FailureAnalysis` per failed test, but the actual analysis work happens once per unique signature.

```1573:1605:src/jenkins_job_insight/analyzer.py
# Group failures by signature to avoid analyzing identical errors multiple times
failure_groups: dict[str, list[TestFailure]] = defaultdict(list)
for tf in test_failures:
    sig = get_failure_signature(tf)
    failure_groups[sig].append(tf)

# Analyze each unique failure group in parallel
total_groups = len(failure_groups)
failure_tasks = []
for group_idx, (_sig, group) in enumerate(failure_groups.items(), 1):
    failure_tasks.append(
        analyze_failure_group(
            failures=group,
            console_context=console_context,
            repo_path=repo_path,
            # ... other analysis args ...
            peer_ai_configs=peer_ai_configs,
            peer_analysis_max_rounds=peer_analysis_max_rounds,
            group_label=f"{group_idx}/{total_groups}"
            if total_groups > 1
            else "",
        )
    )
```

If peer analysis is enabled, the deduplicated group is still the unit of work. JJI just switches the group handler from the single-AI path to the peer-consensus path instead of re-analyzing each test independently.

```989:1010:src/jenkins_job_insight/analyzer.py
if peer_ai_configs:
    from jenkins_job_insight.models import AiConfigEntry
    from jenkins_job_insight.peer_analysis import analyze_failure_group_with_peers

    configs = [
        AiConfigEntry(**c) if isinstance(c, dict) else c for c in peer_ai_configs
    ]
    return await analyze_failure_group_with_peers(
        failures=failures,
        console_context=console_context,
        repo_path=repo_path,
        main_ai_provider=ai_provider,
        main_ai_model=ai_model,
        peer_ai_configs=configs,
        max_rounds=peer_analysis_max_rounds,
        # ... other shared args ...
        group_label=group_label,
    )
```

This is the core reason JJI scales better on noisy failures: ten tests with the same root cause do not trigger ten separate investigations, even when peer consensus is enabled.

> **Note:** Deduplication removes repeated analysis work, not failures from the result. Users still get one `FailureAnalysis` entry per failed test, and each entry keeps its `error_signature`.

> **Note:** Peer analysis only applies when JJI has structured test failures to group. If a job falls back to console-only analysis, JJI logs a warning and runs a single console-level analysis instead of a per-signature peer debate.

> **Tip:** The same signature-grouping logic is also used by `POST /analyze-failures`, so you can test deduplication without a live Jenkins job.

If you want a concrete example, the test suite verifies that three failures with only two unique signatures produce only two group analyses:

```623:679:tests/test_main.py
# Return same signature for first two failures, different for third
signatures = iter(["sig-a", "sig-a", "sig-b"])
with patch(
    "jenkins_job_insight.main.get_failure_signature",
    side_effect=lambda f: next(signatures),
):
    with patch(
        "jenkins_job_insight.main.analyze_failure_group",
        new_callable=AsyncMock,
    ) as mock_analyze_group:
        mock_analyze_group.side_effect = [
            [mock_analysis_a, mock_analysis_a],
            [mock_analysis_b],
        ]

        # ...
        response = test_client.post(
            "/analyze-failures",
            json={
                "failures": [
                    {
                        "test_name": "test_foo",
                        "error_message": "assert False",
                        "stack_trace": "File test.py, line 10",
                    },
                    {
                        "test_name": "test_baz",
                        "error_message": "assert False",
                        "stack_trace": "File test.py, line 10",
                    },
                    {
                        "test_name": "test_bar",
                        "error_message": "KeyError: x",
                        "stack_trace": "File test.py, line 20",
                    },
                ],
                "ai_provider": "claude",
                "ai_model": "test-model",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        # analyze_failure_group called twice: once for sig-a group, once for sig-b
        assert mock_analyze_group.call_count == 2
```

> **Note:** Deduplication is strongest when Jenkins publishes a structured test report. If no test report is available, JJI falls back to a single console-level analysis for that job instead of per-test grouping.

## How Failed Child Jobs Are Found

Pipeline discovery starts with Jenkins build metadata. JJI first looks for failed or unstable downstream jobs in `subBuilds`, then in `actions[].triggeredBuilds` for older plugin styles. If the job name is missing, it can recover it from the triggered build URL.

```522:587:src/jenkins_job_insight/analyzer.py
# Check for subBuilds in pipeline (Blue Ocean / Pipeline plugin)
sub_builds = build_info.get("subBuilds", [])
for sub in sub_builds:
    if sub.get("result") in ("FAILURE", "UNSTABLE"):
        job_name = sub.get("jobName", "")
        build_num = sub.get("buildNumber", 0)
        if job_name and build_num:
            failed_jobs.append((job_name, build_num))

# Also check actions for triggered builds (older Jenkins plugins)
for action in build_info.get("actions", []):
    if action is None:
        continue
    action_class = action.get("_class", "")
    triggered_builds = action.get("triggeredBuilds", [])

    # Check for BuildAction or similar action types
    if triggered_builds or "BuildAction" in action_class:
        for triggered in triggered_builds:
            if triggered.get("result") in ("FAILURE", "UNSTABLE"):
                # Try to get job name from different possible fields
                job_name = triggered.get("jobName", "")
                if not job_name:
                    # Try to parse from URL if available
                    url = triggered.get("url", "")
                    if url:
                        try:
                            job_name, _ = JenkinsClient.parse_jenkins_url(url)
                        except ValueError:
                            continue
                build_num = triggered.get("number", triggered.get("buildNumber", 0))
                if job_name and build_num:
                    failed_jobs.append((job_name, build_num))

# Pattern: Build [job path] #[number] completed: FAILURE/UNSTABLE
pattern = r"Build\s+(.+?)\s+#(\d+)\s+completed:\s*(FAILURE|UNSTABLE)"
matches = re.findall(pattern, console_output)

for match in matches:
    job_path = match[0].strip()
    build_num = int(match[1])
    # Convert "folder » job" to "folder/job" format for Jenkins API
    job_name = job_path.replace(" » ", "/")
    failed_jobs.append((job_name, build_num))
```

A few details matter here:

- `FAILURE` and `UNSTABLE` are both treated as failed child jobs worth following.
- Structured Jenkins metadata is preferred.
- If metadata is missing, console parsing acts as a fallback.
- Console lines such as `Build folder » job-name #123 completed: FAILURE` are normalized into API-friendly job paths like `folder/job-name`.

Once child jobs are found, JJI analyzes them recursively. If a job failed only because its children failed, JJI skips a redundant top-level root-cause analysis and returns a pipeline summary with nested child results.

```909:1004:src/jenkins_job_insight/analyzer.py
if depth >= max_depth:
    return ChildJobAnalysis(
        job_name=job_name,
        build_number=build_number,
        jenkins_url=jenkins_url,
        note="Max depth reached - analysis stopped to prevent infinite recursion",
    )

# ...

failed_children = extract_failed_child_jobs(build_info)

# Fallback to console parsing if none found from build_info
if not failed_children:
    failed_children = extract_failed_child_jobs_from_console(console_output)

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
            # ...
        )
        for child_name, child_num in failed_children
    ]
    child_results = await run_parallel_with_limit(child_tasks)

    # ...
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

> **Warning:** Recursive child-job analysis stops at depth `3` to prevent infinite loops in unusual Jenkins pipeline graphs.

## How Summaries And Counts Are Computed

The project computes different counts for different views. That is intentional, and it is useful to know which number you are looking at.

| Where you see it | How JJI computes it |
| --- | --- |
| Result summary for a direct test job | `N failure(s) analyzed`, or `N failure(s) analyzed (M unique error type(s))` when deduplication reduced repeated work. |
| Result summary for a pipeline-only job | `Pipeline failed due to X child job(s)` plus a `Total: Y failure(s) analyzed` suffix when immediate child analyses contain direct failures. |
| Result summary for a mixed job | The direct-failure summary above, plus `Additionally, X failed child job(s) were analyzed recursively.` |
| `POST /analyze-failures` summary | `Analyzed X test failures (Y unique errors). Z analyzed successfully.` |
| Dashboard failure count / report page total | Recursive count across top-level failures and nested child jobs. The dashboard uses `count_all_failures()` in `storage.py`, and the React report page mirrors that same traversal with `countAllFailures()`. |
| Dashboard child-job badge | Number of top-level entries in `child_job_analyses`. |
| Signature search count | `total_occurrences` is the number of failure-history rows with that signature; `unique_tests` is the number of distinct test names in that set. |
| Job stats failure rate | Completed analyzed builds from `results` are the base count, failing builds from `failure_history` are the failure count, and failure rate is `builds_with_failures / total_builds_analyzed`. |
| Test history pass count | Estimated only when `job_name` is supplied, because `failure_history` stores failures, not full pass records. |

> **Note:** The short pipeline summary string and the dashboard/report totals are not identical on deep pipelines. The summary is built from the current analysis layer, while dashboard and report totals recurse through nested `failed_children`.

The same-signature history search is easy to understand in practice. The tests verify that one shared signature can span multiple test names and still report both totals correctly:

```523:554:tests/test_history.py
# Two different tests with the same error signature
for test_name, count in [
    ("tests.TestA.test_one", 3),
    ("tests.TestB.test_two", 2),
]:
    for i in range(count):
        await db.execute(
            """INSERT INTO failure_history
               (job_id, job_name, build_number, test_name, error_signature, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                f"job-sig-{test_name}-{i}",
                "ocp-e2e",
                i + 1,
                test_name,
                "sig-shared",
                "PRODUCT BUG",
            ),
        )

result = await storage.search_by_signature("sig-shared")
assert result["signature"] == "sig-shared"
assert result["total_occurrences"] == 5
assert result["unique_tests"] == 2
assert len(result["tests"]) == 2
```

## How Failure History Is Preserved

After analysis completes, JJI flattens failures into `failure_history`. That includes:

- top-level failures from the main job,
- failures discovered in child jobs,
- nested failures from deeper `failed_children`,
- the shared `error_signature`,
- child-job context such as `child_job_name` and `child_build_number`.

That flattening still makes later history search and job statistics fast and consistent, and it now also supports startup backfill. If JJI finds completed results that do not yet have `failure_history` rows, it rebuilds those rows and preserves the original analysis timestamp so signature searches, per-job stats, and trend views keep the right chronology.

```1001:1039:src/jenkins_job_insight/storage.py
async def backfill_failure_history() -> None:
    """Backfill failure_history from existing completed results."""

    # Find completed results that are NOT yet in failure_history.
    cursor = await db.execute(
        "SELECT r.job_id, r.result_json, r.created_at FROM results r "
        "LEFT JOIN failure_history fh ON r.job_id = fh.job_id "
        "WHERE r.status = 'completed' AND r.result_json IS NOT NULL AND fh.job_id IS NULL"
    )
    rows = await cursor.fetchall()

    # Skip completed results with zero failures — they have nothing to
    # insert into failure_history, so without this guard the LEFT JOIN
    # would find them "missing" on every startup and reprocess them.
    if count_all_failures(result_data) == 0:
        continue
    # Use the original created_at timestamp to preserve historical chronology
    await populate_failure_history(
        job_id, result_data, analyzed_at=created_at or ""
    )
```

> **Note:** Backfill only processes completed results that are missing history rows. Completed results with zero failures are skipped, so they are not reprocessed on every startup.

> **Tip:** If several different tests suddenly look related, search by `error_signature` first. It is usually the fastest way to tell whether you are looking at one repeated root cause or several independent failures.

You can inspect that stored history through the web UI, the API, or the `jji` CLI:

- Web UI: `/history`
- Web UI: `/history/test/:testName`
- API: `GET /history/search?signature=...`
- API: `GET /history/test/{test_name}`
- API: `GET /history/stats/{job_name}`
- API: `GET /history/trends`
- CLI: `jji history search --signature ...`
- CLI: `jji history test TEST_NAME`
- CLI: `jji history stats JOB_NAME`
- CLI: `jji history trends`

## Relevant Configuration

For Jenkins-backed `/analyze`, you need Jenkins connectivity plus an AI provider and model. Those Jenkins settings can be set as server defaults or passed per request. For `POST /analyze-failures`, you can skip Jenkins entirely and send failures or raw JUnit XML directly.

```6:57:.env.example
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

# ...

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

For `/analyze`, the request model and config defaults also support a few pipeline-focused overrides that are especially useful during pipeline investigations:

- `wait_for_completion` lets you submit analysis while a Jenkins build is still running. When it is `true`, JJI waits for the build to finish before it starts child-job discovery and failure analysis.
- `poll_interval_minutes` controls how often JJI polls Jenkins while it is waiting.
- `max_wait_minutes` sets an upper bound on that wait. `0` means wait indefinitely.
- `tests_repo_url` lets the analyzer inspect the test repository while still grouping failures by signature.
- `get_job_artifacts` controls whether build artifacts are downloaded for extra evidence.
- `jenkins_artifacts_max_size_mb` limits how much artifact data can be downloaded.
- `jenkins_artifacts_context_lines` controls how much artifact context is prepared for analysis.
- `PEER_AI_CONFIGS` or `peer_ai_configs` enables optional peer review for each deduplicated failure group. Omit the request field to inherit the server default, or send `[]` to disable peers for one run.
- `PEER_ANALYSIS_MAX_ROUNDS` or `peer_analysis_max_rounds` limits how many debate rounds peer analysis can use for a group. The default is `3`.

```23:31:config.example.toml
ai_cli_timeout = 10
# Peer analysis (multi-AI consensus)
# peers = "cursor:gpt-5.4-xhigh,gemini:gemini-2.5-pro"
# peer_analysis_max_rounds = 3
# Monitoring
wait_for_completion = true
poll_interval_minutes = 2
max_wait_minutes = 0  # 0 = no limit (wait forever)
```

> **Tip:** Wait/poll settings affect when a Jenkins-backed analysis starts. Artifact settings improve the quality of leaf-job analysis. Peer settings change how each deduplicated failure group is reviewed, but they do not change how signatures are computed or how child jobs are discovered.

## What This Means In Practice

For end users, the important outcome is simple:

- A noisy pipeline is broken down into the child jobs that actually failed.
- Repeated failures are analyzed once instead of over and over.
- Summaries stay readable.
- History pages and CLI queries can show whether a signature is spreading, stable, or already well understood.

That combination is what makes `jenkins-job-insight` useful on real pipelines: it turns a large nested Jenkins failure into a smaller set of actionable root causes.
