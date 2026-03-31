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

There is no separate HTML report file anymore. Browser report views are driven by the stored SQLite result at `/results/{job_id}`. For Jenkins jobs in the `waiting` state, JJI can also store encrypted `request_params` inside `result_json` so the job can resume after a restart.

## Result State Lifecycle

For persisted jobs, the state model now includes an explicit Jenkins-monitoring phase:

- `pending`: the job record exists, but analysis work has not started yet
- `waiting`: JJI has accepted the request and is polling Jenkins for the target build to finish
- `running`: AI analysis is actively in progress
- `completed`: the final JSON result has been written successfully
- `failed`: waiting or analysis ended with an error, and the stored payload contains error details

For asynchronous `POST /analyze`, JJI writes the row immediately. When `WAIT_FOR_COMPLETION=true` and Jenkins connectivity is available, the initial persisted state is `waiting`; otherwise it starts as `pending`.

```942:967:src/jenkins_job_insight/main.py
jenkins_url = build_jenkins_url(
    merged.jenkins_url, body.job_name, body.build_number
)
# Save initial pending state before queueing background task.
# Only persist request_params for waiting jobs (wait_for_completion),
# since those are the only ones that need resumption after restart.
# This avoids storing encrypted secrets with no operational use.
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
    )
await save_result(
    job_id,
    jenkins_url,
    "waiting" if can_resume_wait else "pending",
    initial_result,
)
background_tasks.add_task(process_analysis_with_id, job_id, body, merged)
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

> **Note:** On startup, orphaned `pending` and `running` rows are marked `failed`, while resumable `waiting` rows are kept for restart-safe resumption.

```2049:2077:src/jenkins_job_insight/storage.py
async def mark_stale_results_failed() -> list[dict]:
    """Mark orphaned pending/running jobs as failed. Return waiting jobs for resumption.

    Pending and running jobs have lost their background task and cannot recover,
    so they are marked as failed.  Waiting jobs were polling Jenkins and can be
    safely resumed by re-creating their background task.

    Returns:
        List of dicts with ``job_id`` and ``result_data`` for each waiting job.
    """
    waiting_jobs: list[dict] = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Mark pending/running as failed (background task is gone)
        cursor = await db.execute(
            "UPDATE results SET status = 'failed' "
            "WHERE status IN ('pending', 'running')"
        )

        # Collect waiting jobs for resumption instead of failing them
        cursor = await db.execute(
            "SELECT job_id, result_json FROM results WHERE status = 'waiting'"
        )
        rows = await cursor.fetchall()
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
