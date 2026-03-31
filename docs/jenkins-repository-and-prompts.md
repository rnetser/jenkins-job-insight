# Jenkins, Repository Context, and Prompts

For Jenkins-backed analysis through `/analyze`, `jenkins-job-insight` needs Jenkins connection details to fetch build metadata, console output, and test reports. Direct analysis through `/analyze-failures` skips Jenkins entirely. Repository cloning is optional, but it is what gives the AI source-code context, access to recent Git history, and visibility into any repo-level prompt files. Prompt customization can come from the request itself with `raw_prompt`, or from a repository file named `JOB_INSIGHT_PROMPT.md`.

| Purpose | Server-side default | Per-request field | Notes |
| --- | --- | --- | --- |
| Jenkins base URL | `JENKINS_URL` | `jenkins_url` | Required somewhere for `/analyze` |
| Jenkins username | `JENKINS_USER` | `jenkins_user` | Required somewhere for `/analyze` |
| Jenkins password or API token | `JENKINS_PASSWORD` | `jenkins_password` | Required somewhere for `/analyze` |
| Jenkins TLS verification | `JENKINS_SSL_VERIFY` | `jenkins_ssl_verify` | Optional, defaults to `true` |
| Wait for a running build | `WAIT_FOR_COMPLETION` | `wait_for_completion` | `/analyze` only, defaults to `true` |
| Poll interval while waiting | `POLL_INTERVAL_MINUTES` | `poll_interval_minutes` | `/analyze` only, defaults to `2` |
| Maximum wait time | `MAX_WAIT_MINUTES` | `max_wait_minutes` | `/analyze` only, defaults to `0` for no limit |
| Repository context | `TESTS_REPO_URL` | `tests_repo_url` | Optional |
| One-off prompt instructions | none | `raw_prompt` | Optional, request-only |

> **Note:** `tests_repo_url` and `raw_prompt` are shared analysis fields, so the same repository and prompt behavior applies to both `/analyze` and `/analyze-failures`.

## Jenkins Connection Settings

For Jenkins-backed analysis through `/analyze`, the required connection settings are still `JENKINS_URL`, `JENKINS_USER`, and `JENKINS_PASSWORD`, but they no longer have to live only in the server environment. You can set them as server-side defaults, or provide them per-request. `/analyze-failures` does not use Jenkins at all.

The project's `docker-compose.yaml` still shows a typical server-default setup:

```69:117:docker-compose.yaml
environment:
  # ===================
  # Jenkins Configuration (Required)
  # ===================
  - JENKINS_URL=${JENKINS_URL:-https://jenkins.example.com}
  - JENKINS_USER=${JENKINS_USER:-your-username}
  - JENKINS_PASSWORD=${JENKINS_PASSWORD:-your-api-token}

  # ... unrelated entries omitted ...

  # ===================
  # Optional Defaults (can be overridden per-request)
  # ===================
  - TESTS_REPO_URL=${TESTS_REPO_URL:-}
  # SSL verification for Jenkins (set to false for self-signed certs)
  - JENKINS_SSL_VERIFY=${JENKINS_SSL_VERIFY:-true}
```

If you call the REST API directly, `/analyze` also accepts `jenkins_url`, `jenkins_user`, `jenkins_password`, and `jenkins_ssl_verify` in the request body. Those request values override the server defaults for that one analysis run.

`/analyze` also supports build-monitoring controls:

- `wait_for_completion` waits for a running build to finish before analysis starts
- `poll_interval_minutes` controls how often Jenkins is checked while waiting
- `max_wait_minutes` sets a deadline for waiting, and `0` means no limit

This is useful when:

- one `jenkins-job-insight` service needs to analyze builds from more than one Jenkins
- you want to test a different credential set without changing the server environment
- you need to temporarily disable TLS verification for a specific Jenkins instance
- you want to submit analysis while a Jenkins build is still running and let the service start automatically after the build finishes

`job_name` is separate from the connection settings. It identifies the job to analyze, and it can include nested Jenkins folders such as `folder/job-name`.

> **Warning:** Set `JENKINS_SSL_VERIFY=false` only when you need to work with a self-signed or otherwise untrusted certificate. Disabling TLS verification reduces connection safety.

## Repository Context

Repository context is optional. If you configure `TESTS_REPO_URL` on the server, or send `tests_repo_url` in the request body, the service clones that repository before the AI starts analysis. If no repository URL is available, the analysis still runs, but it relies on Jenkins console output and any available artifact context instead of source code.

