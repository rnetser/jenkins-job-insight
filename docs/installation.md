# Installation

`jenkins-job-insight` is a Python application that installs two commands: `jenkins-job-insight` to run the server and `jji` to talk to that server from the command line. The package metadata requires Python 3.12 or newer.

```1:27:pyproject.toml
[project]
name = "jenkins-job-insight"
version = "1.0.0"
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

[project.optional-dependencies]
tests = ["pytest", "pytest-asyncio"]
```

## Prerequisites

Before installing locally, make sure you have:

- Python 3.12 or newer.
- A Jenkins URL, username, and password or API token if you plan to analyze Jenkins builds.
- Jenkins permissions to read build metadata, console output, and test reports for the jobs you want to analyze.
- One supported AI CLI installed and authenticated: `claude`, `gemini`, or `cursor`.

If you only plan to analyze raw failures or raw JUnit XML through `/analyze-failures`, you can install and start the service without setting Jenkins credentials up front.

For Jenkins-backed analysis, the service reads Jenkins build data directly, including console output and the Jenkins test report API:

```41:85:src/jenkins_job_insight/jenkins.py
def get_build_console(self, job_name: str, build_number: int) -> str:
    """Get console output for a build.
    ...
    """
    logger.debug(f"Fetching console output: {job_name} #{build_number}")
    return self.get_build_console_output(job_name, build_number)

def get_build_info_safe(self, job_name: str, build_number: int) -> dict:
    """Get build information safely.
    ...
    """
    logger.debug(f"Fetching build info: {job_name} #{build_number}")
    return super().get_build_info(job_name, build_number)

def get_test_report(self, job_name: str, build_number: int) -> dict | None:
    """Get test report for a build.

    Uses the Jenkins /testReport/api/json endpoint which provides structured
    test results with all failures in a parseable format.
```

> **Tip:** If your Jenkins uses a self-signed certificate, set `JENKINS_SSL_VERIFY=false`. If your Jenkins account cannot read build artifacts, set `GET_JOB_ARTIFACTS=false`; artifact downloads are enabled by default.

## Install And Authenticate An AI CLI

This project uses external AI CLIs rather than a Python SDK. Installing the Python package does not install or authenticate Claude, Gemini, or Cursor for you.

The repository’s container build installs the provider CLIs separately:

```47:56:Dockerfile
# Install Claude Code CLI (installs to ~/.local/bin)
RUN /bin/bash -o pipefail -c "curl -fsSL https://claude.ai/install.sh | bash"

# Install Cursor Agent CLI (installs to ~/.local/bin)
RUN /bin/bash -o pipefail -c "curl -fsSL https://cursor.com/install | bash"

# Configure npm for non-root global installs and install Gemini CLI
RUN mkdir -p /home/appuser/.npm-global \
    && npm config set prefix '/home/appuser/.npm-global' \
    && npm install -g @google/gemini-cli
```

Use the provider you plan to run:

- `claude`: authenticate with `ANTHROPIC_API_KEY`, or use Vertex AI with `CLAUDE_CODE_USE_VERTEX=1`, `CLOUD_ML_REGION`, and `ANTHROPIC_VERTEX_PROJECT_ID`.
- `gemini`: authenticate with `GEMINI_API_KEY`, or run `gemini auth login`.
- `cursor`: `.env.example` documents `CURSOR_API_KEY` as the local auth variable.

> **Note:** The application expects the selected AI CLI to already be installed and authenticated before you start analyzing jobs.

## Configure Your Environment

The settings model supports a local `.env` file:

```16:19:src/jenkins_job_insight/config.py
model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
)
```

A practical starting point is the repository’s `.env.example` file:

```6:45:.env.example
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

# --- Claude CLI Options ---

# Option 1: Direct API key (simplest)
ANTHROPIC_API_KEY=your-anthropic-api-key

# Option 2: Vertex AI authentication
# CLAUDE_CODE_USE_VERTEX=1
# CLOUD_ML_REGION=us-east5
# ANTHROPIC_VERTEX_PROJECT_ID=your-project-id

# --- Gemini CLI Options ---

# Option 1: API key
GEMINI_API_KEY=your-gemini-api-key

# Option 2: OAuth (run: gemini auth login)
# No env vars needed for OAuth

# --- Cursor Agent CLI Options ---

# Choose ONE of the following authentication methods:

# API key
# CURSOR_API_KEY=your-cursor-api-key
```

Fill in at least these values before you start the server:

- `AI_PROVIDER`
- `AI_MODEL`

If you plan to analyze Jenkins jobs, also set:

- `JENKINS_URL`
- `JENKINS_USER`
- `JENKINS_PASSWORD`

If you only plan to analyze raw failures or raw JUnit XML through `/analyze-failures`, you can leave the Jenkins values unset.

