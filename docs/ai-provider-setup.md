# AI Provider Setup

`jenkins-job-insight` works with `Claude`, `Gemini`, and `Cursor` through their CLIs. You choose a provider with `AI_PROVIDER`, choose a model with `AI_MODEL`, authenticate the matching CLI, and the service keeps the same API and result format no matter which provider you use.

> **Warning:** `AI_PROVIDER` and `AI_MODEL` are both required. The service returns `400` if either one is missing.

## Quick start

1. Copy `.env.example` to `.env`.
2. Set `AI_PROVIDER` to `claude`, `gemini`, or `cursor`.
3. Set `AI_MODEL` to a model name supported by that provider's CLI.
4. Add the authentication expected by the selected CLI.
5. Start the service.

The repository already gives you the expected environment layout in `.env.example`:

```dotenv
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

> **Note:** Provider authentication is handled by the selected CLI, not by the FastAPI app itself. In `config.py`, the Claude Vertex variables are explicitly documented as being read by the `claude` CLI, not by the application.

> **Tip:** Both `config.py` and `docker-compose.yaml` are wired to read `.env`, so one file is enough for most deployments.

## Using the provided image

If you use the repository's `Dockerfile`, all three CLIs are already installed for you. In that setup, switching providers is usually just a matter of changing `AI_PROVIDER`, `AI_MODEL`, and the matching credentials.

```dockerfile
# Install Claude Code CLI (installs to ~/.local/bin)
RUN /bin/bash -o pipefail -c "curl -fsSL https://claude.ai/install.sh | bash"

# Install Cursor Agent CLI (installs to ~/.local/bin)
RUN /bin/bash -o pipefail -c "curl -fsSL https://cursor.com/install | bash"

# Configure npm for non-root global installs and install Gemini CLI
RUN mkdir -p /home/appuser/.npm-global \
    && npm config set prefix '/home/appuser/.npm-global' \
    && npm install -g @google/gemini-cli
```

If you are not using the provided image, mirror that setup in your own runtime so the chosen CLI is installed and available on `PATH`.

## Authentication by provider

### Claude

The repo supports two Claude authentication paths:

- Set `ANTHROPIC_API_KEY` for the simplest setup.
- Or use Vertex AI with `CLAUDE_CODE_USE_VERTEX=1`, `CLOUD_ML_REGION`, and `ANTHROPIC_VERTEX_PROJECT_ID`.

If you run the service in a container and want Claude Vertex to use Application Default Credentials, `docker-compose.yaml` already includes the mount you need:

```yaml
volumes:
  - ./data:/data
  # Optional: Mount gcloud credentials for Vertex AI authentication
  # Uncomment if using CLAUDE_CODE_USE_VERTEX=1 with Application Default Credentials
  # - ~/.config/gcloud:/home/appuser/.config/gcloud:ro
```

> **Note:** The Vertex-related environment variables are CLI settings for Claude. They are not request-body fields on the `jenkins-job-insight` API.

### Gemini

The repo supports two Gemini authentication patterns:

- Set `GEMINI_API_KEY`.
- Or run `gemini auth login`. The `.env.example` file notes that no extra environment variables are needed for OAuth.

If you use the provided image, the `Gemini CLI` is already installed globally via `npm`.

### Cursor

The documented `.env`-based setup is `CURSOR_API_KEY`.

If you prefer interactive web login inside a running container, the repo also documents this flow:

```bash
docker exec <container-name> agent login
```

For container or OpenShift-style deployments, `entrypoint.sh` also supports pre-staged Cursor credentials. If `/cursor-credentials` exists, the entrypoint copies those files into the runtime config directory before the app starts:

```bash
if [ -d /cursor-credentials ]; then
    mkdir -p "${XDG_CONFIG_HOME:-/home/appuser/.config}/cursor"
    cp -a /cursor-credentials/. "${XDG_CONFIG_HOME:-/home/appuser/.config}/cursor/"
