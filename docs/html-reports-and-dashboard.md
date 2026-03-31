# HTML Reports and Dashboard

`jenkins-job-insight` ships with a **React** browser UI (SPA) on top of stored analysis results. Register at `/register`, use `/` as the **dashboard**, open **`/results/<job_id>`** for a full report for one run, and use **`/history`** (and `/history/test/:testName`) for history views. The `/dashboard` path **redirects to `/`**. The analysis APIs attach **`result_url`** (not `html_report_url`) so automation can hand reviewers a ready-to-open link to the same SPA route.

`GET /results/{job_id}` **content-negotiates**: browser HTML requests get the SPA; in-progress browser requests are redirected to `/status/{job_id}`; API clients get JSON. There are **no** server-rendered `/results/<job_id>.html` pages, **no** HTML report cache, **`?refresh=1`**, or regenerate control.

Main routes:

```tsx
<Route path="/register" element={<RegisterPage />} />
<Route element={<Layout />}>
  <Route index element={<ProtectedRoute><DashboardPage /></ProtectedRoute>} />
  <Route path="/dashboard" element={<Navigate to="/" replace />} />
  <Route path="/history" element={<ProtectedRoute><HistoryPage /></ProtectedRoute>} />
  <Route path="/history/test/:testName" element={<ProtectedRoute><TestHistoryPage /></ProtectedRoute>} />
  <Route path="/results/:jobId" element={<ProtectedRoute><ReportPage /></ProtectedRoute>} />
  <Route path="/status/:jobId" element={<ProtectedRoute><StatusPage /></ProtectedRoute>} />
</Route>
```

```python
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
```

> **Note:** Browser requests without a `jji_username` cookie are redirected to `/register`. That name is then used for comments, review toggles, and other interactive UI actions.

## Stored results and the SPA

The service **stores structured analysis results first**; the React app loads them via JSON. There is **no** lazy server-side HTML render, **no** on-disk HTML report cache, and **no** `?refresh=1` path for rebuilding HTML.

For **browsers**, `GET /results/{job_id}` serves the SPA or redirects in-progress work to the status route (see handler below). For **API clients**, the same path returns JSON. You can open `/results/{job_id}` as soon as a job exists; while it is still in progress, browser navigation lands on `/status/{job_id}` instead of a completed report.

```python
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
```

The default container setup persists the **SQLite database** under `/data` so data survives restarts in normal deployments.

```yaml
# Persist SQLite database across container restarts
# The ./data directory on host maps to /data in container
volumes:
  - ./data:/data
```

## Refresh and polling behavior

There is **no** HTML cache, HTTP `Refresh` header on a static status document, **`?refresh=1`**, or **Regenerate** control.

- **Dashboard:** refetches `GET /api/dashboard` **every 10 seconds**.

```typescript
useEffect(() => {
  fetchJobs()
  const interval = setInterval(fetchJobs, 10_000)
  return () => clearInterval(interval)
}, [fetchJobs])
```

- **Status page (queued / running / failed before completion):** polls `GET /results/{job_id}` **every 10 seconds**; when status becomes `completed`, it **navigates to `/results/{jobId}`**.

```typescript
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
```

- **Report page:** loads the analysis **once**; **comments / reviews** are polled on an interval (default **30 seconds** via `COMMENT_POLL_MS`, configurable with `VITE_COMMENT_POLL_MS`). Polling is skipped while a comment draft is open.

```typescript
useEffect(() => {
  if (!jobId || state.error || !state.result) return

  const interval = setInterval(() => {
    if (state.commentDraftCount > 0) return
    fetchComments(jobId)
  }, COMMENT_POLL_MS)

  return () => {
    clearInterval(interval)
  }
}, [jobId, fetchComments, state.commentDraftCount, state.error, state.result])
```

## Grouped failure cards

The report’s **Failures** section is grouped for noisy builds: one card can represent many tests that share the same underlying error.

**Grouping is strict:** use `error_signature` when present; **otherwise** each failure is its own group keyed by test name (via a `unique-…` key).

```typescript
/** Compute grouping key — matches Python _grouping_key(). */
export function groupingKey(failure: FailureAnalysis): string {
  return failure.error_signature || `unique-${failure.test_name}`
}
```

Each grouped card typically includes the AI analysis, classification controls, affected tests, comments, and issue actions. For groups with more than one failure, the UI can **Review All** for the whole group:

