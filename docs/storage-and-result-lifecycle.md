# Storage and Result Lifecycle

Jenkins Job Insight persists analysis data in SQLite. If you preserve the data directory, you preserve past results, comments, review state, classifications, failure history, and lifecycle timestamps across restarts.

The most important thing to know is this: the SQLite database is the source of truth. The web UI reads stored results from `/results/{job_id}`, and there is no separate on-disk HTML report cache anymore.

## Default Storage Locations

By default, the database lives at `/data/results.db`. There is no separate `/data/reports` cache directory anymore. The user-facing report route is `/results/{job_id}`, and it reads the stored SQLite result instead of a cached HTML file.

```21:21:src/jenkins_job_insight/storage.py
DB_PATH = Path(os.getenv("DB_PATH", "/data/results.db"))
```

The `results` table is still created in that file at startup, and JJI now keeps lifecycle metadata such as `analysis_started_at` in SQLite itself.

```85:101:src/jenkins_job_insight/storage.py
async def init_db() -> None:
    """Initialize the database schema.

    Creates the results table if it does not exist.
    """
    logger.info(f"Initializing database at {DB_PATH}")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS results (
                job_id TEXT PRIMARY KEY,
                jenkins_url TEXT,
                status TEXT,
                result_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                analysis_started_at TIMESTAMP
            )
        """)
```

With the provided Docker Compose setup, the host `./data` directory is mounted into the container at `/data`, so the SQLite database survives container restarts.

```38:41:docker-compose.yaml
# Persist SQLite database across container restarts
# The ./data directory on host maps to /data in container
volumes:
  - ./data:/data
```

> **Note:** The service creates the database parent directory automatically at startup and runs schema creation plus migrations for you.

> **Tip:** If you set `DB_PATH`, only the SQLite file moves. There is no sibling report cache directory to move with it.

## What Gets Stored

Jenkins Job Insight stores more than just the final analysis summary. In practice, SQLite holds:

- One `results` row per analysis, keyed by `job_id`
- The current job status
- The serialized JSON payload for the result
- User comments on failures
- Reviewed/unreviewed state
- Manual and AI-generated test classifications
- Flattened failure history used by the History pages and queries

Direct `POST /analyze-failures` runs use the same persistence layer. They are stored in the same `results` table; they just do not have a Jenkins URL.

There is no separate HTML report file anymore. Browser report views are driven by the stored SQLite result at `/results/{job_id}`.

For Jenkins jobs in the `waiting` state, JJI stores an encrypted `request_params` payload inside `result_json` so the background task can be reconstructed after a restart. That payload now includes the resolved peer AI configuration, the peer-analysis round limit, and a `wait_started_at` timestamp so resumed jobs keep the original wait deadline instead of starting over.

```922:987:src/jenkins_job_insight/main.py
def _build_request_params(
    body: AnalyzeRequest,
    merged: Settings,
    ai_provider: str,
    ai_model: str,
    peer_ai_configs_resolved: list | None = None,
) -> dict:
    params = {
        # ... other persisted request settings ...
        "peer_ai_configs": [
            c.model_dump() if hasattr(c, "model_dump") else c
            for c in (peer_ai_configs_resolved or [])
        ],
        "peer_analysis_max_rounds": merged.peer_analysis_max_rounds,
        "wait_started_at": _time.time(),
    }
    return encrypt_sensitive_fields(params)
```

JJI also persists `progress_phase` and a timestamped `progress_log` inside `result_json`, so the status view can restore the same in-progress timeline after a refresh.

```628:659:src/jenkins_job_insight/storage.py
def _make_progress_phase_patcher(phase: str) -> Callable[[dict], None]:
    import time

    def _patcher(d: dict) -> None:
        d["progress_phase"] = phase
        progress_log = d.get("progress_log")
        if not isinstance(progress_log, list):
            progress_log = []
            d["progress_log"] = progress_log
        progress_log.append(
            {"phase": phase, "timestamp": time.time()}
        )

    return _patcher
```

