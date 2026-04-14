# Container Deployment

`jenkins-job-insight` includes a production `Dockerfile` and a ready-to-use `docker-compose.yaml`. For most deployments, the shortest path is to create a `.env` file, set your AI-provider credentials and any default Jenkins settings you want the service to use, start the service with Compose, and persist `/data` so the SQLite database survives restarts. The same container serves both the FastAPI backend and the built React web UI on port `8000`.

> **Note:** This repository provides the image build and a Compose deployment, but it does not include Kubernetes/OpenShift manifests or a container publishing pipeline. For OpenShift, use the provided `Dockerfile` as the image source and supply your own `Deployment`, `Service`, `Route`, and secret management.

## What the provided image does

The `Dockerfile` is now a three-stage build: a Node-based frontend build stage, a Python dependency builder stage, and a final runtime image. The frontend stage runs `vite build`, and the finished assets are copied into the runtime image so the API and React UI ship together.

```1:16:Dockerfile
# Frontend build stage
FROM node:20-slim AS frontend-builder

WORKDIR /frontend

# Copy package files first for layer caching
COPY frontend/package.json frontend/package-lock.json ./

# Install dependencies
RUN npm ci

# Copy frontend source
COPY frontend/ .

# Build the frontend (vite build only — type checking runs in tox/CI)
RUN npx vite build
```

```84:88:Dockerfile
# Copy source code
COPY --chown=appuser:0 --from=builder /app/src /app/src

# Copy built frontend assets from frontend builder
COPY --chown=appuser:0 --from=frontend-builder /frontend/dist /app/frontend/dist
```

The runtime image still installs the supported AI CLIs, runs as a non-root user, is prepared for OpenShift-style arbitrary UIDs, exposes port `8000`, sets `HOME=/home/appuser`, and starts the service through `entrypoint.sh`.

> **Warning:** Image builds are not fully offline. The `Dockerfile` pulls `uv` from `ghcr.io`, installs Claude and Cursor from remote install scripts, and installs Gemini from npm. Your build environment needs outbound access to those endpoints, or mirrored equivalents.

## Deploy With Docker Compose

The provided `docker-compose.yaml` still builds from the local `Dockerfile`, publishes port `8000`, mounts `./data` into `/data`, loads variables from `.env`, and configures a health check. It now also makes it explicit that port `8000` serves both the React web UI and the REST API, and it includes commented development-only examples for Vite hot reload on port `5173`.

```22:65:docker-compose.yaml
services:
  jenkins-job-insight:
    # Build from local Dockerfile
    build:
      context: .
      dockerfile: Dockerfile

    # Container name for easier management
    container_name: jenkins-job-insight

    # Ports: Web UI + API served on the same port
    ports:
      - "8000:8000"   # Web UI (React) + REST API
      # Dev mode: Vite HMR for frontend hot-reload (uncomment with DEV_MODE=true)
      # - "5173:5173"

    # Persist SQLite database across container restarts
    # The ./data directory on host maps to /data in container
    volumes:
      - ./data:/data
      # Optional: Mount gcloud credentials for Vertex AI authentication
      # Uncomment if using CLAUDE_CODE_USE_VERTEX=1 with Application Default Credentials
      # - ~/.config/gcloud:/home/appuser/.config/gcloud:ro
      # Dev mode: mount source for hot-reload (uncomment with DEV_MODE=true)
      # - ./src:/app/src
      # - ./frontend:/app/frontend

    # Restart policy: container restarts unless explicitly stopped
    restart: unless-stopped

    # Health check configuration
    # Verifies the service is responding on /health endpoint
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s

    # Environment variables
    # Option 1: Use .env file (recommended)
    env_file:
      - .env
```

```67:82:docker-compose.yaml
# Option 2: Define variables directly (shown as examples/defaults)
# Uncomment and configure if not using .env file
environment:
  # ===================
  # Jenkins Configuration (Required)
  # ===================
  - JENKINS_URL=${JENKINS_URL:-https://jenkins.example.com}
  - JENKINS_USER=${JENKINS_USER:-your-username}
  - JENKINS_PASSWORD=${JENKINS_PASSWORD:-your-api-token}

  # ===================
  # AI CLI Configuration
  # ===================
  # AI_PROVIDER: claude, gemini, or cursor
  - AI_PROVIDER=${AI_PROVIDER:?AI_PROVIDER is required}
  - AI_MODEL=${AI_MODEL:?AI_MODEL is required}
```

A typical Compose workflow looks like this:

1. Copy `.env.example` to `.env`.
2. Replace the example AI settings with real values. If you want server-level Jenkins defaults, replace the placeholder Jenkins values too; otherwise remove those placeholder lines from the Compose environment section and provide Jenkins settings per request instead.
3. Start the stack with `docker compose up -d`.
4. Check the service on `/health` and then use the web UI or API on port `8000`.