```tsx
{group.count > 1 && (
  <div className="flex items-center gap-2">
    <button
      onClick={handleReviewAll}
      disabled={reviewingAll}
      className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-bold transition-colors ${
        allReviewed
          ? 'bg-signal-green/15 text-signal-green'
          : 'bg-surface-elevated text-text-tertiary hover:text-text-secondary'
      }`}
    >
      <CheckCircle2 className="h-4 w-4" />
      {allReviewed ? 'All Reviewed' : `Review All (${reviewedCount}/${group.count})`}
    </button>
```

**Classification overrides** still update **all** failures in the same job that share the same `error_signature`, so grouped cards stay in sync:

```python
"""Override the classification of a failure in failure_history.

Updates ALL failure_history rows sharing the same error_signature
(within the same job) so that grouped failures stay in sync.
Also inserts a test_classifications entry so the AI can learn from
human overrides.
"""
```

Child pipeline jobs use the same grouped model under nested **Child Job** sections.

> **Tip:** Start with grouped cards when a build looks overwhelming—they are the quickest way to see how many distinct error signatures you have.

## Browsing a report

A full report is laid out in the React UI with:

- a **sticky header** with build context, status, AI provider/model, analysis timestamp, failure counts, and Jenkins links when available
- an optional **Key Takeaway** when a summary exists

```tsx
{result.summary && (
  <div className="rounded-lg border-l-4 border-l-signal-orange bg-glow-orange p-4 animate-slide-up">
    <h2 className="text-xs font-display uppercase tracking-widest text-signal-orange mb-2">Key Takeaway</h2>
    <p className="text-sm text-text-secondary whitespace-pre-wrap">{result.summary}</p>
  </div>
)}
```

- **Failures** — grouped cards (section title includes total failure count); this is the primary list of failing tests—there is **no** separate **All Failures** table or `Bug Ref` column tying back to HTML-era `BUG-n` labels.

```tsx
{groups.length > 0 && (
  <section>
    <div className="flex items-center justify-between mb-3">
      <h2 className="text-xs font-display uppercase tracking-widest text-text-tertiary">
        Failures ({(result.failures ?? []).length})
      </h2>
```

- **Child job analyses** — nested sections for downstream jobs; **URL hash** targets auto-expand matching sections and scroll them into view for deep links:

```typescript
// Auto-expand and scroll when the URL hash targets this child job or any descendant
useEffect(() => {
  if (activeHash && (activeHash === hashId || activeHash.startsWith(`${hashId}--`))) {
    if (!expanded) {
      setExpanded(true)
    }
    if (activeHash === hashId) {
      requestAnimationFrame(() => {
        sectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
```

- **Comments** and **review** toggles for tracking investigation in-place on each failure card or group.

## Dashboard search and pagination

The dashboard is at **`/`** ( **`/dashboard` redirects to `/`** ). It is a **table** of recent jobs (not cards). The page loads jobs from **`GET /api/dashboard`** and **refetches every 10 seconds**.

```typescript
useEffect(() => {
  fetchJobs()
  const interval = setInterval(fetchJobs, 10_000)
  return () => clearInterval(interval)
}, [fetchJobs])
```

**Search** filters only **`job_name`** and **`job_id`** (case-insensitive substring match):

```typescript
const filtered = useMemo(() => {
  if (!search) return jobs
  const q = search.toLowerCase()
  return jobs.filter((j) => {
    const haystack = `${j.job_name ?? ''} ${j.job_id}`.toLowerCase()
    return haystack.includes(q)
  })
}, [jobs, search])
```

**Pagination** is client-side on the filtered rows. Page-size options are **10**, **20**, and **50**.

**Row navigation:** clicking a row goes to **`/status/{job_id}`** for `waiting`, `pending`, or `running` jobs; otherwise to **`/results/{job_id}`**.

```typescript
function getJobRoute(job: DashboardJob): string {
  return ['waiting', 'pending', 'running'].includes(job.status)
    ? `/status/${job.job_id}`
    : `/results/${job.job_id}`
}
```

The backend default dashboard window is **`DEFAULT_DASHBOARD_LIMIT = 500`** in `storage.py`. The React dashboard has **no** “Load last” or server `limit` control in the UI—you work within that default list.

> **Note:** Search and pagination apply only to jobs already returned in the dashboard list.

> **Tip:** If a run is outside that window, use other flows (for example **`/history`**) to find it.

When there are no jobs, the dashboard shows an empty state.