## Result State Lifecycle

For persisted jobs, the state model now includes an explicit Jenkins-monitoring phase:

- `pending`: the job record exists, but analysis work has not started yet
- `waiting`: JJI has accepted the request and is polling Jenkins for the target build to finish
- `running`: AI analysis is actively in progress
- `completed`: the final JSON result has been written successfully
- `failed`: waiting or analysis ended with an error, and the stored payload contains error details

For asynchronous `POST /analyze`, JJI writes the row immediately. When `WAIT_FOR_COMPLETION=true` and Jenkins connectivity is available, the initial persisted state is `waiting`; otherwise it starts as `pending`. JJI now validates and resolves peer AI settings before saving the resumable payload so a restarted job uses the same provider/model mix that was queued originally.

```1008:1040:src/jenkins_job_insight/main.py
job_id = str(uuid.uuid4())
merged = _merge_settings(body, settings)

# Validate peer configs early -- fail fast before returning 202.
resolved_peers = _validate_peer_configs(body, merged)
jenkins_url = build_jenkins_url(
    merged.jenkins_url, body.job_name, body.build_number
)
initial_result: dict = {
    "job_name": body.job_name,
    "build_number": body.build_number,
}
can_resume_wait = merged.wait_for_completion and bool(merged.jenkins_url)
if can_resume_wait:
    initial_result["request_params"] = _build_request_params(
        body,
        merged,
        body.ai_provider or AI_PROVIDER,
        body.ai_model or AI_MODEL,
        peer_ai_configs_resolved=resolved_peers,
    )
await save_result(
    job_id,
    jenkins_url,
    "waiting" if can_resume_wait else "pending",
    initial_result,
)
```

Once background work begins, JJI updates the same row in place. `analysis_started_at` is stamped the first time the job reaches `running`, and `completed_at` is stamped the first time it reaches `completed`.

```531:560:src/jenkins_job_insight/storage.py
def _build_status_update_clause(
    status: str,
    result_json: str | None = None,
) -> tuple[list[str], list]:
    """Build the SET clause parts and params for a status update.

    Returns the set-clause fragments and the corresponding parameter list.
    The caller must append the trailing ``job_id`` parameter.
    """
    set_parts = ["status = ?"]
    params: list = [status]

    if result_json is not None:
        set_parts.append("result_json = ?")
        params.append(result_json)

    if status == "running":
        set_parts.append(
            "analysis_started_at = COALESCE(analysis_started_at, CURRENT_TIMESTAMP)"
        )
    if status == "completed":
        set_parts.append("completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)")
```

Direct `POST /analyze-failures` runs use the same persistence layer, but they skip `waiting` and follow the simpler `pending` -> `running` -> `completed` or `failed` flow.

> **Note:** On startup, orphaned `pending` and `running` rows are still marked `failed`, but `waiting` rows are resumed only when their stored payload is actually usable. A `waiting` row must have valid JSON plus `job_name`, `build_number`, and a non-empty `request_params` object; otherwise JJI marks it `failed` as unrecoverable instead of leaving it stuck in `waiting`.

```2096:2160:src/jenkins_job_insight/storage.py
cursor = await db.execute(
    "UPDATE results SET status = 'failed' "
    "WHERE status IN ('pending', 'running')"
)

cursor = await db.execute(
    "SELECT job_id, result_json FROM results WHERE status = 'waiting'"
)
rows = await cursor.fetchall()
for row in rows:
    if row["result_json"]:
        result_data = parse_result_json(
            row["result_json"], job_id=row["job_id"]
        )
        stored_params = (
            result_data.get("request_params") if result_data else None
        )
        is_resumable = (
            result_data is not None
            and isinstance(stored_params, dict)
            and bool(stored_params)
            and "job_name" in result_data
            and "build_number" in result_data
        )
        if is_resumable:
            waiting_jobs.append(
                {"job_id": row["job_id"], "result_data": result_data}
            )
        else:
            await db.execute(
                "UPDATE results SET status = 'failed' WHERE job_id = ?",
                (row["job_id"],),
            )
```

