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

> **Note:** Provider authentication is handled by the selected CLI, not by the FastAPI app itself. In `config.py`, the Claude Vertex variables are explicitly documented as being read by the `claude` CLI, not by the application. If you enable `PEER_AI_CONFIGS`, every provider listed there must also be installed and authenticated in the same runtime.

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

Server-wide defaults still come from `AI_PROVIDER` and `AI_MODEL`, but provider setup now has two layers:

- `AI_PROVIDER` and `AI_MODEL` choose the primary AI that produces the final analysis.
- `PEER_AI_CONFIGS` optionally adds peer reviewers, and `PEER_ANALYSIS_MAX_ROUNDS` limits how long the peer debate can continue.

In `.env`, peer configs use a simple `provider:model,provider:model` format:

```dotenv
AI_PROVIDER=claude
AI_MODEL=your-model-name
# PEER_AI_CONFIGS=cursor:gpt-5.4-xhigh,gemini:gemini-2.5-pro
# PEER_ANALYSIS_MAX_ROUNDS=3
```

Each peer entry uses the same provider names as the primary AI: `claude`, `gemini`, or `cursor`.

On the `jji` CLI, the primary provider flags stay the same and peer analysis is opt-in per run:

```bash
jji analyze --job-name mtv-2.11-ocp-4.20-test-release-non-gate --build-number 27 --provider claude --model opus-4 --peers "cursor:gpt-5.4-xhigh,gemini:gemini-2.5-pro" --peer-analysis-max-rounds 5 --jira
```

The CLI config file supports the same pattern. `config.example.toml` shows peer defaults under `[defaults]`, while individual server profiles can still override the primary provider and model:

```toml
[defaults]
# Peer analysis (multi-AI consensus)
# peers = "cursor:gpt-5.4-xhigh,gemini:gemini-2.5-pro"
# peer_analysis_max_rounds = 3

[servers.prod]
# Inherits all defaults, overrides ai_provider:
ai_provider = "cursor"
ai_model = "gpt-5.4-xhigh"
```

For the API, the request model still accepts `ai_provider` and `ai_model`, and it now also accepts `peer_ai_configs` and `peer_analysis_max_rounds`:

```json
{
  "job_name": "test",
  "build_number": 123,
  "tests_repo_url": "https://github.com/example/repo",
  "ai_provider": "claude",
  "ai_model": "test-model",
  "peer_ai_configs": [
    {"ai_provider": "cursor", "ai_model": "gpt-5.4-xhigh"},
    {"ai_provider": "gemini", "ai_model": "gemini-2.5-pro"}
  ],
  "peer_analysis_max_rounds": 5
}
```

Both the API and CLI validate `peer_analysis_max_rounds` in the `1`-to-`10` range.

Omit `peer_ai_configs` when you want the server default from `PEER_AI_CONFIGS`. Send `peer_ai_configs: []` when you want to disable peer analysis for one request while keeping the server default in place.

## Provider-agnostic execution model

The service still does not maintain separate Python integrations for Claude, Gemini, and Cursor. It uses `ai-cli-runner` for all of them and feeds the chosen provider/model through the same `_call_ai_cli_with_retry()` helper. There are no provider-specific Python SDKs in `pyproject.toml`; the shared dependency is still `ai-cli-runner>=0.1.1`.

The primary analysis path in `analyzer.py` is still provider-agnostic. The only provider-specific behavior is the extra CLI flags:

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

Optional peer analysis reuses that same abstraction. When `peer_ai_configs` is present, `analyze_failure_group()` switches from the single-AI path to the peer-consensus path instead of introducing a second provider-specific pipeline:

```python
if peer_ai_configs:
    return await analyze_failure_group_with_peers(
        failures=failures,
        console_context=console_context,
        repo_path=repo_path,
        main_ai_provider=ai_provider,
        main_ai_model=ai_model,
        peer_ai_configs=configs,
        max_rounds=peer_analysis_max_rounds,
        ai_cli_timeout=ai_cli_timeout,
        custom_prompt=custom_prompt,
        artifacts_context=artifacts_context,
        server_url=server_url,
        job_id=job_id,
        group_label=group_label,
    )
```

Inside `peer_analysis.py`, peers are just additional `{ai_provider, ai_model}` entries reviewed in parallel:

```python
peer_tasks = [_call_peer(cfg) for cfg in peer_ai_configs]
peer_results = await run_parallel_with_limit(peer_tasks)
```

The main AI analyzes first, peers review in parallel, and the loop stops when they reach consensus or `peer_analysis_max_rounds` is exhausted. In practice, switching the main provider is still mostly a configuration change, not a code change: the HTTP endpoints stay the same, the request body keeps the same primary `ai_provider` and `ai_model` fields, and peer analysis adds only optional `peer_ai_configs` input and `peer_debate` metadata to each failure result.

> **Note:** `_call_ai_cli_with_retry()` is still a thin wrapper around `call_ai_cli()`. Structural flags such as Claude's `-p` and Cursor's `--print` are handled inside `ai-cli-runner`; the service only supplies the extra per-provider flags shown above.

## Verify your setup

Completed analyses store the primary `ai_provider` and `ai_model`. The service also exposes the distinct primary provider/model pairs it has already used successfully through `GET /ai-configs`, and the CLI wraps that as `jji ai-configs`.

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

This is the quickest way to confirm which primary provider/model combinations have already completed successfully in your environment.

> **Note:** `GET /ai-configs` reads the top-level `ai_provider` and `ai_model` stored on completed results. If you enable peer analysis, the peer provider/model pairs are stored inside each failure's optional `peer_debate` trail rather than being listed separately by this endpoint.

## Common setup issues

- Only `claude`, `gemini`, and `cursor` are accepted provider names.
- Setting credentials alone is not enough; you must also set `AI_PROVIDER` and `AI_MODEL`.
- If you switch providers, update the matching credentials too.
- If Claude Vertex works on your machine but not in a container, check the `gcloud` credentials mount.
- If a model is slow, increase `AI_CLI_TIMEOUT`. The default in `config.py` and `.env.example` is `10` minutes.

> **Tip:** If you are experimenting, start by getting one provider working through the supplied container image first. Once that works, moving to another provider is usually just a matter of changing `AI_PROVIDER`, `AI_MODEL`, and the provider-specific auth settings.