fi
```

That is useful when you already have working Cursor CLI credentials on a mounted volume.

> **Tip:** In `docker-compose.yaml`, the `CURSOR_API_KEY` line is present but commented out. Uncomment it when you switch `AI_PROVIDER` to `cursor`.

## Default provider vs per-request override

Server-wide defaults come from `AI_PROVIDER` and `AI_MODEL`, but the API also lets you override both values per request. The shared request model exposes `ai_provider` and `ai_model`, and the `jji` CLI maps them to `--provider` and `--model`.

For the CLI, the current command shape is:

```bash
jji analyze --job-name mtv-2.11-ocp-4.20-test-release-non-gate --build-number 27 --provider claude --jira
```

Use `--model` the same way when you want to override the model for a single run.

The CLI can also keep provider/model defaults in `~/.config/jji/config.toml`. The bundled `config.example.toml` shows the same keys under `[defaults]` and under individual server profiles:

```toml
[servers.prod]
# Inherits all defaults, overrides ai_provider:
ai_provider = "cursor"
ai_model = "gpt-5.4-xhigh"
```

For the API, tests in the repo use request bodies like this:

```json
{
  "job_name": "test",
  "build_number": 123,
  "tests_repo_url": "https://github.com/example/repo",
  "ai_provider": "claude",
  "ai_model": "test-model"
}
```

That means you can keep a stable deployment default in `.env`, let `jji` remember its own defaults per server, and still override provider or model only for the runs where you want to try something different.

## Provider-agnostic execution model

The service does not maintain three separate analysis pipelines. Instead, it depends on `ai-cli-runner` and passes the chosen provider and model through one shared execution path. In the main analysis path, that call now goes through a small retry helper for known transient CLI failures. There are no provider-specific Python SDKs in `pyproject.toml`; the common dependency is `ai-cli-runner>=0.1.1`.

The current execution flow in `analyzer.py` looks like this:

```python
PROVIDER_CLI_FLAGS: dict[str, list[str]] = {
    "claude": ["--dangerously-skip-permissions"],
    "gemini": ["--yolo"],
    "cursor": ["--force"],
}

success, analysis_output = await _call_ai_cli_with_retry(
    prompt,
    cwd=repo_path,
    ai_provider=ai_provider,
    ai_model=ai_model,
    ai_cli_timeout=ai_cli_timeout,
    cli_flags=PROVIDER_CLI_FLAGS.get(ai_provider, []),
)
```

This is the important design choice:

- The service resolves `ai_provider` and `ai_model` once.
- It sends both through the same `call_ai_cli()` abstraction.
- Only the provider-specific extra flags change.

The same pattern is reused beyond main failure analysis. The repo also uses the shared runner when filtering Jira matches and when generating GitHub or Jira issue content from analysis results.

In practice, this means switching providers is mostly a configuration change, not a code change:

- The HTTP endpoints stay the same.
- The request shape stays the same.
- The stored result schema stays the same.
- Only the selected CLI and model string change.

> **Note:** `_call_ai_cli_with_retry()` is a thin wrapper around `call_ai_cli()`. A code comment in `analyzer.py` explains that structural flags such as Claude's `-p` and Cursor's `--print` are handled inside `ai-cli-runner`; the service only supplies the extra per-provider flags shown above.

## Verify your setup

Completed analyses store both `ai_provider` and `ai_model`. The service also exposes the distinct provider/model pairs it has already used successfully through `GET /ai-configs`, and the CLI wraps that as `jji ai-configs`.

```bash
jji ai-configs --json
```

The test suite shows the expected response shape:

```json
[
  {"ai_provider": "claude", "ai_model": "opus-4"},
  {"ai_provider": "gemini", "ai_model": "2.5-pro"}
]
```

This is a simple way to confirm which provider/model combinations have already completed successfully in your environment.

## Common setup issues

- Only `claude`, `gemini`, and `cursor` are accepted provider names.
- Setting credentials alone is not enough; you must also set `AI_PROVIDER` and `AI_MODEL`.
- If you switch providers, update the matching credentials too.
- If Claude Vertex works on your machine but not in a container, check the `gcloud` credentials mount.
- If a model is slow, increase `AI_CLI_TIMEOUT`. The default in `config.py` and `.env.example` is `10` minutes.

> **Tip:** If you are experimenting, start by getting one provider working through the supplied container image first. Once that works, moving to another provider is usually just a matter of changing `AI_PROVIDER`, `AI_MODEL`, and the provider-specific auth settings.
