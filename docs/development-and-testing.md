# Development and Testing

`jenkins-job-insight` is maintained as two codebases in one repository: a Python backend/CLI under `src/jenkins_job_insight/` and a React + TypeScript frontend under `frontend/`. For maintainers, the checked-in configuration is the source of truth: `pyproject.toml`, `tox.toml`, `.pre-commit-config.yaml`, and the frontend toolchain files in `frontend/`.

> **Note:** At the time of writing, the repository does not contain a checked-in GitHub Actions workflow, GitLab CI file, or `Jenkinsfile`. The repeatable validation flow is defined locally through `tox`, `pre-commit`, and the frontend scripts.

## Quick Start

For a fresh checkout, install the Python test extras and the frontend dependencies first:

```bash
uv sync --extra tests

cd frontend
npm ci
cd ..

uvx --with tox-uv tox
pre-commit run --all-files

cd frontend
npm run lint
npm run typecheck
```

If you already have `tox` installed, plain `tox` uses the same `tox.toml`. The `uvx --with tox-uv tox` form is just the repository’s standard way to run it without needing a separate global install.

## The Pytest Suite

The backend requires Python 3.12, and its `pytest` settings live in `pyproject.toml`. The project installs a small `tests` extra, looks for tests in `tests/`, adds `src/` to the import path, and enables `pytest-asyncio` in automatic mode.

```26:36:pyproject.toml
[project.optional-dependencies]
tests = ["pytest", "pytest-asyncio"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

That `pythonpath` setting is why direct `pytest` runs can import `jenkins_job_insight` without an extra editable-install step. The backend command used by `tox` is:

```bash
uv run --extra tests pytest tests/ -q
```

The `pytest` suite is broad. It is not just a handful of unit tests:

- `tests/test_main.py` exercises the FastAPI app, including health checks, analysis endpoints, persisted `request_params`, `PUBLIC_BASE_URL` handling, `peer_ai_configs` / `peer_analysis_max_rounds` request overrides, history, comments, review state, issue preview/creation, waiting logic, OpenAPI output, and SPA routes.
- `tests/test_cli_main.py`, `tests/test_cli_client.py`, `tests/test_cli_config.py`, and `tests/test_cli_output.py` cover the `jji` CLI end to end: command wiring, HTTP transport, config resolution, and output formatting.
- `tests/test_analyzer.py` and `tests/test_peer_analysis.py` cover AI CLI orchestration, JSON parsing and retry behavior, plus the multi-AI debate loop and consensus rules.
- `tests/test_storage.py`, `tests/test_history.py`, and `tests/test_comments.py` cover SQLite storage, historical aggregation, comments, and review state.
- `tests/test_models.py`, `tests/test_config.py`, and `tests/test_encryption.py` cover validation, settings, peer-analysis config parsing and `peer_analysis_max_rounds` bounds, plus redaction/encryption behavior.
- `tests/test_jira.py`, `tests/test_jenkins.py`, `tests/test_jenkins_artifacts.py`, `tests/test_bug_creation.py`, `tests/test_repository.py`, and `tests/test_xml_enrichment.py` cover integrations, repository cloning and URL validation, SSL retry behavior, and supporting utilities.

Most backend tests avoid live network calls. Instead, they patch external boundaries such as Jenkins clients, HTTP transport, AI CLI calls, Git clone operations, and temporary SQLite databases. That keeps the suite fast and predictable.

The peer-analysis tests use the same approach. They build `PeerRound` values directly and assert consensus behavior in-process, which keeps the debate-loop coverage fast and deterministic.

```102:127:tests/test_peer_analysis.py
    def test_check_consensus_disagreement(self) -> None:
        """At least one peer disagrees -> False."""
        from jenkins_job_insight.peer_analysis import _check_consensus
        from jenkins_job_insight.models import PeerRound

        rounds = [
            PeerRound(
                round=1,
                ai_provider="gemini",
                ai_model="gemini-2.5-pro",
                role="peer",
                classification="CODE ISSUE",
                details="agree",
                agrees_with_orchestrator=True,
            ),
            PeerRound(
                round=1,
                ai_provider="claude",
                ai_model="claude-sonnet-4-20250514",
                role="peer",
                classification="PRODUCT BUG",
                details="disagree",
                agrees_with_orchestrator=False,
            ),
        ]
        assert _check_consensus("CODE ISSUE", rounds) is False
