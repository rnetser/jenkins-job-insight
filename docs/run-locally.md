I’ve got the pieces I need. The final page will explain the safest local startup pattern, call out the writable `DB_PATH` requirement, show both supported launch methods, and note the browser redirect behavior that affects `/docs` and `/openapi.json` until you register a username.# Run Locally

You can bring `jenkins-job-insight` up locally in two ways: by using the package's console entrypoint or by running `uvicorn` directly. For an API-level smoke test, you only need Python 3.12+, `uv`, and a writable database path. If you also want browser-based checks that can redirect to `/register`, build the frontend once with Node.js and npm. You do **not** need Jenkins or AI credentials just to verify `/health`, `/docs`, and `/openapi.json`.

```1:24:pyproject.toml
[project]
name = "jenkins-job-insight"
version = "2.0.0"
description = "Jenkins job insight and analysis tool"
requires-python = ">=3.12"
dependencies = [
  "ai-cli-runner>=0.1.1",
  "aiosqlite",
  "cryptography>=46.0.5",
  "defusedxml>=0.7.1",
  "fastapi",
  "gitpython",
  "httpx",
  "pydantic-settings",
  "python-jenkins",
  "python-multipart",
  "python-simple-logger",
  "typer>=0.9.0",
  "uvicorn",
]

[project.scripts]
jenkins-job-insight = "jenkins_job_insight.main:run"
jji = "jenkins_job_insight.cli.main:app"
```

## Before You Start

Install the project with the locked dependencies, then set the small amount of local configuration the app needs to start cleanly.

> **Warning:** The app defaults to `DB_PATH=/data/results.db`. That works well in containers, but `/data` is often not writable on a normal workstation. Set `DB_PATH` to a project-local path before starting the server.

```21:92:src/jenkins_job_insight/storage.py
DB_PATH = Path(os.getenv("DB_PATH", "/data/results.db"))

# ...

async def init_db() -> None:
    """Initialize the database schema.

    Creates the results table if it does not exist.
    """
    logger.info(f"Initializing database at {DB_PATH}")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
```

```bash
uv sync --frozen

# Optional for browser-based checks that may redirect to /register
cd frontend
npm install
npm run build
cd ..

export DB_PATH="$PWD/data/results.db"
export PORT=8000
# Optional when using the packaged entrypoint:
# export DEBUG=true
```

If you leave `PORT` unset, the app uses `8000`.

> **Tip:** You do not need to create the `data/` directory yourself. The app creates the parent directory for the SQLite database on startup.

## Start with the Packaged Entrypoint

The installed `jenkins-job-insight` command is the package entrypoint for the service. It calls the app's `run()` function, which starts `uvicorn` on `0.0.0.0` using the `PORT` environment variable.

```2386:2393:src/jenkins_job_insight/main.py
def run() -> None:
    """Entry point for the CLI."""
    import uvicorn

    reload = os.getenv("DEBUG", "").lower() == "true"
    uvicorn.run(
        "jenkins_job_insight.main:app", host="0.0.0.0", port=APP_PORT, reload=reload
    )
```

```bash
uv run jenkins-job-insight
```

If you exported `DEBUG=true`, this launch path enables auto-reload.

## Start with `uvicorn` Directly

If you prefer to run the ASGI app yourself, start the same app object directly:

```bash
uv run uvicorn jenkins_job_insight.main:app --host 0.0.0.0 --port "$PORT"
```

This is the most direct way to run the service if you already work with ASGI apps and want full control over `uvicorn` flags.

## Verify the Service

### Check `/health`

The health endpoint returns a minimal JSON payload:

```2344:2347:src/jenkins_job_insight/main.py
@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy"}
```

```bash
curl http://localhost:$PORT/health
```

Expected response:

```json
{"status": "healthy"}
```

### Check the OpenAPI document

Fetch the schema directly:

```bash
curl -s "http://localhost:$PORT/openapi.json" | python -m json.tool
```

The test suite confirms that the generated schema exposes the expected title and version, and that `/docs` is available:

```933:944:tests/test_main.py
def test_openapi_schema_available(self, test_client) -> None:
    """Test that OpenAPI schema is available."""
    response = test_client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Jenkins Job Insight"
    assert schema["info"]["version"] == "0.1.0"

def test_docs_available(self, test_client) -> None:
    """Test that docs endpoint is available."""
    response = test_client.get("/docs")
    assert response.status_code == 200
```

When the service is running, you should see `"title": "Jenkins Job Insight"` and `"version": "0.1.0"` in the schema output.

### Check `/docs`

Open `http://localhost:$PORT/docs` in your browser.

> **Note:** Browser requests without a `jji_username` cookie are still redirected to `/register`. That route is served by the React frontend, so if the browser lands on `Frontend not built`, run the frontend build commands above. Once the register page loads, enter any username and then reopen `/docs`.

```440:463:src/jenkins_job_insight/main.py
class UsernameMiddleware(BaseHTTPMiddleware):
    """Middleware that checks for jji_username cookie and redirects to /register if missing."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Allow register page, health check, static assets, and API paths without auth
        if (
            path in ("/register", "/health", "/favicon.ico", "/api")
            or path.startswith("/register")
            or path.startswith("/assets/")
            or path.startswith("/api/")
        ):
            return await call_next(request)

        username = request.cookies.get("jji_username", "")
        request.state.username = username

        # Only redirect browser (HTML) requests without auth
        if not username:
            accept = request.headers.get("accept", "")
            if "text/html" in accept:
                return RedirectResponse(url="/register", status_code=303)

        return await call_next(request)
```

```2360:2365:src/jenkins_job_insight/main.py
def _serve_spa() -> HTMLResponse:
    """Read and serve the React SPA index.html."""
    index_file = _FRONTEND_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="Frontend not built")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
```

> **Tip:** Use `curl` for `/docs` and `/openapi.json` if you want to avoid that browser redirect entirely during a quick smoke test.

## When You Move Past Smoke Testing

You can start the service without Jenkins or AI credentials, but before you call analysis endpoints such as `/analyze`, install and authenticate one supported AI provider CLI: Claude, Gemini, or Cursor. You will also need the matching provider settings in your environment. The repository's env template shows the expected values:

```4:19:.env.example
# Jenkins Configuration (Required)
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
```

Export those values in your shell before you start the app when you are ready to analyze real Jenkins jobs.