> **Warning:** For local package installs, set `AI_PROVIDER` and `AI_MODEL` in the environment that starts the server. `src/jenkins_job_insight/main.py` reads them directly from `os.environ`, so relying on `.env` alone is not the safest local setup.

```86:87:src/jenkins_job_insight/main.py
AI_PROVIDER = os.getenv("AI_PROVIDER", "").lower()
AI_MODEL = os.getenv("AI_MODEL", "")
```

> **Warning:** The default SQLite database path is `/data/results.db`. That works well in the container image, but many local environments will need a writable project-local path instead. Set `DB_PATH` before starting the server if you are running outside the container.

```13:14:src/jenkins_job_insight/storage.py
DB_PATH = Path(os.getenv("DB_PATH", "/data/results.db"))
REPORTS_DIR = DB_PATH.parent / "reports"
```

> **Note:** Jira, GitHub issue creation, and test-repository settings are optional. You only need the AI configuration to install and start the service, plus Jenkins settings if you plan to analyze Jenkins jobs.

## Install The Package Locally

If you want to use the same dependency-install path the repository uses in its own build, install with `uv` from the repository root and run `uv sync --frozen --no-dev`:

```34:35:Dockerfile
# Create venv and install dependencies
RUN uv sync --frozen --no-dev
```

That installs the backend application and the two console commands from `pyproject.toml`: `jenkins-job-insight` and `jji`.

If you also want the repository's backend test dependencies, the project now exposes a `tests` optional dependency group:

```26:27:pyproject.toml
[project.optional-dependencies]
tests = ["pytest", "pytest-asyncio"]
```

If you want the local React dashboard and report pages too, you also need Node.js and npm so you can install the frontend dependencies and run the `build` script once from `frontend/`. The server serves `frontend/dist`, and it returns `Frontend not built` if that bundle is missing:

```424:431:src/jenkins_job_insight/main.py
# React frontend static assets
_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _FRONTEND_DIR.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_FRONTEND_DIR / "assets")),
        name="frontend-assets",
    )
```

```2282:2287:src/jenkins_job_insight/main.py
def _serve_spa() -> HTMLResponse:
    """Read and serve the React SPA index.html."""
    index_file = _FRONTEND_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="Frontend not built")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
```

The frontend scripts are defined in `frontend/package.json`:

```6:14:frontend/package.json
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "tsc -b"
  },
```

> **Tip:** If you use a `uv`-managed environment without activating it, run the installed commands with `uv run`, for example `uv run jenkins-job-insight`.

## Start The Server

After installation and environment setup, start the application with `jenkins-job-insight`. The entry point launches Uvicorn, binds to `0.0.0.0`, and uses `PORT` if you set it. If you do not set `PORT`, the app defaults to `8000`.

```1952:1958:src/jenkins_job_insight/main.py
def run() -> None:
    """Entry point for the CLI."""
    import uvicorn

    reload = os.getenv("DEBUG", "").lower() == "true"
    uvicorn.run(
        "jenkins_job_insight.main:app", host="0.0.0.0", port=APP_PORT, reload=reload
    )
```

The service exposes a simple health endpoint:

```1936:1939:src/jenkins_job_insight/main.py
@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy"}
```

Once the server is running, open `http://localhost:8000/health`. A healthy server returns `{"status": "healthy"}`.

## Verify The CLI

The bundled `jji` CLI talks to the running server. It reads `JJI_SERVER` by default. `--server` accepts either a full URL or a named config profile, and `--user` sets the name shown in comments and reviews.

```167:204:src/jenkins_job_insight/cli/main.py
@app.callback()
def main_callback(
    ctx: typer.Context,
    server: str = typer.Option(
        None,
        "--server",
        "-s",
        envvar="JJI_SERVER",
        help="Server name from config or URL (required unless configured in config).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON instead of table.",
    ),
    username: str = typer.Option(
        "",
        "--user",
        envvar="JJI_USERNAME",
        help="Username displayed in comments and reviews.",
    ),
    no_verify_ssl: bool | None = typer.Option(
        None,
        "--no-verify-ssl",
        envvar="JJI_NO_VERIFY_SSL",
        help="Disable SSL certificate verification for HTTPS connections.",
    ),
    verify_ssl: bool | None = typer.Option(
        None,
        "--verify-ssl",
        help="Force SSL certificate verification on (overrides config profile).",
    ),
    insecure: bool = typer.Option(
        False,
        "--insecure",
        help="Alias for --no-verify-ssl.",
    ),
):
```

> **Tip:** Set `JJI_SERVER` to your local server and run `jji health` as your first smoke test. If that returns `healthy`, the local install, server process, and CLI are all wired up correctly.
