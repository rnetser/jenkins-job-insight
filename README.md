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

## CLI

```bash
uv tool install jenkins-job-insight
export JJI_SERVER=http://localhost:8000

jji health
jji analyze --job-name my-job --build-number 42
jji results list
```

Run `jji --help` for all commands.

## Development

```bash
git clone https://github.com/myk-org/jenkins-job-insight.git
cd jenkins-job-insight
uvx --with tox-uv tox
```

See the [development guide](https://myk-org.github.io/jenkins-job-insight/development-and-testing.html) for full setup.

## License

MIT
