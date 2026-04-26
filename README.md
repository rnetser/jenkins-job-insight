# Jenkins Job Insight

AI-powered Jenkins failure analysis -- classifies test failures as code issues or product bugs.

**[Documentation](https://myk-org.github.io/jenkins-job-insight/)** -- configuration, API reference, integrations, and more.

## Prerequisites

An AI provider CLI must be installed and authenticated: [Claude](https://docs.anthropic.com/en/docs/claude-code), [Gemini](https://github.com/google-gemini/gemini-cli), or [Cursor](https://docs.cursor.com/agent). See [docs](https://myk-org.github.io/jenkins-job-insight/ai-provider-setup.html) for setup details.

## Quick Start

```bash
mkdir -p data
docker run -d -p 8000:8000 -v ./data:/data \
  -e JENKINS_URL=https://jenkins.example.com \
  -e JENKINS_USER=your-username \
  -e JENKINS_PASSWORD=your-api-token \
  -e AI_PROVIDER=claude \
  -e AI_MODEL=your-model-name \
  ghcr.io/myk-org/jenkins-job-insight:latest
```

## Features

- **AI-Powered Failure Analysis** — Classifies test failures as code issues or product bugs
- **AI Token Usage Tracking** — Track token consumption, costs, and duration for all AI CLI calls. Admin dashboard shows usage by provider/model/time period with CSV export.

## CLI

```bash
uv tool install jenkins-job-insight
export JJI_SERVER=http://localhost:8000

jji health
jji analyze --job-name my-job --build-number 42
jji results list
jji admin token-usage              # Summary dashboard
jji admin token-usage --group-by model  # Grouped breakdown
jji admin token-usage --job-id <uuid>   # Per-job usage
jji admin token-usage --period month --format csv  # CSV export
```

Run `jji --help` for all commands.

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/admin/token-usage` | Aggregated token usage with filters and grouping (admin only) |
| `GET /api/admin/token-usage/summary` | Dashboard summary: today/week/month stats (admin only) |
| `GET /api/admin/token-usage/{job_id}` | Per-job token usage breakdown (admin only) |

See the [API reference](https://myk-org.github.io/jenkins-job-insight/) for all endpoints.

## Web Push Notifications

Users can receive browser push notifications when @mentioned in comments. The server uses [VAPID](https://datatracker.ietf.org/doc/html/rfc8292) for Web Push authentication.

| Variable | Description |
|----------|-------------|
| `VAPID_PUBLIC_KEY` | VAPID public key (auto-generated with private key if not set) |
| `VAPID_PRIVATE_KEY` | VAPID private key |
| `VAPID_CLAIM_EMAIL` | Contact email included in VAPID claims |

Subscribe/unsubscribe is browser-only (managed via the web UI). To list users available for @mentions:

```bash
jji mentionable-users
```

## Development

```bash
git clone https://github.com/myk-org/jenkins-job-insight.git
cd jenkins-job-insight
uvx --with tox-uv tox
```

See the [development guide](https://myk-org.github.io/jenkins-job-insight/development-and-testing.html) for full setup.

## License

MIT