```

A representative API test from `tests/test_main.py` checks the public contract instead of internal helper functions:

```124:145:tests/test_main.py
class TestAnalyzeEndpoint:
    """Tests for the /analyze endpoint."""

    def test_analyze_async_returns_queued(self, test_client) -> None:
        """Test that async analyze returns queued status."""
        with patch("jenkins_job_insight.main.process_analysis_with_id"):
            response = test_client.post(
                "/analyze",
                json={
                    "job_name": "test",
                    "build_number": 123,
                    "tests_repo_url": "https://github.com/example/repo",
                    "ai_provider": "claude",
                    "ai_model": "test-model",
                },
            )
            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "queued"
            assert data["base_url"] == ""
            assert data["result_url"].startswith("/results/")
```

## Shared Test Runner: `tox`

`tox.toml` gives the repository one top-level entry point for both Python and frontend validation:

```1:31:tox.toml
skipsdist = true
envlist = ["backend", "frontend"]

[env.backend]
description = "Run Python tests"
commands = [["uv", "run", "--extra", "tests", "pytest", "tests/", "-q"]]
allowlist_externals = ["uv"]

[env.frontend]
commands = [
  [
    "npm",
    "ci",
    "--no-audit",
    "--no-fund",
  ],
  [
    "npx",
    "vite",
    "build",
  ],
  [
    "npm",
    "test",
  ],
]
description = "Run frontend build and tests"
skip_install = true
allowlist_externals = ["npm", "npx"]
change_dir = "frontend"
```

In practice, that means:

```bash
uvx --with tox-uv tox -e backend
uvx --with tox-uv tox -e frontend
uvx --with tox-uv tox
```

- `backend` runs the Python `pytest` suite.
- `frontend` installs dependencies with `npm ci`, performs a production build, and then runs frontend tests.
- Running `tox` with no `-e` runs both and is the best single “did I break anything?” command in the repo.

> **Tip:** Use the targeted `tox` environment while iterating, but finish with the full `uvx --with tox-uv tox` and `pre-commit run --all-files` before review.

Frontend tests are separate from `pytest` and use `Vitest` with a browser-like `jsdom` environment:

```29:34:frontend/vite.config.ts
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
  },
```

A typical frontend unit test looks like this:

```9:29:frontend/src/lib/__tests__/api.test.ts
  it('returns parsed JSON on success', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const result = await api.get<{ status: string }>('/test')
    expect(result.status).toBe('ok')
  })

  it('throws ApiError on non-ok response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'not found' }), {
        status: 404,
        statusText: 'Not Found',
        headers: { 'Content-Type': 'application/json' },
      }),
    )
```

Current frontend tests live close to the code they validate, especially in `frontend/src/lib/__tests__/` and `frontend/src/pages/report/__tests__/`. For example, `frontend/src/lib/__tests__/peerDebate.test.ts` covers the helper that groups peer-analysis rounds for the report UI.

## Pre-commit, Linting, and Formatting

`pre-commit` is the umbrella check for repository hygiene. It combines generic file-safety hooks with Python linting, formatting, typing, and secret scanning.

```10:25:.pre-commit-config.yaml
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: check-added-large-files
      - id: check-docstring-first
      - id: check-executables-have-shebangs
      - id: check-merge-conflict
      - id: check-symlinks
      - id: detect-private-key
      - id: mixed-line-ending
      - id: debug-statements
      - id: trailing-whitespace
        args: [--markdown-linebreak-ext=md] # Do not process Markdown files.
        exclude: ^docs/
      - id: end-of-file-fixer
        exclude: ^docs/
      - id: check-ast
      - id: check-builtin-literals
      - id: check-toml