A few practical details matter here:

- The Compose file fail-fast checks `AI_PROVIDER` and `AI_MODEL`, so Compose will not start until those are set.
- The commented `DEV_MODE` examples are for local development only. Production deployments should keep the prebuilt frontend and leave those lines commented out.
- If you do not want to use Compose, mirror the same environment variables, `/data` mount, port exposure, and health probe behavior in your own deployment manifests.

> **Warning:** The container does not run as root. If your host-side `./data` directory or mounted volume is not writable by the container, database initialization will fail. Fix the volume permissions on the host or through your platform security settings.

## Persistent `/data` Storage

The application stores its SQLite database in `/data/results.db` by default.

```21:21:src/jenkins_job_insight/storage.py
DB_PATH = Path(os.getenv("DB_PATH", "/data/results.db"))
```

That makes `/data` the stateful part of the container. It holds:

- Analysis results
- Comments and review state
- Failure history and classifications
- Stored data used to resume waiting Jenkins jobs after a restart

> **Note:** A restart does not resume every in-progress job. JJI resumes only jobs that were in the `waiting` state and still have usable stored request data in SQLite. Orphaned `pending` and `running` jobs are marked `failed` instead of resuming mid-analysis.

The app initializes the database automatically on startup, creates the parent directory if needed, and runs SQLite schema migrations as part of startup.

```85:92:src/jenkins_job_insight/storage.py
async def init_db() -> None:
    """Initialize the database schema.

    Creates the results table if it does not exist.
    """
    logger.info(f"Initializing database at {DB_PATH}")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
```

Sensitive request fields that need to be stored for later job resumption are encrypted before they are written. The service uses `JJI_ENCRYPTION_KEY` when it is set; otherwise it creates a local key file under `$XDG_DATA_HOME/jji/.encryption_key` or `~/.local/share/jji/.encryption_key`.

```101:106:src/jenkins_job_insight/encryption.py
key_dir = (
    Path(os.environ.get("XDG_DATA_HOME", ""))
    if os.environ.get("XDG_DATA_HOME")
    else Path.home() / ".local" / "share"
) / "jji"
key_file = key_dir / ".encryption_key"
```

```133:136:src/jenkins_job_insight/encryption.py
secret = os.environ.get("JJI_ENCRYPTION_KEY", "")
if not secret:
    secret = _get_or_create_key_file()
return Fernet(_derive_fernet_key(secret))
```

> **Warning:** Deleting `/data` resets the service state. You will lose stored analysis history, comments, classifications, review state, and saved results.

> **Tip:** You can move the SQLite file by setting `DB_PATH`. If you do that, persist the parent directory of that path.

> **Warning:** For production containers, set `JJI_ENCRYPTION_KEY` as a secret or persist the XDG data directory used for the auto-generated key. If the database survives but the encryption key does not, stored sensitive request parameters cannot be decrypted after restart.

> **Warning:** The provided deployment model is best suited to a single app replica. The shipped service uses a single SQLite database file, so treat this container deployment as a stateful single-instance service unless you replace the storage approach.

## Configure Provider Credentials

For the container itself, the always-required settings are `AI_PROVIDER`, `AI_MODEL`, and the credentials needed by that AI provider. Jenkins settings are optional server defaults: set them when you want the service to fetch Jenkins builds directly with `POST /analyze`, or leave them unset and provide Jenkins details per request instead.

```6:9:.env.example
JENKINS_URL=https://jenkins.example.com
JENKINS_USER=your-username
JENKINS_PASSWORD=your-api-token
JENKINS_SSL_VERIFY=true
```

```11:44:.env.example
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

Optional peer-analysis and tracker settings are now part of the sample environment too. If you use the default `env_file: .env` workflow, add these keys to `.env` even though the commented inline `environment:` example only shows a subset of variables:

```52:57:.env.example
# ===================
# Peer Analysis (Optional)
# ===================
# Enable multi-AI consensus by configuring peer AI providers
# PEER_AI_CONFIGS=cursor:gpt-5.4-xhigh,gemini:gemini-2.5-pro
# PEER_ANALYSIS_MAX_ROUNDS=3
```

Each peer entry uses `provider:model` format. The primary analysis still uses `AI_PROVIDER` and `AI_MODEL`; `PEER_AI_CONFIGS` adds secondary providers and models that review the main analysis.

```102:110:.env.example
# ===================
# GitHub Integration (Optional)
# ===================
# GitHub API token for private repo PR status in comments
# GITHUB_TOKEN=your-github-token

# Explicitly enable/disable GitHub issue creation (overrides auto-detection)
# When not set, auto-detected from TESTS_REPO_URL and GITHUB_TOKEN
# ENABLE_GITHUB_ISSUES=true

