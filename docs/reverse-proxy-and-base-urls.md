# Reverse Proxy and Base URL Handling

When Jenkins Job Insight (JJI) runs behind an ingress, route, or reverse proxy, the public links it returns are no longer derived from forwarded headers. JJI uses the trusted `PUBLIC_BASE_URL` setting when you provide it; otherwise it returns origin-relative links such as `/results/{job_id}`.

The simplest production setup is:
- publish JJI at `/` on its own host or subdomain
- set `PUBLIC_BASE_URL` to the public URL users should open, bookmark, and share
- route both the API and the React UI through the same JJI service
- avoid subpath deployments unless your proxy can fully rewrite the SPA routes and assets

> **Note:** JJI now has a dedicated `PUBLIC_BASE_URL` setting. It is a trusted server-side base URL for public links, and request headers are intentionally not used as a fallback.

## How JJI Chooses `base_url`

JJI no longer calculates `base_url` from `Host` or `X-Forwarded-*` request headers. The current implementation trusts only the server-side `PUBLIC_BASE_URL` setting. When that setting is unset, JJI returns an empty `base_url` and builds relative links instead of guessing from request metadata.

```70:74:src/jenkins_job_insight/config.py
# Trusted public base URL — used for result_url and tracker links.
# When set, _extract_base_url() returns this value verbatim.
# When unset, _extract_base_url() returns an empty string (relative
# URLs only) — request Host / X-Forwarded-* headers are never trusted.
public_base_url: str | None = None
```

```142:160:src/jenkins_job_insight/main.py
def _extract_base_url() -> str:
    settings = get_settings()
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")

    logger.debug(
        "PUBLIC_BASE_URL is not set; returning empty base URL (relative paths)"
    )
    return ""
```

In practice, that means:
- `PUBLIC_BASE_URL` is the only source of absolute public links.
- The configured value is used verbatim except that a trailing slash is stripped. For example, `https://example.com/jji/` becomes `https://example.com/jji`.
- If `PUBLIC_BASE_URL` is unset, `base_url` becomes `""` and `result_url` falls back to `/results/{job_id}`.
- Request headers such as `Host`, `X-Forwarded-Proto`, `X-Forwarded-Host`, `X-Forwarded-Port`, `X-Forwarded-Prefix`, and `Forwarded` do not affect `base_url`.

The tests in `tests/test_main.py` cover both branches: `PUBLIC_BASE_URL` wins when set, and forwarded headers are ignored when it is not.

> **Warning:** If you need absolute links in API responses, enriched XML, or issue previews, you must set `PUBLIC_BASE_URL`. Forwarded headers alone no longer change the generated links.

> **Tip:** Include the public port in `PUBLIC_BASE_URL` when you use a non-standard port, for example `https://jji.example.com:8443`.

## How `result_url` and Report URLs Are Built

JJI now attaches only `base_url` and `result_url` to API responses. There is no separate `html_report_url` field anymore. The canonical browser/API report route is `/results/{job_id}`.

```197:202:src/jenkins_job_insight/main.py
def _attach_result_links(payload: dict, base_url: str, job_id: str) -> dict:
    payload["base_url"] = base_url
    result_url = f"{base_url}/results/{job_id}"
    payload["result_url"] = result_url
    return payload
```

That gives you:
- `result_url` as `{PUBLIC_BASE_URL}/results/{job_id}` when `PUBLIC_BASE_URL` is set
- `result_url` as `/results/{job_id}` when `PUBLIC_BASE_URL` is unset
- the same `/results/{job_id}` pattern for both Jenkins-backed and direct failure analysis
- no `html_report_url` response field

Browsers and JSON clients share the same `GET /results/{job_id}` endpoint. Browsers get the React app, and in-progress jobs are redirected to `/status/{job_id}`:

```1146:1169:src/jenkins_job_insight/main.py
@app.get("/results/{job_id}", response_model=None)
async def get_job_result(request: Request, job_id: str, response: Response):
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        result = await get_result(job_id)
        if result and result.get("status") in IN_PROGRESS_STATUSES:
            return RedirectResponse(url=f"/status/{job_id}", status_code=302)
        return _serve_spa()

    # ... more code omitted ...
    _attach_result_links(result, _extract_base_url(), job_id)
```

Other report links follow the same pattern:
- issue preview endpoints use `{base_url}/results/{job_id}` only when `include_links=true` and `base_url` is non-empty
- otherwise the report link included in those previews falls back to `/results/{job_id}`
- enriched JUnit XML writes a `report_url` property on the first `testsuite`