When a `waiting` job is resumed, JJI subtracts the already-elapsed wait time from `MAX_WAIT_MINUTES`. If the deadline already passed while the service was down, the resumed job is marked `failed` immediately.

```349:386:src/jenkins_job_insight/main.py
raw_wait_started_at = params.get("wait_started_at")
wait_started_at: float | None = None
if raw_wait_started_at is not None:
    try:
        wait_started_at = float(raw_wait_started_at)
    except (TypeError, ValueError):
        await _fail_resumed_waiting_job(
            job["job_id"],
            result_data,
            f"Cannot resume: malformed wait_started_at value: {raw_wait_started_at!r}",
        )
        continue

if merged.max_wait_minutes > 0 and wait_started_at is not None:
    elapsed_minutes = (_time.time() - wait_started_at) / 60
    remaining = merged.max_wait_minutes - elapsed_minutes
    if remaining <= 0:
        await _fail_resumed_waiting_job(
            job["job_id"],
            result_data,
            (
                f"Timed out waiting for Jenkins job "
                f"{result_data.get('job_name')} #{result_data.get('build_number')} "
                f"after {merged.max_wait_minutes} minutes (deadline passed during restart)"
            ),
        )
        continue
```

## Result Route and Browser Behavior

The browser-facing report path is now `/results/{job_id}`. There is no separate `.html` endpoint and no on-disk HTML cache. Instead, JJI uses content negotiation on the same route: browsers get the React app, while API clients get JSON.

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

When a browser opens an in-progress job, the backend sends it to `/status/{job_id}`. That page polls the JSON result endpoint until the job completes, then returns the user to the report route.

```61:76:frontend/src/pages/StatusPage.tsx
async function poll() {
  if (inFlight || cancelled) return
  inFlight = true
  try {
    const res = await api.get<ResultResponse>(`/results/${jobId}`)
    if (cancelled) return
    setError('')
    setData(res)
    if (res.status === 'completed') {
      stopPolling()
      navigate(`/results/${jobId}`, { replace: true })
    } else if (res.status === 'failed') {
      stopPolling()
      setTerminalErrorKind('failed')
      setError(res.result?.error ?? 'Analysis failed')
    }
```

> **Tip:** API responses now include `result_url` instead of `html_report_url`. If `PUBLIC_BASE_URL` is unset, that link is intentionally relative, for example `/results/{job_id}`.

## Cleanup

Because there is no filesystem report cache anymore, JJI does not need a cache invalidation step when comments, review state, or classifications change. Those updates live in SQLite and are reflected the next time the UI fetches the latest result and comment data.

When you delete a job, JJI removes the stored result and its related database records. There is no cached report file to clean up.

```1819:1831:src/jenkins_job_insight/storage.py
async def delete_job(job_id: str) -> bool:
    """Delete an analyzed job and all its related data."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Delete from all related tables
        await db.execute("DELETE FROM comments WHERE job_id = ?", (job_id,))
        await db.execute("DELETE FROM failure_reviews WHERE job_id = ?", (job_id,))
        await db.execute("DELETE FROM failure_history WHERE job_id = ?", (job_id,))
        await db.execute("DELETE FROM test_classifications WHERE job_id = ?", (job_id,))
        cursor = await db.execute("DELETE FROM results WHERE job_id = ?", (job_id,))
        job_existed = cursor.rowcount > 0
        await db.commit()

        return job_existed
```

> **Warning:** Deleting a job removes the stored JSON result, related comments, reviews, failure history, and classifications. There is no separate cached report artifact to keep.

## Historical Failure Data

Completed analyses are also flattened into `failure_history`, which powers the History UI and history-related API queries. On startup, the service backfills missing history rows from older completed results, so existing completed analyses can still appear in history after upgrades or schema changes.

> **Note:** Only completed results are backfilled into historical failure data. Jobs in `waiting`, `pending`, `running`, or `failed` states are not treated as historical runs.