# Report Portal integration
# REPORTPORTAL_URL=https://reportportal.example.com
# REPORTPORTAL_API_TOKEN=your-rp-api-token
# REPORTPORTAL_PROJECT=your-project
# ENABLE_REPORTPORTAL=true
# REPORTPORTAL_VERIFY_SSL=false  # for self-signed certificates
```

For day-to-day container deployments, these are the most important choices:

- `AI_PROVIDER` must be one of `claude`, `gemini`, or `cursor`.
- `AI_MODEL` is always required, regardless of provider.
- `JENKINS_URL`, `JENKINS_USER`, and `JENKINS_PASSWORD` are optional defaults for Jenkins-backed `/analyze` requests.
- `JENKINS_SSL_VERIFY=false` is useful if your Jenkins uses a self-signed certificate.
- `AI_CLI_TIMEOUT` and `LOG_LEVEL` are useful operational settings once the service is running.
- If you want multi-AI consensus, set `PEER_AI_CONFIGS` to a comma-separated list of additional `provider:model` pairs. Use `PEER_ANALYSIS_MAX_ROUNDS` to limit how many peer debate rounds the service will run. Every provider you list there must also have working credentials in the container.
- If you want GitHub issue creation from the report UI, set `TESTS_REPO_URL` and `GITHUB_TOKEN`. Use `ENABLE_GITHUB_ISSUES` only when you want to force the feature on or off instead of relying on auto-detection.
- If you want Jira bug search or Jira bug creation, set `JIRA_URL` and `JIRA_PROJECT_KEY`, then use either `JIRA_EMAIL` + `JIRA_API_TOKEN` for Jira Cloud or `JIRA_PAT` for Jira Server/Data Center.
- If you want to push classifications to Report Portal, set `REPORTPORTAL_URL`, `REPORTPORTAL_API_TOKEN`, and `REPORTPORTAL_PROJECT`. The token must belong to the launch owner (typically a CI service account) or a Project Manager. Use `REPORTPORTAL_VERIFY_SSL=false` for instances with self-signed certificates.

Provider-specific notes:

- `claude`: The simplest container-friendly option is `ANTHROPIC_API_KEY`. If you use Vertex AI instead, set `CLAUDE_CODE_USE_VERTEX=1`, `CLOUD_ML_REGION`, and `ANTHROPIC_VERTEX_PROJECT_ID`. The Compose file already includes a commented example for mounting Google credentials into `/home/appuser/.config/gcloud`.
- `gemini`: `GEMINI_API_KEY` is the easiest option in containers. OAuth is also supported, but it stores login state in the container user's home/config directory.
- `cursor`: If you use API-key auth, set `CURSOR_API_KEY`. If you need file-based credentials in OpenShift, the entrypoint can stage them from a mounted `/cursor-credentials` directory.

> **Tip:** API-key auth is still the simplest fit for containers because the provided Compose file only persists `/data`. If you use OAuth or file-based CLI auth, mount a second volume or secret-backed path for the provider config under `/home/appuser/.config`, or set `XDG_CONFIG_HOME` to a writable persistent location.

> **Warning:** The provided `docker-compose.yaml` does not persist `/home/appuser`. Any provider credentials stored there by interactive login flows are ephemeral unless you mount that path explicitly.

## OpenShift-Specific Runtime Considerations

The image is explicitly prepared for OpenShift's arbitrary-UID model. It creates `/data`, makes key directories group-writable, sets `HOME`, and runs the app without requiring a fixed runtime UID. Because the final image already contains the built frontend assets, an OpenShift deployment only needs one app container and one HTTP port.

```51:123:Dockerfile
# Create non-root user, data directory, and set permissions
# OpenShift runs containers as a random UID in the root group (GID 0)
RUN useradd --create-home --shell /bin/bash -g 0 appuser \
    && mkdir -p /data \
    && chown appuser:0 /data \
    && chmod -R g+w /data

# ... omitted ...

# Make /app group-writable for OpenShift compatibility
RUN chmod -R g+w /app

# Make appuser home accessible by OpenShift arbitrary UID
# Only chmod directories (not files) — files are already group-readable by default.
# Directories need group write+execute for OpenShift's arbitrary UID (in GID 0)
# to create config/cache files at runtime.
RUN find /home/appuser -type d -exec chmod g=u {} + \
    && npm cache clean --force 2>/dev/null; \
    rm -rf /home/appuser/.npm/_cacache

# Switch back to non-root user for runtime
USER appuser