```163:194:src/jenkins_job_insight/main.py
def _build_report_context(
    include_links: bool,
    base_url: str,
    job_id: str,
    result_data: dict,
) -> tuple[str, str]:
    # ... more code omitted ...
    if include_links and base_url:
        report_url = f"{base_url}/results/{job_id}"
    else:
        report_url = f"/results/{job_id}"
```

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

> **Note:** `result_url` is now the canonical link to both the stored result and the browser report page.

> **Tip:** If you want absolute report links in enriched XML or bug previews, set `PUBLIC_BASE_URL`. Otherwise those links stay relative to the current origin.

## Reverse Proxy and Ingress Checklist

The example Compose setup still publishes a single service port for both the API and the React UI:

```32:36:docker-compose.yaml
# Ports: Web UI + API served on the same port
ports:
  - "8000:8000"   # Web UI (React) + REST API
  # Dev mode: Vite HMR for frontend hot-reload (uncomment with DEV_MODE=true)
  # - "5173:5173"
```

For a stable public deployment:
- route both browser traffic and API traffic to the same JJI service port
- set `PUBLIC_BASE_URL` to the exact public base URL users should open and share
- include the public port in `PUBLIC_BASE_URL` when it is not `80` or `443`
- keep the proxy, service, and container port mapping in sync
- prefer publishing JJI at `/` on a dedicated host or subdomain

> **Note:** Forwarded headers such as `Host`, `X-Forwarded-Proto`, `X-Forwarded-Host`, `X-Forwarded-Port`, and `X-Forwarded-Prefix` no longer control `result_url`.

## Path Prefixes and Subpath Deployments

Serving JJI at `/` is still the most predictable setup. The backend will include any configured prefix in `PUBLIC_BASE_URL`, but the frontend itself is built for root-based routes and assets.

```15:24:frontend/src/App.tsx
<BrowserRouter basename="/">
  <Routes>
    <Route path="/register" element={<RegisterPage />} />
    <Route element={<Layout />}>
      <Route index element={<ProtectedRoute><DashboardPage /></ProtectedRoute>} />
      <Route path="/dashboard" element={<Navigate to="/" replace />} />
      <Route path="/history" element={<ProtectedRoute><HistoryPage /></ProtectedRoute>} />
      <Route path="/history/test/:testName" element={<ProtectedRoute><TestHistoryPage /></ProtectedRoute>} />
      <Route path="/results/:jobId" element={<ProtectedRoute><ReportPage /></ProtectedRoute>} />
      <Route path="/status/:jobId" element={<ProtectedRoute><StatusPage /></ProtectedRoute>} />
      {/* ... more routes omitted ... */}
```

```21:24:frontend/vite.config.ts
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/',
  resolve: {
  // ... more config omitted ...
```

In practice, that means:
- setting `PUBLIC_BASE_URL=https://example.com/jji` makes emitted links look like `https://example.com/jji/results/{job_id}`
- the React router still expects browser paths rooted at `/`, not at `/jji`
- the built frontend assets are also emitted for `/`
- JJI does not auto-detect or adapt to a proxy path prefix

> **Warning:** Setting `PUBLIC_BASE_URL` with a prefix is not the same as full subpath support. If the public URL is `https://example.com/jji`, you need a proxy setup that rewrites the SPA routes and assets accordingly; otherwise the generated link may exist, but the app will not behave like a native `/jji` deployment.

> **Tip:** For the least surprising behavior, publish JJI at `/` on a dedicated hostname or subdomain such as `https://jji.example.com`.

## Troubleshooting

- `result_url` comes back as `/results/...` instead of `https://...`: `PUBLIC_BASE_URL` is unset. This is expected when no trusted public base URL is configured.
- `result_url` points to the wrong host, scheme, or port: `PUBLIC_BASE_URL` is set incorrectly. Check the exact external URL, including any non-standard port.
- Your proxy sends `X-Forwarded-Proto` and `X-Forwarded-Host`, but the returned links do not change: expected. JJI ignores forwarded headers for public link construction.
- Enriched XML or bug previews contain a relative report link: `PUBLIC_BASE_URL` is unset, or a preview request used `include_links=false`.
- A `/jji` or other prefixed deployment loads inconsistently, refreshes to 404, or misses assets: the frontend is still built for `/`, so the proxy is not rewriting the prefix in a way the SPA can use.