The runtime resolves repository context and prompt input like this:

```1321:1343:src/jenkins_job_insight/analyzer.py
# Clone repo for context BEFORE child job analysis so it's available for all jobs
# Use request value if provided, otherwise fall back to settings
tests_repo_url = request.tests_repo_url or settings.tests_repo_url
repo_context = ""
repo_path: Path | None = None
custom_prompt = ""

# Use RepositoryManager context for entire analysis (child jobs and main job)
async with contextlib.AsyncExitStack() as stack:
    if tests_repo_url:
        repo_manager = RepositoryManager()
        stack.enter_context(repo_manager)
        try:
            logger.info(f"Cloning repository: {tests_repo_url}")
            repo_path = await asyncio.to_thread(
                repo_manager.clone, str(tests_repo_url)
            )
            repo_context = f"\nRepository cloned from: {tests_repo_url}"
        except Exception as e:
            logger.warning(f"Failed to clone repository: {e}")
            repo_context = f"\nFailed to clone repo: {e}"

    custom_prompt = (request.raw_prompt or "").strip()
```

A few practical details are worth knowing:

- A request-level `tests_repo_url` wins over the server’s `TESTS_REPO_URL`.
- The clone is temporary, so repo context is there for the analysis run and then cleaned up.
- `RepositoryManager.clone()` uses a shallow clone depth of `50`, which is enough for recent Git history but not full repository history.
- When the cloned workspace includes `.git`, the AI is explicitly told it can use Git commands such as `git log` and `git diff`.

> **Tip:** If you want the AI to suggest real file-level fixes, inspect recent commits, or follow repository-specific instructions, provide a repository URL. Without repo context, it cannot inspect your codebase.

## Allowed Repository URL Schemes

The clone layer is intentionally strict. It only allows `https://` and `git://` repository URLs:

```41:46:src/jenkins_job_insight/repository.py
# Validate URL scheme to prevent SSRF and local file access
url_str = str(repo_url).lower()
if not url_str.startswith(("https://", "git://")):
    raise ValueError(
        f"Invalid repository URL scheme. Only https:// and git:// are allowed, got: {repo_url}"
    )
```

In practice, that means:

- `https://...` is the best choice for API requests
- `http://...` is not accepted by the clone layer
- `ssh://...`, `git@host:repo.git`, `file://...`, and local filesystem paths are not accepted
- malformed request URLs are rejected before analysis starts

> **Note:** The clone code allows `git://`, but the request field for `tests_repo_url` is modeled as an HTTP URL. For per-request API use, prefer `https://...`.

## Prompt Resolution

There are two user-facing ways to customize the AI’s behavior:

- `raw_prompt` for one-off, request-specific instructions
- `JOB_INSIGHT_PROMPT.md` for repository-wide, version-controlled instructions

`raw_prompt` is the per-request mechanism. The request model describes it as overriding the repo-level prompt, and the runtime injects it directly into the assembled AI prompt as an `ADDITIONAL INSTRUCTIONS` section:

```731:733:src/jenkins_job_insight/analyzer.py
custom_prompt_section = (
    f"\n\nADDITIONAL INSTRUCTIONS:\n{custom_prompt}\n" if custom_prompt else ""
)
```

`JOB_INSIGHT_PROMPT.md` is the repository-level mechanism. If the cloned repo root contains that file, the AI is told that it exists and to read and follow it:

```818:822:src/jenkins_job_insight/analyzer.py
job_insight_prompt = repo_path / JOB_INSIGHT_PROMPT_FILENAME
if job_insight_prompt.exists():
    resources.append(
        f"- Project-specific analysis instructions at {job_insight_prompt} — read and follow them"
    )
```

In day-to-day use, the simplest way to think about it is:

- Use `raw_prompt` when you want to influence one request without changing the repository.
- Use `JOB_INSIGHT_PROMPT.md` when you want stable project guidance to travel with the codebase.
- Treat `raw_prompt` as the higher-priority, request-scoped instruction source.
- `JOB_INSIGHT_PROMPT.md` only works when repository cloning is enabled and successful.
- Only the repository root is checked automatically for `JOB_INSIGHT_PROMPT.md`.

> **Tip:** Put long-lived project rules in `JOB_INSIGHT_PROMPT.md`, and reserve `raw_prompt` for temporary, incident-specific, or experiment-specific guidance.

> **Warning:** A `JOB_INSIGHT_PROMPT.md` file in a subdirectory will not be discovered automatically. The lookup is for the cloned repo root only.