```

```30:70:.pre-commit-config.yaml
  - repo: https://github.com/PyCQA/flake8
    rev: 7.3.0
    hooks:
      - id: flake8
        args: [--config=.flake8]
        additional_dependencies:
          [git+https://github.com/RedHatQE/flake8-plugins.git, flake8-mutable]

  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.8
    hooks:
      - id: ruff
      - id: ruff-format

  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.30.0
    hooks:
      - id: gitleaks

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.19.1
    hooks:
      - id: mypy
        exclude: (tests/)
        additional_dependencies:
          [types-requests, types-PyYAML, types-colorama, types-aiofiles, pydantic]

  - repo: https://github.com/pre-commit/mirrors-eslint
    rev: v10.1.0
    hooks:
      - id: eslint
        files: \.js$
        exclude: (eslint\.config\.js|^docs/)
        args: [--fix]
        additional_dependencies:
          - eslint@9.38.0
```

On top of the language-specific tools above, the same file also enables housekeeping hooks such as `check-added-large-files`, `check-merge-conflict`, `detect-private-key`, `debug-statements`, `trailing-whitespace`, and `end-of-file-fixer`. The `trailing-whitespace` and `end-of-file-fixer` hooks now exclude `docs/`, so checked-in Markdown and generated HTML under `docs/` are left alone by those automatic cleanups.

For Python, linting is intentionally split:

- `ruff` is the main Python linter.
- `ruff-format` handles formatting.
- `flake8` is kept for one narrow rule family, not for full style enforcement.

That narrow Flake8 scope is explicit in `.flake8`:

```1:14:.flake8
[flake8]
select=M511

exclude =
    doc,
    .tox,
    .git,
    *.yml,
    Pipfile.*,
    docs/*,
    .cache/*

per-file-ignores =
    src/jenkins_job_insight/cli/main.py:M511
```

For the frontend, linting lives in `frontend/package.json` and `frontend/eslint.config.js`. The checked-in ESLint config targets `**/*.{ts,tsx}`, ignores `dist/`, and layers `typescript-eslint`, React Hooks, and React Refresh rules on top of the base JavaScript rules.

> **Warning:** The checked-in `eslint` pre-commit hook still only matches `.js` files, and it excludes both `eslint.config.js` and everything under `docs/`. Most frontend code in this repository lives in `.ts` and `.tsx`, so `pre-commit run --all-files` is not a complete frontend lint check by itself. Run `cd frontend && npm run lint` when you change React or TypeScript code. If you edit files under `docs/`, remember that `trailing-whitespace` and `end-of-file-fixer` also skip that directory, and any JavaScript there bypasses the checked-in ESLint hook as well. Secret scanners and the other repo-wide hooks still run.

To make the hooks automatic in your local clone, run this once:

```bash
pre-commit install
```

## Typing

Python typing is checked through the `mypy` pre-commit hook. There is no separate checked-in `mypy.ini` or `[tool.mypy]` section in `pyproject.toml`, so the hook definition in `.pre-commit-config.yaml` is the most accurate place to see how Python typing runs. Two details matter:

- `mypy` excludes `tests/`.
- The hook installs its own extra stubs and dependencies, so running it through `pre-commit` is the most faithful way to reproduce repository behavior.

The frontend has a more explicit typing story. `frontend/package.json` exposes both a dedicated type-check command and a build command that includes TypeScript compilation:

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

The app config itself is strict and enables the kinds of checks maintainers usually want during refactors:

```19:31:frontend/tsconfig.app.json
    /* Linting */
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "erasableSyntaxOnly": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["src"]
```

For TypeScript-heavy changes, the most useful local commands are:

```bash
cd frontend
npm run typecheck
npm run build
```

`npm run build` is especially helpful because it runs a real type-checked build, not just isolated compilation.

> **Warning:** `tox -e frontend` does not currently run `npm run typecheck`. It runs `npm ci`, `npx vite build`, and `npm test`. If you touched TypeScript, run `cd frontend && npm run typecheck` yourself, or use `cd frontend && npm run build` for a type-checked build.

## Secret Scanning

This repository uses several overlapping secret defenses:

- `detect-private-key` catches checked-in private keys early.
- `detect-secrets` scans for common credential patterns.
- `gitleaks` adds another pass, with a small repo-specific allowlist.
- Some tests use inline annotations such as `# pragma: allowlist secret` and `# noqa: S105` because they intentionally contain fake credentials.

The Gitleaks allowlist is intentionally small and limited to tests that contain obviously fake values:

```1:14:.gitleaks.toml
# Gitleaks configuration for jenkins-job-insight
# https://github.com/gitleaks/gitleaks#configuration

[extend]
# Use the default gitleaks config as a base
useDefault = true

[allowlist]
# Allowlist test files that contain fake/mock credentials for unit tests
paths = [
    '''tests/test_config\.py''',
    '''tests/test_main\.py''',
    '''tests/conftest\.py''',
]
```

The test suite follows the same idea in code by making fake credentials obvious and annotating them locally:

```19:21:tests/test_main.py
# Fake credentials for tests — annotated once to suppress Ruff S105/S106 globally.
FAKE_JENKINS_PASSWORD = "not-a-real-password"  # noqa: S105  # pragma: allowlist secret
FAKE_GITHUB_TOKEN = "not-a-real-token"  # noqa: S105  # pragma: allowlist secret
```

> **Tip:** If you need fake tokens or passwords in a new test, keep them obviously fake and annotate them the same way existing tests do. That preserves the value of the scanners without creating noisy false positives.

There is no checked-in `.secrets.baseline`, so false-positive handling is intentionally explicit and local rather than hidden in a large baseline file.

## Repository Layout for Maintainers

The fastest way to stay productive in this repository is to treat it as a few clear layers rather than one large codebase.

| Path | What lives there |
| --- | --- |
| `src/jenkins_job_insight/` | The backend service, domain logic, storage layer, external integrations, and FastAPI routes. |
| `src/jenkins_job_insight/cli/` | The `jji` CLI client, command definitions, config loading, and output formatting. |
| `tests/` | The backend and CLI `pytest` suite. If you add behavior in `src/`, there is usually a nearby test file here that should change too. |
| `frontend/src/pages/` | Route-level React pages. |
| `frontend/src/pages/report/` | Report-specific UI and state logic, plus report-focused tests. |
| `frontend/src/components/ui/` | Low-level UI primitives. |
| `frontend/src/components/shared/` | Shared app-level components such as badges, dialogs, pagination, and protected routes. |
| `frontend/src/lib/` | Frontend utilities, API wrapper, cookies, grouping logic, hooks, and colocated unit tests. |
| `frontend/src/types/` | TypeScript types mirroring backend data structures. |
| `frontend/src/test/` | Shared frontend test setup, including `@testing-library/jest-dom`. |
| `examples/pytest-junitxml/` | A standalone example of integrating JJI with an external `pytest` suite by enriching JUnit XML. |
| `docs/` | The checked-in documentation site: Markdown source pages, paired generated HTML pages, search assets, and static CSS/JavaScript. |
| Repository root | Cross-cutting project config such as `pyproject.toml`, `tox.toml`, `.pre-commit-config.yaml`, `.gitleaks.toml`, `config.example.toml`, and the container files. |

A few practical navigation rules help:

- Start in `tests/test_main.py` when you need to understand backend HTTP behavior.
- Start in `tests/test_cli_main.py` and `tests/test_cli_client.py` when a CLI change is involved.
- Start in `src/jenkins_job_insight/peer_analysis.py` and `tests/test_peer_analysis.py` when you are working on multi-AI consensus or the debate loop.
- Start in `frontend/src/lib/` for shared browser-side logic and in `frontend/src/pages/` for route behavior.
- Start in `docs/*.md` when you are updating documentation content; the paired `docs/*.html` pages and search assets are checked in alongside them.
- Check `examples/pytest-junitxml/` if you want to see how this project can plug into someone else’s `pytest` workflow.

> **Tip:** When you add or change an API endpoint, keep the CLI in sync. In this repository that usually means updating the backend route in `src/jenkins_job_insight/main.py`, the client in `src/jenkins_job_insight/cli/client.py`, the command in `src/jenkins_job_insight/cli/main.py`, and the matching tests in `tests/test_cli_client.py` and `tests/test_cli_main.py`.


## Related Pages

- [Architecture and Project Structure](architecture-and-project-structure.html)
- [Run Locally](run-locally.html)
- [Pytest JUnit XML Integration](pytest-junitxml-integration.html)
- [API Overview](api-overview.html)
- [Troubleshooting](troubleshooting.html)