# Ensure CLIs are in PATH
ENV PATH="/home/appuser/.local/bin:/home/appuser/.npm-global/bin:${PATH}"
# Set HOME for OpenShift compatibility (random UID has no passwd entry)
ENV HOME="/home/appuser"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Use uv run for uvicorn
# --no-sync prevents uv from attempting to modify the venv at runtime.
# This is required for OpenShift where containers run as an arbitrary UID
# and may not have write access to the .venv directory.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uv", "run", "--no-sync", "uvicorn", "jenkins_job_insight.main:app", "--host", "0.0.0.0"]
```

In practice, that means:

- Mount a writable PVC at `/data`.
- Do not assume the container will run with a fixed numeric UID.
- If you add secret or config mounts under the home directory, make sure they are compatible with arbitrary-UID access.
- Keep the service as a single replica when using the built-in SQLite storage.

The entrypoint still stages Cursor credentials from `/cursor-credentials` and normalizes `PORT`, but it now also appends `--port $PORT` to the `uvicorn` command when needed. In `DEV_MODE`, it starts the Vite dev server and adds `--reload --reload-dir /app/src` for backend hot reload. Leave `DEV_MODE` unset in OpenShift production.

```8:53:entrypoint.sh
# Copy cursor credentials from PVC staging mount
if [ -d /cursor-credentials ]; then
    mkdir -p "${XDG_CONFIG_HOME:-/home/appuser/.config}/cursor"
    cp -a /cursor-credentials/. "${XDG_CONFIG_HOME:-/home/appuser/.config}/cursor/"
fi

# Resolve PORT with a default so the exec-form CMD (which cannot expand
# shell variables) gets the correct bind port at runtime.
export PORT="${PORT:-8000}"

# Dev mode: start Vite dev server in background for frontend HMR
if [ "${DEV_MODE:-}" = "true" ] && [ -f /app/frontend/package.json ]; then
    echo "[DEV] Frontend source detected, starting Vite dev server..."
    cd /app/frontend || { echo "[DEV] Failed to change to frontend directory"; exit 1; }
    if [ ! -d node_modules ]; then
        echo "[DEV] Installing frontend dependencies..."
        npm install --no-audit --no-fund
    fi
    npm run dev -- --host 0.0.0.0 --port 5173 &
    cd /app || { echo "[DEV] Failed to return to app directory"; exit 1; }
fi

# Check if any argument contains "uvicorn" to detect all uvicorn invocations
has_uvicorn=false
has_port=false
for arg in "$@"; do
    case "$arg" in
        *uvicorn*) has_uvicorn=true ;;
        --port|--port=*) has_port=true ;;
    esac
done

# Build final arguments
extra_args=""
if [ "$has_uvicorn" = true ] && [ "$has_port" = false ]; then
    extra_args="$extra_args --port $PORT"
fi
if [ "$has_uvicorn" = true ] && [ "${DEV_MODE:-}" = "true" ]; then
    extra_args="$extra_args --reload --reload-dir /app/src"
fi

if [ -n "$extra_args" ]; then
    exec "$@" $extra_args
else
    exec "$@"
fi
```

If a projected volume or `subPath` mount makes `~/.config` non-writable, set `XDG_CONFIG_HOME` to a writable location and mount provider configuration there instead.

This matters on OpenShift because platform runtimes often inject or expect a `PORT` value. The image honors that automatically, but your surrounding deployment still needs to match it.

- If you set a non-default `PORT`, update your `Service`, `Route`, and probes to match.
- If you use the provided Compose file and change `PORT`, also update its `ports` mapping and its health check, because the Compose health check is hard-coded to `8000`.
- Point readiness and liveness probes at `/health`.

Public result links no longer trust `Host` or `X-Forwarded-*` headers. If you want absolute URLs behind an OpenShift `Route` or another reverse proxy, set `PUBLIC_BASE_URL` explicitly.

```146:164:src/jenkins_job_insight/main.py
def _extract_base_url() -> str:
    """Extract the external base URL for building public-facing links.

    When ``PUBLIC_BASE_URL`` is set, it is used directly as the trusted
    origin.  Otherwise the function returns an empty string so that
    callers produce relative URLs, avoiding host-header injection.

    Returns:
        Base URL without trailing slash (e.g. "https://example.com"),
        or an empty string when no trusted origin is configured.
    """
    settings = get_settings()
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")

    logger.debug(
        "PUBLIC_BASE_URL is not set; returning empty base URL (relative paths)"
    )
    return ""
```

> **Tip:** On OpenShift, set `PUBLIC_BASE_URL` to your public route URL, for example `https://jji.apps.example.com`, if you want API responses and issue-preview links to contain absolute external URLs.

> **Warning:** The image-level health check respects `PORT`, but the provided `docker-compose.yaml` health check does not. If you override the container port, update both the Compose port mapping and the Compose health check.


## Related Pages

- [Installation](installation.html)
- [Configuration Reference](configuration-reference.html)
- [Reverse Proxy and Base URL Handling](reverse-proxy-and-base-urls.html)
- [AI Provider Setup](ai-provider-setup.html)
- [Storage and Result Lifecycle](storage-and-result-lifecycle.html)