import asyncio
import json
import math
import os
import time as _time
import urllib.parse
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Coroutine
from xml.etree.ElementTree import ParseError

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import SecretStr
from simple_logger.logger import get_logger

from ai_cli_runner import VALID_AI_PROVIDERS, run_parallel_with_limit
from jenkins_job_insight.analyzer import (
    clone_additional_repos,
    resolve_additional_repos,
    analyze_failure_group,
    analyze_job,
    format_exception_with_type,
    get_failure_signature,
)
from jenkins_job_insight.config import (
    Settings,
    get_settings,
    parse_peer_configs,
    parse_repo_ref,
)
from jenkins_job_insight.encryption import (
    SENSITIVE_KEYS,
    decrypt_sensitive_fields,
    encrypt_sensitive_fields,
)
from jenkins_job_insight.jira import enrich_with_jira_matches
from jenkins_job_insight.bug_creation import (
    create_github_issue,
    create_jira_bug,
    generate_github_issue_content,
    generate_jira_bug_content,
    search_github_duplicates,
    search_jira_duplicates,
)
from jenkins_job_insight.models import (
    AddCommentRequest,
    AnalyzeFailuresRequest,
    AnalyzeRequest,
    BaseAnalysisRequest,
    ChildJobAnalysis,
    ClassifyTestRequest,
    CreateIssueRequest,
    FailureAnalysis,
    FailureAnalysisResult,
    OverrideClassificationRequest,
    PreviewIssueRequest,
    SetReviewedRequest,
)
from jenkins_job_insight.xml_enrichment import (
    build_enriched_xml,
    extract_test_failures,
)
from jenkins_job_insight.repository import RepositoryManager, derive_test_repo_name
from jenkins_job_insight import storage
from jenkins_job_insight.storage import (
    get_ai_configs,
    get_effective_classification,
    get_result,
    init_db,
    list_results,
    list_results_for_dashboard,
    patch_result_json,
    populate_failure_history,
    save_result,
    update_progress_phase,
    update_status,
)

# Inline favicon
FAVICON_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y="0.9em" font-size="90">\xf0\x9f\x94\x8d</text></svg>'

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))

# Statuses that indicate the analysis is still in progress.
IN_PROGRESS_STATUSES = ("pending", "running", "waiting")

AI_PROVIDER = os.getenv("AI_PROVIDER", "").lower()
AI_MODEL = os.getenv("AI_MODEL", "")


def _read_app_port() -> int:
    """Parse and validate the PORT environment variable.

    Returns:
        The validated integer port number.

    Raises:
        SystemExit: If PORT is not a valid integer or is out of range.
    """
    raw_port = os.environ.get("PORT", "8000")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit(
            f"Invalid PORT environment variable: {raw_port!r}. Must be an integer."
        ) from exc
    if not 1 <= port <= 65535:
        raise SystemExit(
            f"Invalid PORT environment variable: {raw_port!r}. Must be between 1 and 65535."
        )
    return port


# APP_PORT is the single source of truth for the server port.
# Used by both uvicorn bind (run()) and internal AI self-calls (_build_internal_server_url()).
# If overriding, set the PORT env var — the Dockerfile's --port should match.
APP_PORT = _read_app_port()


def _build_internal_server_url() -> str:
    """Build the internal server URL for AI tool access."""
    url = f"http://localhost:{APP_PORT}"
    logger.debug(f"Built internal server_url={url} for AI tool access")
    return url


def build_jenkins_url(base_url: str, job_name: str, build_number: int) -> str:
    """Construct full Jenkins build URL from job name and build number.

    Args:
        base_url: Base Jenkins URL from settings.
        job_name: Job name (can include folders like "folder/job-name").
        build_number: Build number.

    Returns:
        Full Jenkins build URL.
    """
    # Handle folder-style job names by URL-encoding each segment and joining with '/job/'
    segments = job_name.split("/")
    encoded_segments = [urllib.parse.quote(segment, safe="") for segment in segments]
    job_path = "/job/".join(encoded_segments)
    return f"{base_url.rstrip('/')}/job/{job_path}/{build_number}/"


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


def _build_report_context(
    include_links: bool,
    base_url: str,
    job_id: str,
    result_data: dict,
) -> tuple[str, str]:
    """Build report URL and Jenkins URL for bug preview endpoints.

    When ``include_links`` is *True* the returned URLs are fully-qualified
    hyperlinks.  Otherwise plain-text identifiers are returned so that
    previews remain useful without clickable links.

    Args:
        include_links: Whether to produce full hyperlinks.
        base_url: The external base URL of the service.
        job_id: The stored job identifier.
        result_data: Raw result dict from storage (contains jenkins_url, job_name, etc.).

    Returns:
        A ``(report_url, jenkins_url)`` tuple.
    """
    jenkins_url = result_data.get("jenkins_url", "")

    if include_links and base_url:
        report_url = f"{base_url}/results/{job_id}"
    else:
        report_url = f"/results/{job_id}"
        job_name = result_data.get("job_name", "")
        build_number = result_data.get("build_number", 0)
        jenkins_url = f"{job_name} #{build_number}" if job_name else ""

    return report_url, jenkins_url


def _attach_result_links(payload: dict, base_url: str, job_id: str) -> dict:
    """Attach ``base_url`` and ``result_url`` to a response payload."""
    payload["base_url"] = base_url
    result_url = f"{base_url}/results/{job_id}"
    payload["result_url"] = result_url
    return payload


def _recompose_repo_spec(url: str, ref: str) -> str:
    """Recompose 'url:ref' from stored components. Returns url alone when ref is empty."""
    if not url:
        return ""
    return f"{url}:{ref}" if ref else url


def _reconstruct_from_params(
    result_data: dict,
) -> tuple[AnalyzeRequest, Settings]:
    """Reconstruct an AnalyzeRequest and Settings from stored request_params.

    Args:
        result_data: Stored result dict containing ``job_name``,
            ``build_number``, and ``request_params``.

    Returns:
        Tuple of (AnalyzeRequest, Settings).
    """
    params = decrypt_sensitive_fields(result_data["request_params"])
    # Fail fast if any sensitive field is still encrypted (key changed / corrupt)
    for _key in SENSITIVE_KEYS:
        _val = params.get(_key)
        if isinstance(_val, str) and _val.startswith("enc:"):
            raise ValueError(
                f"Cannot resume waiting job: stored {_key} could not be decrypted"
            )
    body = AnalyzeRequest(
        job_name=result_data["job_name"],
        build_number=result_data["build_number"],
        ai_provider=params.get("ai_provider", ""),
        ai_model=params.get("ai_model", ""),
        wait_for_completion=params.get("wait_for_completion", True),
        poll_interval_minutes=params.get("poll_interval_minutes", 2),
        max_wait_minutes=params.get("max_wait_minutes", 0),
        enable_jira=params.get("enable_jira"),
        raw_prompt=params.get("raw_prompt") or None,
        tests_repo_url=_recompose_repo_spec(
            params.get("tests_repo_url", ""), params.get("tests_repo_ref", "")
        )
        or None,
        peer_ai_configs=(
            params["peer_ai_configs"] if "peer_ai_configs" in params else []
        ),
        peer_analysis_max_rounds=params.get("peer_analysis_max_rounds", 3),
        additional_repos=(
            params["additional_repos"] if "additional_repos" in params else None
        ),
    )
    # Build Settings from env defaults, then layer stored overrides
    base_settings = get_settings()
    overrides: dict = {}
    settings_fields = [
        "jenkins_url",
        "jenkins_user",
        "jenkins_password",
        "jenkins_ssl_verify",
        "wait_for_completion",
        "poll_interval_minutes",
        "max_wait_minutes",
        "jira_url",
        "jira_email",
        "jira_project_key",
        "jira_ssl_verify",
        "jira_max_results",
        "ai_cli_timeout",
        "jenkins_artifacts_max_size_mb",
        "jenkins_artifacts_context_lines",
        "get_job_artifacts",
        "peer_analysis_max_rounds",
    ]
    for field in settings_fields:
        if field in params:
            overrides[field] = params[field]

    # Tests repo URL
    recomposed = _recompose_repo_spec(
        params.get("tests_repo_url", ""), params.get("tests_repo_ref", "")
    )
    if recomposed:
        overrides["tests_repo_url"] = recomposed

    # SecretStr fields
    if params.get("jira_api_token"):
        overrides["jira_api_token"] = SecretStr(params["jira_api_token"])
    if params.get("jira_pat"):
        overrides["jira_pat"] = SecretStr(params["jira_pat"])
    if params.get("github_token"):
        overrides["github_token"] = SecretStr(params["github_token"])

    # Enable jira
    if params.get("enable_jira") is not None:
        overrides["enable_jira"] = params["enable_jira"]

    if overrides:
        merged_data = base_settings.model_dump(mode="python") | overrides
        merged = Settings.model_validate(merged_data)
    else:
        merged = base_settings

    return body, merged


_background_tasks: set[asyncio.Task] = set()


async def _preserve_request_params(job_id: str, result_data: dict) -> None:
    """Copy ``request_params`` from the stored result into *result_data*.

    The initial ``save_result`` persists ``request_params`` (ai_provider,
    ai_model, peer_ai_configs, etc.) but the ``AnalysisResult`` model dump
    produced when analysis finishes does not include that key.  Without this
    merge the params would be silently lost when ``update_status`` overwrites
    ``result_json``.

    Args:
        job_id: The analysis job identifier.
        result_data: Mutable dict that will be written to ``result_json``.
            Modified in place to add ``request_params`` when available.
    """
    stored = await get_result(job_id, strip_sensitive=False)
    if stored and stored.get("result") and "request_params" in stored["result"]:
        result_data["request_params"] = stored["result"]["request_params"]


async def _fail_resumed_waiting_job(job_id: str, result_data: dict, error: str) -> None:
    """Mark a resumed waiting job as failed with a standard payload.

    Args:
        job_id: The job identifier.
        result_data: The stored result data dict for the job.
        error: Human-readable error message.
    """
    fail_data = {
        "job_name": result_data.get("job_name", ""),
        "build_number": result_data.get("build_number", 0),
        "error": error,
    }
    if "request_params" in result_data:
        fail_data["request_params"] = result_data["request_params"]
    await storage.update_status(job_id, "failed", fail_data)


async def _resume_waiting_jobs(waiting_jobs: list[dict]) -> None:
    """Resume waiting jobs by re-creating their background tasks.

    Args:
        waiting_jobs: List of dicts with ``job_id`` and ``result_data``
            returned by ``mark_stale_results_failed``.
    """
    for job in waiting_jobs:
        result_data = job["result_data"]
        params = result_data.get("request_params")
        if not params:
            logger.warning(
                f"Waiting job {job['job_id']} has no request_params, marking as failed"
            )
            await _fail_resumed_waiting_job(
                job["job_id"],
                result_data,
                "Cannot resume: no request_params stored (queued before resume support)",
            )
            continue

        try:
            body, merged = _reconstruct_from_params(result_data)
        except Exception as exc:
            logger.warning(
                f"Failed to reconstruct params for waiting job {job['job_id']}: {exc}"
            )
            await _fail_resumed_waiting_job(
                job["job_id"],
                result_data,
                f"Cannot resume: failed to reconstruct request params: {exc}",
            )
            continue

        # Adjust max_wait_minutes to account for time already elapsed before
        # the restart, so the original deadline is honoured.
        raw_wait_started_at = params.get("wait_started_at")
        wait_started_at: float | None = None
        if raw_wait_started_at is not None:
            try:
                wait_started_at = float(raw_wait_started_at)
            except (TypeError, ValueError):
                await _fail_resumed_waiting_job(
                    job["job_id"],
                    result_data,
                    f"Cannot resume: malformed wait_started_at value: {raw_wait_started_at!r}",
                )
                continue
            if not math.isfinite(wait_started_at):
                await _fail_resumed_waiting_job(
                    job["job_id"],
                    result_data,
                    f"Cannot resume: non-finite wait_started_at value: {raw_wait_started_at!r}",
                )
                continue
        if merged.max_wait_minutes > 0 and wait_started_at is not None:
            elapsed_minutes = (_time.time() - wait_started_at) / 60
            remaining = merged.max_wait_minutes - elapsed_minutes
            if remaining <= 0:
                await _fail_resumed_waiting_job(
                    job["job_id"],
                    result_data,
                    (
                        f"Timed out waiting for Jenkins job "
                        f"{result_data.get('job_name')} #{result_data.get('build_number')} "
                        f"after {merged.max_wait_minutes} minutes (deadline passed during restart)"
                    ),
                )
                continue
            merged_data = merged.model_dump(mode="python")
            merged_data["max_wait_minutes"] = max(1, math.ceil(remaining))
            merged = Settings.model_validate(merged_data)

        task = asyncio.create_task(
            process_analysis_with_id(job["job_id"], body, merged)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        logger.info(
            f"Resumed waiting job {job['job_id']} "
            f"({result_data.get('job_name')} #{result_data.get('build_number')})"
        )


async def _deferred_resume_waiting_jobs(waiting_jobs: list[dict]) -> None:
    """Resume waiting jobs after startup is complete.

    Waits briefly so uvicorn finishes binding and the app is ready to
    serve internal API requests before any resumed job transitions to
    the "running" phase.
    """
    await asyncio.sleep(1)
    await _resume_waiting_jobs(waiting_jobs)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    waiting_jobs = await storage.mark_stale_results_failed()
    if waiting_jobs:
        # Schedule resumption as a background task so it runs after the
        # app is fully started and ready to serve internal API requests.
        task = asyncio.create_task(_deferred_resume_waiting_jobs(waiting_jobs))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    yield


app = FastAPI(
    title="Jenkins Job Insight",
    description="Analyzes Jenkins job failures and classifies them as code or product issues",
    version="0.1.0",
    lifespan=lifespan,
)

# React frontend static assets
_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _FRONTEND_DIR.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_FRONTEND_DIR / "assets")),
        name="frontend-assets",
    )


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


app.add_middleware(UsernameMiddleware)


@app.get("/", include_in_schema=False)
async def root() -> HTMLResponse:
    """Serve the React SPA."""
    return _serve_spa()


def _resolve_ai_config_values(
    ai_provider: str | None, ai_model: str | None
) -> tuple[str, str]:
    """Resolve and validate AI provider and model from given values or env defaults.

    Args:
        ai_provider: Provider from request body (or None).
        ai_model: Model from request body (or None).

    Returns:
        Tuple of (ai_provider, ai_model).

    Raises:
        HTTPException: If provider or model is not configured.
    """
    provider = ai_provider or AI_PROVIDER
    model = ai_model or AI_MODEL
    if not provider:
        raise HTTPException(
            status_code=400,
            detail=f"No AI provider configured. Set AI_PROVIDER env var or pass ai_provider in request body. Valid providers: {', '.join(sorted(VALID_AI_PROVIDERS))}",
        )
    if provider not in VALID_AI_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported AI provider: {provider}. "
                f"Valid providers: {', '.join(sorted(VALID_AI_PROVIDERS))}"
            ),
        )
    if not model:
        raise HTTPException(
            status_code=400,
            detail="No AI model configured. Set AI_MODEL env var or pass ai_model in request body.",
        )
    return provider, model


def _resolve_ai_config(body: AnalyzeRequest) -> tuple[str, str]:
    """Resolve AI config from an AnalyzeRequest."""
    return _resolve_ai_config_values(body.ai_provider, body.ai_model)


def _resolve_peer_ai_configs(
    body: BaseAnalysisRequest, settings: Settings
) -> list | None:
    """Resolve peer AI configs from request body or env var default.

    Priority:
    - Request field absent (None) -> use server default from PEER_AI_CONFIGS env var
    - Request field present and empty ([]) -> explicitly disable peers
    - Request field present and non-empty -> use request value

    Returns:
        List of peer config dicts/AiConfigEntry, or None if no peers configured.
    """
    if body.peer_ai_configs is not None:
        return body.peer_ai_configs or None  # [] -> None (disable)
    # Fall back to env var default (string format)
    if settings.peer_ai_configs:
        return parse_peer_configs(settings.peer_ai_configs) or None
    return None


def _validate_peer_configs(
    body: BaseAnalysisRequest, settings: Settings
) -> list | None:
    """Resolve and validate peer AI configs. Raises HTTPException(400) on invalid input."""
    try:
        return _resolve_peer_ai_configs(body, settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _resolve_enable_jira(body: BaseAnalysisRequest, settings: Settings) -> bool:
    """Resolve enable_jira flag from request, env var, or auto-detection.

    Priority order:
    1. Request body field (highest)
    2. ENABLE_JIRA env var (via settings)
    3. Auto-detect from Jira credentials (lowest)

    Args:
        body: The analysis request (AnalyzeRequest or AnalyzeFailuresRequest).
        settings: Application settings (should be merged settings).

    Returns:
        True if Jira enrichment should run, False otherwise.
    """
    if body.enable_jira is not None:
        return body.enable_jira
    return settings.jira_enabled


def _merge_settings(body: BaseAnalysisRequest, settings: Settings) -> Settings:
    """Create a copy of settings with per-request overrides applied.

    Request values take precedence over environment variable defaults.
    Only non-None request values are applied as overrides.

    Args:
        body: The analysis request with optional override fields.
        settings: Base application settings from environment.

    Returns:
        Settings instance with overrides applied (or original if no overrides).
    """
    overrides: dict = {}

    # Direct field mappings (request field name == settings field name).
    # Keep in sync with BaseAnalysisRequest and Settings when adding new overrides.
    # Fields intentionally NOT listed here are handled by their own resolvers:
    #   tests_repo_url   - HttpUrl vs str type mismatch; resolved in endpoint code
    #   ai_provider      - resolved + validated by _resolve_ai_config()
    #   ai_model         - resolved + validated by _resolve_ai_config()
    #   additional_repos - list vs str type mismatch; resolved by resolve_additional_repos()
    direct_fields = [
        "jira_url",
        "jira_email",
        "jira_project_key",
        "jira_ssl_verify",
        "jira_max_results",
        "ai_cli_timeout",
        "enable_jira",
        "jenkins_artifacts_max_size_mb",
        "jenkins_artifacts_context_lines",
        "get_job_artifacts",
    ]
    for field in direct_fields:
        value = getattr(body, field, None)
        if value is not None:
            overrides[field] = value

    # peer_analysis_max_rounds has a non-None default in the model;
    # only apply as override when explicitly sent by the caller.
    if "peer_analysis_max_rounds" in body.model_fields_set:
        overrides["peer_analysis_max_rounds"] = body.peer_analysis_max_rounds

    # SecretStr fields need wrapping
    if body.jira_api_token is not None:
        overrides["jira_api_token"] = SecretStr(body.jira_api_token)
    if body.jira_pat is not None:
        overrides["jira_pat"] = SecretStr(body.jira_pat)
    if body.github_token is not None:
        overrides["github_token"] = SecretStr(body.github_token)

    # AnalyzeRequest-specific fields (Jenkins overrides + monitoring)
    if isinstance(body, AnalyzeRequest):
        jenkins_fields = [
            "jenkins_url",
            "jenkins_user",
            "jenkins_password",
            "jenkins_ssl_verify",
        ]
        for field in jenkins_fields:
            value = getattr(body, field, None)
            if value is not None:
                overrides[field] = value

        # Monitoring fields have non-None defaults in the model.  Only
        # apply them as overrides when explicitly sent by the caller
        # (present in ``model_fields_set``) so that omitted fields fall
        # back to the environment/settings default instead of always
        # overriding with the model default.
        for field in (
            "wait_for_completion",
            "poll_interval_minutes",
            "max_wait_minutes",
        ):
            if field in body.model_fields_set:
                overrides[field] = getattr(body, field)

    if overrides:
        merged_data = settings.model_dump(mode="python") | overrides
        return Settings.model_validate(merged_data)
    return settings


async def _enrich_result_with_jira(
    failures: list[FailureAnalysis | ChildJobAnalysis],
    settings: Settings,
    ai_provider: str = "",
    ai_model: str = "",
) -> None:
    """Enrich PRODUCT BUG failures with Jira matches.

    Collects all FailureAnalysis objects from the provided list,
    recursing into ChildJobAnalysis objects, then searches Jira
    for matching issues. Results are attached in-place.

    Args:
        failures: Mixed list of FailureAnalysis and ChildJobAnalysis objects.
        settings: Application settings with Jira configuration.
        ai_provider: AI provider for Jira relevance filtering.
        ai_model: AI model for Jira relevance filtering.
    """
    if not settings.jira_enabled:
        return

    all_failures: list[FailureAnalysis] = []

    def _collect(items: list) -> None:
        for item in items:
            if isinstance(item, FailureAnalysis):
                all_failures.append(item)
            elif isinstance(item, ChildJobAnalysis):
                _collect(item.failures)
                _collect(item.failed_children)

    _collect(failures)

    await enrich_with_jira_matches(all_failures, settings, ai_provider, ai_model)


async def _wait_for_jenkins_completion(
    jenkins_url: str,
    job_name: str,
    build_number: int,
    jenkins_user: str,
    jenkins_password: str,
    jenkins_ssl_verify: bool,
    poll_interval_minutes: int,
    max_wait_minutes: int,
) -> tuple[bool, str]:
    """Poll Jenkins until the build finishes.

    Args:
        jenkins_url: Jenkins server base URL.
        job_name: Name of the Jenkins job.
        build_number: Build number to monitor.
        jenkins_user: Jenkins username for authentication.
        jenkins_password: Jenkins password or API token.
        jenkins_ssl_verify: Whether to verify SSL certificates.
        poll_interval_minutes: Minutes between polls.
        max_wait_minutes: Maximum minutes to wait before timing out.
            0 means no limit (poll forever until job finishes).

    Returns:
        A tuple of (success, error_message). success is True if the build
        completed, False otherwise. error_message is empty on success.
    """
    import jenkins

    from jenkins_job_insight.jenkins import JenkinsClient

    client = JenkinsClient(
        url=jenkins_url,
        username=jenkins_user,
        password=jenkins_password,
        ssl_verify=jenkins_ssl_verify,
    )

    if max_wait_minutes > 0:
        deadline: float | None = _time.monotonic() + max_wait_minutes * 60
    else:
        deadline = None  # No limit

    while True:
        try:
            build_info = await asyncio.to_thread(
                client.get_build_info_safe, job_name, build_number
            )

            if build_info and not build_info.get("building", True):
                logger.info(
                    f"Jenkins job {job_name} #{build_number} completed "
                    f"with result: {build_info.get('result')}"
                )
                return True, ""

            logger.info(f"Jenkins job {job_name} #{build_number} still running")

        except jenkins.NotFoundException:
            logger.error(
                f"Jenkins job {job_name} #{build_number} not found (404). "
                "Stopping poll."
            )
            return False, f"Jenkins job {job_name} #{build_number} not found (404)"

        except (OSError, TimeoutError) as e:
            logger.warning(f"Transient error checking Jenkins status: {e}")

        except Exception as e:
            logger.error(f"Non-transient error checking Jenkins status: {e}")
            return False, f"Jenkins poll failed: {e}"

        if deadline is not None:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval_minutes * 60, remaining))
        else:
            await asyncio.sleep(poll_interval_minutes * 60)

    error_msg = (
        f"Timed out waiting for Jenkins job {job_name} #{build_number} "
        f"after {max_wait_minutes} minutes"
    )
    logger.warning(error_msg)
    return False, error_msg


async def process_analysis_with_id(
    job_id: str, body: AnalyzeRequest, settings: Settings
) -> None:
    """Background task to process analysis with a pre-generated job_id.

    Args:
        job_id: Pre-generated job ID for tracking.
        body: The analysis request.
        settings: Application settings.
    """
    logger.info(
        f"Analysis request received for {body.job_name} #{body.build_number} "
        f"(job_id: {job_id})"
    )

    async def _safe_update_progress_phase(phase: str) -> None:
        try:
            await update_progress_phase(job_id, phase)
        except Exception:
            logger.debug(
                f"Failed to update progress phase for job_id={job_id}, phase={phase}",
                exc_info=True,
            )

    try:
        # Validate AI config early -- before potentially waiting hours for Jenkins.
        # This ensures invalid provider/model fails fast instead of after a long wait.
        ai_provider, ai_model = _resolve_ai_config(body)

        # Wait for Jenkins job to finish if requested and Jenkins is configured
        if settings.wait_for_completion and not settings.jenkins_url:
            logger.info(
                f"Wait requested for job {job_id} but jenkins_url not configured, skipping wait"
            )

        if settings.wait_for_completion and settings.jenkins_url:
            await update_status(job_id, "waiting")
            await _safe_update_progress_phase("waiting_for_jenkins")

            completed, wait_error = await _wait_for_jenkins_completion(
                jenkins_url=settings.jenkins_url,
                job_name=body.job_name,
                build_number=body.build_number,
                jenkins_user=settings.jenkins_user,
                jenkins_password=settings.jenkins_password,
                jenkins_ssl_verify=settings.jenkins_ssl_verify,
                poll_interval_minutes=settings.poll_interval_minutes,
                max_wait_minutes=settings.max_wait_minutes,
            )

            if not completed:
                await update_status(
                    job_id,
                    "failed",
                    {
                        "job_name": body.job_name,
                        "build_number": body.build_number,
                        "error": wait_error,
                    },
                )
                return

        logger.debug(
            f"process_analysis_with_id: updating status to running, job_id={job_id}"
        )
        await update_status(job_id, "running")
        await _safe_update_progress_phase("analyzing")

        logger.debug(
            f"process_analysis_with_id: ai_provider={ai_provider}, ai_model={ai_model}"
        )

        server_url = _build_internal_server_url()

        # Resolve peer AI configs: request body (JSON list) takes precedence
        # over env var default (parsed from "provider:model" string).
        # None = not sent → use env default; [] = explicitly disable peers.
        peer_ai_configs = _resolve_peer_ai_configs(body, settings)

        result = await analyze_job(
            body,
            settings,
            ai_provider=ai_provider,
            ai_model=ai_model,
            job_id=job_id,
            server_url=server_url,
            peer_ai_configs=peer_ai_configs,
            peer_analysis_max_rounds=settings.peer_analysis_max_rounds,
        )

        # Enrich PRODUCT BUG failures with Jira matches
        if _resolve_enable_jira(body, settings):
            await _safe_update_progress_phase("enriching_jira")
            logger.debug(
                f"process_analysis_with_id: enriching with Jira matches, job_id={job_id}"
            )
            await _enrich_result_with_jira(
                result.failures + list(result.child_job_analyses),
                settings,
                ai_provider,
                ai_model,
            )

        await _safe_update_progress_phase("saving")
        logger.debug(
            f"process_analysis_with_id: saving completed result, job_id={job_id}"
        )
        result_data = result.model_dump(mode="json")
        await _preserve_request_params(job_id, result_data)

        # Save to storage — do NOT persist base_url / result_url as they are
        # request-derived and re-generated on every GET to avoid host-header
        # injection from being stored.
        await update_status(job_id, result.status, result_data)
        logger.info(
            f"Analysis completed for {body.job_name} #{body.build_number} "
            f"(job_id: {job_id})"
        )

        # Populate failure history for completed analyses
        if result.status == "completed":
            try:
                await populate_failure_history(job_id, result_data)
            except Exception:
                logger.warning(
                    "Failed to populate failure_history for job_id=%s",
                    job_id,
                    exc_info=True,
                )

        # Reveal classifications created during analysis
        await storage.make_classifications_visible(job_id)

    except Exception as e:
        logger.exception(f"Analysis failed for job {job_id}")
        error_detail = format_exception_with_type(e)
        fail_data: dict = {
            "job_name": body.job_name,
            "build_number": body.build_number,
            "error": error_detail,
        }
        await _preserve_request_params(job_id, fail_data)
        await update_status(job_id, "failed", fail_data)


def _build_base_request_params(
    ai_provider: str,
    ai_model: str,
    peer_ai_configs_resolved: list | None = None,
    *,
    tests_repo_url: str = "",
    tests_repo_ref: str = "",
    additional_repos: list | None = None,
) -> dict:
    """Serialize the common request parameters shared by all analysis endpoints.

    Captures the AI configuration, peer configs, tests repo, and additional
    repos.  Callers pass the **resolved** (effective) values so that env-var
    and config-file defaults are persisted, not just request-body values.

    Args:
        ai_provider: Resolved AI provider name.
        ai_model: Resolved AI model name.
        peer_ai_configs_resolved: Resolved peer AI configs (already validated).
        tests_repo_url: Effective tests repo URL (already resolved from
            request body / env / config).
        additional_repos: Effective additional repos list (already resolved).

    Returns:
        Dict of serializable base request parameters.
    """
    return {
        "ai_provider": ai_provider,
        "ai_model": ai_model,
        "peer_ai_configs": [
            c.model_dump() if hasattr(c, "model_dump") else c
            for c in (peer_ai_configs_resolved or [])
        ],
        "additional_repos": [
            ar.model_dump(mode="json") if hasattr(ar, "model_dump") else ar
            for ar in additional_repos
        ]
        if additional_repos is not None
        else None,
        "tests_repo_url": tests_repo_url,
        "tests_repo_ref": tests_repo_ref,
    }


def _build_request_params(
    body: AnalyzeRequest,
    merged: Settings,
    ai_provider: str,
    ai_model: str,
    peer_ai_configs_resolved: list | None = None,
) -> dict:
    """Serialize the request parameters needed to resume a waiting job.

    Captures everything ``process_analysis_with_id`` needs so that the
    background task can be re-created after a server restart.

    Args:
        body: The original analysis request.
        merged: Settings with per-request overrides applied.
        ai_provider: Resolved AI provider name.
        ai_model: Resolved AI model name.

    Returns:
        Dict of serializable request parameters.
    """
    resolved_tests_repo = (
        str(body.tests_repo_url)
        if body.tests_repo_url is not None
        else str(merged.tests_repo_url)
        if merged.tests_repo_url
        else ""
    )
    resolved_tests_repo, tests_repo_ref = parse_repo_ref(resolved_tests_repo)
    resolved_additional = resolve_additional_repos(body, merged)
    params = _build_base_request_params(
        ai_provider,
        ai_model,
        peer_ai_configs_resolved,
        tests_repo_url=resolved_tests_repo,
        tests_repo_ref=tests_repo_ref,
        additional_repos=resolved_additional,
    )
    params.update(
        {
            "jenkins_url": merged.jenkins_url,
            "jenkins_user": merged.jenkins_user,
            "jenkins_password": merged.jenkins_password,
            "jenkins_ssl_verify": merged.jenkins_ssl_verify,
            "wait_for_completion": merged.wait_for_completion,
            "poll_interval_minutes": merged.poll_interval_minutes,
            "max_wait_minutes": merged.max_wait_minutes,
            "enable_jira": body.enable_jira
            if body.enable_jira is not None
            else merged.enable_jira,
            "jira_url": merged.jira_url or "",
            "jira_email": merged.jira_email or "",
            "jira_api_token": merged.jira_api_token.get_secret_value()
            if merged.jira_api_token
            else "",
            "jira_pat": merged.jira_pat.get_secret_value() if merged.jira_pat else "",
            "jira_project_key": merged.jira_project_key or "",
            "jira_ssl_verify": merged.jira_ssl_verify,
            "jira_max_results": merged.jira_max_results,
            "github_token": merged.github_token.get_secret_value()
            if merged.github_token
            else "",
            "ai_cli_timeout": merged.ai_cli_timeout,
            "jenkins_artifacts_max_size_mb": merged.jenkins_artifacts_max_size_mb,
            "jenkins_artifacts_context_lines": merged.jenkins_artifacts_context_lines,
            "get_job_artifacts": merged.get_job_artifacts,
            "raw_prompt": body.raw_prompt or "",
            "peer_analysis_max_rounds": merged.peer_analysis_max_rounds,
            "wait_started_at": _time.time(),
        }
    )
    return encrypt_sensitive_fields(params)


@app.post("/analyze", status_code=202, response_model=None)
async def analyze(
    request: Request,
    body: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    *,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Submit a Jenkins job for analysis.

    Returns immediately with a job_id. Poll /results/{job_id} for status.
    """
    logger.debug(f"Starting analysis for {body.job_name} #{body.build_number}")
    base_url = _extract_base_url()

    # Validate AI config early -- fail fast before queuing invalid jobs.
    _resolve_ai_config(body)

    # Generate job_id here so we can return it to the client for polling
    job_id = str(uuid.uuid4())
    merged = _merge_settings(body, settings)

    # Validate peer configs early -- fail fast before returning 202.
    resolved_peers = _validate_peer_configs(body, merged)
    jenkins_url = build_jenkins_url(
        merged.jenkins_url, body.job_name, body.build_number
    )
    # Save initial pending state before queueing background task.
    # request_params is always persisted so the status page can display
    # AI provider/model and peer configs, and waiting jobs can resume
    # after a server restart.
    initial_result: dict = {
        "job_name": body.job_name,
        "build_number": body.build_number,
        "request_params": _build_request_params(
            body,
            merged,
            body.ai_provider or AI_PROVIDER,
            body.ai_model or AI_MODEL,
            peer_ai_configs_resolved=resolved_peers,
        ),
    }
    can_resume_wait = merged.wait_for_completion and bool(merged.jenkins_url)
    await save_result(
        job_id,
        jenkins_url,
        "waiting" if can_resume_wait else "pending",
        initial_result,
    )
    background_tasks.add_task(process_analysis_with_id, job_id, body, merged)
    message = f"Analysis job queued. Poll /results/{job_id} for status."

    response: dict = {
        "status": "queued",
        "job_id": job_id,
        "message": message,
    }

    return _attach_result_links(response, base_url, job_id)


@app.post("/analyze-failures", response_model=None)
async def analyze_failures(
    request: Request,
    body: AnalyzeFailuresRequest,
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Analyze raw test failures directly without Jenkins.

    Accepts test failure data (or raw JUnit XML) and returns AI analysis.
    Sync only. Reuses the same deduplication and analysis logic as Jenkins-based analysis.

    When raw_xml is provided, failures are extracted from the XML and the enriched
    XML with analysis results is included in the response.
    """
    logger.debug(
        f"POST /analyze-failures: failures_count={len(body.failures) if body.failures else 0}, has_raw_xml={body.raw_xml is not None}"
    )
    base_url = _extract_base_url()

    if raw_xml := body.raw_xml:
        try:
            test_failures = extract_test_failures(raw_xml)
        except ParseError as e:
            raise HTTPException(status_code=400, detail=f"Invalid XML: {e}") from e

        if not test_failures:
            job_id = str(uuid.uuid4())
            analysis_result = FailureAnalysisResult(
                job_id=job_id,
                status="completed",
                summary="No test failures found in the provided XML.",
                enriched_xml=raw_xml,
            )
            result_data = analysis_result.model_dump(mode="json")
            await save_result(job_id, "", "completed", result_data)
            return JSONResponse(
                content=_attach_result_links(result_data, base_url, job_id)
            )
    else:
        if not body.failures:
            raise HTTPException(status_code=400, detail="No failures provided")
        test_failures = body.failures

    merged = _merge_settings(body, settings)
    ai_provider, ai_model = _resolve_ai_config_values(body.ai_provider, body.ai_model)

    # Validate/resolve peer configs early -- fail fast before saving result.
    peer_ai_configs = _validate_peer_configs(body, merged)

    # Resolve repos early so _build_base_request_params captures effective values
    # (including env-var / config-file defaults, not just request-body values).
    tests_repo_url_raw = str(body.tests_repo_url or merged.tests_repo_url or "")
    tests_repo_url, tests_repo_ref = parse_repo_ref(tests_repo_url_raw)
    additional_repos_list = resolve_additional_repos(body, merged)

    job_id = str(uuid.uuid4())
    logger.info(
        f"Direct failure analysis request received with {len(test_failures)} failures (job_id: {job_id})"
    )

    # Save initial pending state with request_params so GET /results/{job_id}
    # works immediately and _preserve_request_params can find them later.
    initial_result: dict = {
        "request_params": _build_base_request_params(
            ai_provider,
            ai_model,
            peer_ai_configs,
            tests_repo_url=tests_repo_url,
            tests_repo_ref=tests_repo_ref,
            additional_repos=additional_repos_list or None,
        ),
    }
    await save_result(job_id, "", "pending", initial_result)

    # Group failures by error signature for deduplication
    groups: dict[str, list] = defaultdict(list)
    for failure in test_failures:
        sig = get_failure_signature(failure)
        groups[sig].append(failure)

    logger.info(
        f"Grouped {len(test_failures)} failures into {len(groups)} unique error signatures"
    )

    # Always create a workspace for AI to work in
    repo_manager = RepositoryManager()
    custom_prompt = ""
    cloned_repos: dict[str, Path] = {}
    try:
        await update_status(job_id, "running")

        repo_path = repo_manager.create_workspace()

        if tests_repo_url:
            try:
                repo_name = derive_test_repo_name(
                    str(tests_repo_url), additional_repos_list
                )
                logger.info(f"Cloning test repository: {tests_repo_url}")
                await asyncio.to_thread(
                    repo_manager.clone_into,
                    str(tests_repo_url),
                    repo_path / repo_name,
                    depth=50,
                    branch=tests_repo_ref,
                )
                cloned_repos[repo_name] = repo_path / repo_name
            except Exception as e:
                logger.warning(f"Failed to clone test repository: {e}")

        # Clone additional repositories for AI context
        if additional_repos_list:
            additional_repos_cloned, repo_path = await clone_additional_repos(
                repo_manager, additional_repos_list, repo_path
            )
            cloned_repos.update(additional_repos_cloned)

        custom_prompt = (body.raw_prompt or "").strip()

        server_url = _build_internal_server_url()

        # Analyze each unique failure group in parallel
        coroutines: list[Coroutine[Any, Any, Any]] = [
            analyze_failure_group(
                failures=group_failures,
                console_context="",
                repo_path=repo_path,
                ai_provider=ai_provider,
                ai_model=ai_model,
                ai_cli_timeout=merged.ai_cli_timeout,
                custom_prompt=custom_prompt,
                server_url=server_url,
                job_id=job_id,
                peer_ai_configs=peer_ai_configs,
                peer_analysis_max_rounds=merged.peer_analysis_max_rounds,
                additional_repos=cloned_repos or None,
            )
            for group_failures in groups.values()
        ]

        results = await run_parallel_with_limit(coroutines)

        # Flatten results and filter out exceptions
        all_analyses = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(
                    f"Failed to analyze failure group: {result}", exc_info=result
                )
            else:
                all_analyses.extend(result)

        unique_errors = len(groups)
        summary = f"Analyzed {len(test_failures)} test failures ({unique_errors} unique errors). {len(all_analyses)} analyzed successfully."

        # Enrich PRODUCT BUG failures with Jira matches
        if _resolve_enable_jira(body, merged):
            await enrich_with_jira_matches(all_analyses, merged, ai_provider, ai_model)

        # If raw_xml was provided, produce enriched XML
        enriched_xml = None
        if raw_xml is not None:
            enriched_xml = build_enriched_xml(
                raw_xml, all_analyses, f"{base_url}/results/{job_id}"
            )

        analysis_result = FailureAnalysisResult(
            job_id=job_id,
            status="completed",
            summary=summary,
            ai_provider=ai_provider,
            ai_model=ai_model,
            failures=all_analyses,
            enriched_xml=enriched_xml,
        )

        result_data = analysis_result.model_dump(mode="json")
        await _preserve_request_params(job_id, result_data)
        await update_status(job_id, "completed", result_data)

        # Populate failure history
        try:
            await populate_failure_history(job_id, result_data)
        except Exception:
            logger.warning(
                "Failed to populate failure_history for job_id=%s",
                job_id,
                exc_info=True,
            )

        # Reveal classifications created during analysis
        await storage.make_classifications_visible(job_id)

        return JSONResponse(content=_attach_result_links(result_data, base_url, job_id))

    except Exception as e:
        logger.exception(f"Direct failure analysis failed for job {job_id}")
        error_detail = format_exception_with_type(e)
        analysis_result = FailureAnalysisResult(
            job_id=job_id,
            status="failed",
            summary=f"Analysis failed: {error_detail}",
            ai_provider=ai_provider,
            ai_model=ai_model,
        )
        fail_data = analysis_result.model_dump(mode="json")
        await _preserve_request_params(job_id, fail_data)
        await update_status(job_id, "failed", fail_data)
        return JSONResponse(content=_attach_result_links(fail_data, base_url, job_id))

    finally:
        repo_manager.cleanup()


@app.get("/results/{job_id}", response_model=None)
async def get_job_result(request: Request, job_id: str, response: Response):
    """Retrieve stored result by job_id, or serve SPA for browser requests."""
    # Content negotiation: browsers requesting HTML get the SPA
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        result = await get_result(job_id)
        if result and result.get("status") in IN_PROGRESS_STATUSES:
            return RedirectResponse(url=f"/status/{job_id}", status_code=302)
        return _serve_spa()

    logger.debug(f"GET /results/{job_id}")
    result = await get_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")
    _attach_result_links(result, _extract_base_url(), job_id)
    settings = get_settings()
    result["capabilities"] = {
        "github_issues": settings.github_issues_enabled,
        "jira_bugs": settings.jira_enabled,
    }
    if result.get("status") in IN_PROGRESS_STATUSES:
        response.status_code = 202
    return result


def _find_test_in_children(
    children: list[dict],
    test_name: str,
    child_job_name: str,
    child_build_number: int = 0,
) -> bool:
    """Recursively search child job analyses for a test."""
    for child in children:
        if child.get("job_name") == child_job_name and (
            child_build_number == 0 or child.get("build_number") == child_build_number
        ):
            for f in child.get("failures", []):
                if f.get("test_name") == test_name:
                    return True
        if _find_test_in_children(
            child.get("failed_children", []),
            test_name,
            child_job_name,
            child_build_number,
        ):
            return True
    return False


async def _validate_test_name_in_result(
    job_id: str, test_name: str, child_job_name: str = "", child_build_number: int = 0
) -> None:
    """Validate that a test_name exists in the stored result.

    Also checks job status before looking for the test -- if the job is still
    pending/running or has failed, the caller gets a clear status-based error
    instead of a misleading "Test not found".
    """
    logger.debug(
        f"_validate_test_name_in_result: job_id={job_id}, test_name={test_name}, child_job_name={child_job_name}"
    )
    stored = await storage.get_result(job_id)
    if not stored:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    status = stored.get("status", "unknown")
    if status in IN_PROGRESS_STATUSES:
        raise HTTPException(status_code=202, detail=f"Job {job_id} is still pending")
    if status == "failed":
        raise HTTPException(status_code=409, detail=f"Job {job_id} failed")

    result_data = stored.get("result") or {}

    if child_job_name:
        if _find_test_in_children(
            result_data.get("child_job_analyses", []),
            test_name,
            child_job_name,
            child_build_number,
        ):
            return
        raise HTTPException(
            status_code=400,
            detail=f"Test '{test_name}' not found in child job '{child_job_name}' of job {job_id}",
        )
    else:
        for f in result_data.get("failures", []):
            if f.get("test_name") == test_name:
                return
        raise HTTPException(
            status_code=400, detail=f"Test '{test_name}' not found in job {job_id}"
        )


def _find_failure_in_children(
    children: list[dict],
    test_name: str,
    child_job_name: str,
    child_build_number: int = 0,
) -> dict | None:
    """Recursively find a failure dict in child job analyses."""
    for child in children:
        if child.get("job_name") == child_job_name and (
            child_build_number == 0 or child.get("build_number") == child_build_number
        ):
            for f in child.get("failures", []):
                if f.get("test_name") == test_name:
                    return f
        result = _find_failure_in_children(
            child.get("failed_children", []),
            test_name,
            child_job_name,
            child_build_number,
        )
        if result is not None:
            return result
    return None


def _find_failure_in_result(
    result_data: dict,
    test_name: str,
    child_job_name: str = "",
    child_build_number: int = 0,
) -> dict | None:
    """Find a specific failure dict in the stored result data."""
    if child_job_name:
        return _find_failure_in_children(
            result_data.get("child_job_analyses", []),
            test_name,
            child_job_name,
            child_build_number,
        )
    for f in result_data.get("failures", []):
        if f.get("test_name") == test_name:
            return f
    return None


async def _get_error_signature(
    job_id: str,
    test_name: str,
    child_job_name: str = "",
    child_build_number: int = 0,
) -> str:
    """Look up the error_signature for a test from stored result data."""
    stored = await storage.get_result(job_id)
    if not stored or not stored.get("result"):
        return ""
    failure = _find_failure_in_result(
        stored["result"],
        test_name,
        child_job_name,
        child_build_number,
    )
    return failure.get("error_signature", "") if failure else ""


async def _resolve_effective_failure(
    job_id: str,
    failure: FailureAnalysis,
    child_job_name: str = "",
    child_build_number: int = 0,
) -> FailureAnalysis:
    """Resolve the effective classification and return an updated failure.

    Checks test_classifications for overrides. If an override exists,
    updates the failure's classification and clears stale subtype data.
    Falls back to the original classification if no override found.
    """
    effective_cls = await get_effective_classification(
        job_id, failure.test_name, child_job_name, child_build_number
    )
    if not effective_cls or effective_cls == failure.analysis.classification:
        return failure
    updates: dict = {"classification": effective_cls}
    if effective_cls == "CODE ISSUE":
        updates["product_bug_report"] = False
    elif effective_cls == "PRODUCT BUG":
        updates["code_fix"] = False
    return failure.model_copy(
        update={"analysis": failure.analysis.model_copy(update=updates)}
    )


@app.get("/results/{job_id}/comments")
async def get_comments(job_id: str) -> dict:
    """Get all comments and review states for a job."""
    logger.debug(f"GET /results/{job_id}/comments")
    comments = await storage.get_comments_for_job(job_id)
    reviews = await storage.get_reviews_for_job(job_id)
    return {"comments": comments, "reviews": reviews}


@app.post("/results/{job_id}/comments", status_code=201)
async def add_comment(job_id: str, body: AddCommentRequest, request: Request) -> dict:
    """Add a comment to a test failure."""
    logger.debug(f"POST /results/{job_id}/comments: test_name={body.test_name}")
    await _validate_test_name_in_result(
        job_id, body.test_name, body.child_job_name, body.child_build_number
    )

    error_signature = await _get_error_signature(
        job_id, body.test_name, body.child_job_name, body.child_build_number
    )

    username = request.cookies.get("jji_username", "")
    try:
        comment_id = await storage.add_comment(
            job_id=job_id,
            test_name=body.test_name,
            comment=body.comment,
            child_job_name=body.child_job_name,
            child_build_number=body.child_build_number,
            error_signature=error_signature,
            username=username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": comment_id}


@app.delete("/results/{job_id}/comments/{comment_id}")
async def delete_comment_endpoint(
    job_id: str, comment_id: int, request: Request
) -> dict:
    """Delete a comment. Username check is a UI courtesy, not security.

    This project has no authentication — all users are trusted.
    See issue #55 for future auth plans.
    """
    logger.debug(f"DELETE /results/{job_id}/comments/{comment_id}")
    username = request.cookies.get("jji_username", "")
    if not username:
        raise HTTPException(status_code=401, detail="Username required")

    deleted = await storage.delete_comment(comment_id, username, job_id=job_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail="Comment not found or not owned by you"
        )

    return {"status": "deleted"}


@app.put("/results/{job_id}/reviewed")
async def set_reviewed(job_id: str, body: SetReviewedRequest, request: Request) -> dict:
    """Toggle the reviewed state for a test failure."""
    logger.debug(
        f"PUT /results/{job_id}/reviewed: test_name={body.test_name}, reviewed={body.reviewed}"
    )
    await _validate_test_name_in_result(
        job_id, body.test_name, body.child_job_name, body.child_build_number
    )
    username = request.cookies.get("jji_username", "")
    try:
        await storage.set_reviewed(
            job_id=job_id,
            test_name=body.test_name,
            reviewed=body.reviewed,
            child_job_name=body.child_job_name,
            child_build_number=body.child_build_number,
            username=username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "reviewed_by": username if body.reviewed else "",
    }


@app.post("/results/{job_id}/enrich-comments")
async def enrich_comments(
    job_id: str, settings: Settings = Depends(get_settings)
) -> dict:
    """Fetch live statuses for GitHub PRs and Jira tickets found in comments."""
    logger.debug(f"POST /results/{job_id}/enrich-comments")
    from jenkins_job_insight.comment_enrichment import (
        detect_github_issues,
        detect_github_prs,
        detect_jira_keys,
        fetch_github_issue_status,
        fetch_github_pr_status,
        fetch_jira_ticket_status,
    )

    comments = await storage.get_comments_for_job(job_id)
    logger.debug(f"enrich_comments: job_id={job_id}, comments_count={len(comments)}")

    # Detect Cloud vs Server/DC auth once, matching JiraClient logic:
    # - Cloud: jira_email is set -> Basic auth with email:token
    # - Server/DC: no email -> Bearer PAT
    # Token resolution: prefer jira_api_token, fall back to jira_pat
    auth: tuple[str, str] | None = None
    auth_headers: dict[str, str] = {}
    jira_url: str | None = settings.jira_url if settings.jira_enabled else None
    jira_active = bool(jira_url)

    if jira_active and jira_url:
        jira_token = ""
        if settings.jira_api_token:
            jira_token = settings.jira_api_token.get_secret_value()
        elif settings.jira_pat:
            jira_token = settings.jira_pat.get_secret_value()

        if settings.jira_email and jira_token:
            # Cloud: Basic auth
            auth = (settings.jira_email, jira_token)
        elif jira_token:
            # Server/DC: Bearer
            auth_headers["Authorization"] = f"Bearer {jira_token}"

    github_token = (
        settings.github_token.get_secret_value() if settings.github_token else None
    )

    # Collect all enrichment tasks for parallel execution
    tasks: list[Coroutine[Any, Any, Any]] = []
    task_map: dict[int, tuple[str, dict]] = {}

    for c in comments:
        for pr in detect_github_prs(c["comment"]):
            idx = len(tasks)
            tasks.append(
                fetch_github_pr_status(
                    pr["owner"],
                    pr["repo"],
                    pr["number"],
                    token=github_token,
                )
            )
            task_map[idx] = (
                str(c["id"]),
                {
                    "type": "github_pr",
                    "key": f"{pr['owner']}/{pr['repo']}#{pr['number']}",
                },
            )

        for issue in detect_github_issues(c["comment"]):
            idx = len(tasks)
            tasks.append(
                fetch_github_issue_status(
                    issue["owner"],
                    issue["repo"],
                    issue["number"],
                    token=github_token,
                )
            )
            task_map[idx] = (
                str(c["id"]),
                {
                    "type": "github_issue",
                    "key": f"{issue['owner']}/{issue['repo']}#{issue['number']}",
                },
            )

        if jira_active and jira_url:
            for key in detect_jira_keys(c["comment"]):
                idx = len(tasks)
                tasks.append(
                    fetch_jira_ticket_status(
                        jira_url,
                        key,
                        auth_headers,
                        ssl_verify=settings.jira_ssl_verify,
                        auth=auth,
                    )
                )
                task_map[idx] = (str(c["id"]), {"type": "jira", "key": key})

    enrichments: dict[str, list[dict]] = {}
    logger.debug(f"enrich_comments: job_id={job_id}, enrichment_tasks={len(tasks)}")

    if tasks:
        results = await run_parallel_with_limit(tasks)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.debug("Enrichment task %d failed: %s", i, result)
                continue
            if result is None:
                continue
            comment_id, info = task_map[i]
            info["status"] = result
            enrichments.setdefault(comment_id, []).append(info)

    logger.debug(
        f"enrich_comments: job_id={job_id}, enrichments_count={len(enrichments)}"
    )
    return {"enrichments": enrichments}


async def _load_effective_failure(
    job_id: str,
    test_name: str,
    child_job_name: str,
    child_build_number: int,
) -> tuple[FailureAnalysis, dict]:
    """Shared lookup for preview/create endpoints: validate, load, and resolve a failure.

    Returns:
        Tuple of (resolved FailureAnalysis, result_data dict).

    Raises:
        HTTPException: 404 if the job is not found, 400 if the test is not found.
    """
    await _validate_test_name_in_result(
        job_id, test_name, child_job_name, child_build_number
    )
    stored = await storage.get_result(job_id)
    if not stored or not stored.get("result"):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    result_data = stored["result"]
    failure_dict = _find_failure_in_result(
        result_data, test_name, child_job_name, child_build_number
    )
    if not failure_dict:
        raise HTTPException(
            status_code=400,
            detail=f"Test '{test_name}' not found in job {job_id}",
        )
    failure = FailureAnalysis.model_validate(failure_dict)
    failure = await _resolve_effective_failure(
        job_id, failure, child_job_name, child_build_number
    )
    return failure, result_data


# NOTE: Preview/create bug endpoints intentionally bypass _merge_settings().
# These are server-level operations (GITHUB_TOKEN, TESTS_REPO_URL, Jira config)
# that act on behalf of the server, not per-request analysis overrides. The
# credentials and repo targets are fixed at deployment, not caller-supplied.
@app.post("/results/{job_id}/preview-github-issue")
async def preview_github_issue(
    job_id: str,
    body: PreviewIssueRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Generate preview content for a GitHub issue from a failure analysis."""
    logger.debug(
        f"POST /results/{job_id}/preview-github-issue: test_name={body.test_name}"
    )
    if not settings.github_issues_enabled:
        raise HTTPException(
            status_code=403,
            detail="GitHub issue creation is disabled on this server",
        )
    failure, result_data = await _load_effective_failure(
        job_id, body.test_name, body.child_job_name, body.child_build_number
    )

    # AI config is best-effort for preview — fallback content is generated if not configured
    ai_provider = body.ai_provider or AI_PROVIDER
    ai_model = body.ai_model or AI_MODEL
    base_url = _extract_base_url()
    effective_include_links = body.include_links and bool(base_url)
    report_url, jenkins_url = _build_report_context(
        include_links=effective_include_links,
        base_url=base_url,
        job_id=job_id,
        result_data=result_data,
    )

    content = await generate_github_issue_content(
        failure=failure,
        report_url=report_url,
        ai_provider=ai_provider,
        ai_model=ai_model,
        jenkins_url=jenkins_url,
        include_links=effective_include_links,
    )

    # Duplicate detection (best-effort: failures must not break preview)
    tests_repo_url = str(settings.tests_repo_url or "")
    github_token = (
        settings.github_token.get_secret_value() if settings.github_token else ""
    )
    similar: list[dict] = []
    if tests_repo_url and github_token:
        try:
            similar = await search_github_duplicates(
                title=content["title"],
                repo_url=tests_repo_url,
                github_token=github_token,
            )
        except Exception:
            logger.warning(
                "GitHub duplicate search failed for job_id=%s",
                job_id,
                exc_info=True,
            )

    return {
        "title": content["title"],
        "body": content["body"],
        "similar_issues": similar,
    }


@app.post("/results/{job_id}/preview-jira-bug")
async def preview_jira_bug(
    job_id: str,
    body: PreviewIssueRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Generate preview content for a Jira bug from a failure analysis."""
    logger.debug(f"POST /results/{job_id}/preview-jira-bug: test_name={body.test_name}")
    if not settings.jira_enabled:
        raise HTTPException(
            status_code=403,
            detail="Jira integration is disabled on this server",
        )
    failure, result_data = await _load_effective_failure(
        job_id, body.test_name, body.child_job_name, body.child_build_number
    )

    # AI config is best-effort for preview — fallback content is generated if not configured
    ai_provider = body.ai_provider or AI_PROVIDER
    ai_model = body.ai_model or AI_MODEL
    base_url = _extract_base_url()
    effective_include_links = body.include_links and bool(base_url)
    report_url, jenkins_url = _build_report_context(
        include_links=effective_include_links,
        base_url=base_url,
        job_id=job_id,
        result_data=result_data,
    )

    content = await generate_jira_bug_content(
        failure=failure,
        report_url=report_url,
        ai_provider=ai_provider,
        ai_model=ai_model,
        jenkins_url=jenkins_url,
        include_links=effective_include_links,
    )

    # Duplicate detection (best-effort: failures must not break preview)
    similar: list[dict] = []
    if settings.jira_enabled:
        try:
            similar = await search_jira_duplicates(
                title=content["title"],
                settings=settings,
            )
        except Exception:
            logger.warning(
                "Jira duplicate search failed for job_id=%s",
                job_id,
                exc_info=True,
            )

    return {
        "title": content["title"],
        "body": content["body"],
        "similar_issues": similar,
    }


def _require_tracker_url(result: dict, tracker_name: str) -> str:
    """Extract and validate the issue URL from a tracker API response.

    Raises:
        HTTPException: 502 when the response does not contain a ``url`` field.
    """
    issue_url = str(result.get("url", ""))
    if not issue_url:
        raise HTTPException(
            status_code=502,
            detail=f"{tracker_name} API returned unexpected response: missing url",
        )
    return issue_url


async def _add_tracker_comment(
    tracker_label: str,
    job_id: str,
    body: CreateIssueRequest,
    result: dict,
    username: str,
) -> int:
    """Best-effort auto-add a comment linking to the created tracker issue.

    Args:
        tracker_label: Human-readable tracker name (e.g. "GitHub Issue", "Jira Bug").
        job_id: Analysis job ID.
        body: The create-issue request (carries test_name, child_job_name, etc.).
        result: The tracker API response (must contain ``url`` and optionally ``key``).
        username: Username from the request cookie.

    Returns:
        The comment ID on success, or ``0`` on failure.
    """
    comment_id = 0
    issue_url = str(result.get("url", ""))
    try:
        if not issue_url:
            raise ValueError("Tracker response missing url")
        key = result.get("key", "")
        key_suffix = f" [{key}]" if key else ""
        comment_text = f"{tracker_label}{key_suffix}: [{body.title}]({issue_url})"
        error_signature = await _get_error_signature(
            job_id, body.test_name, body.child_job_name, body.child_build_number
        )
        comment_id = await storage.add_comment(
            job_id=job_id,
            test_name=body.test_name,
            comment=comment_text,
            child_job_name=body.child_job_name,
            child_build_number=body.child_build_number,
            error_signature=error_signature,
            username=username,
        )
    except Exception:
        logger.warning(
            f"Failed to add comment after {tracker_label} creation "
            f"for job_id={job_id}, issue url={issue_url or '<missing>'}",
            exc_info=True,
        )
    return comment_id


@app.post("/results/{job_id}/create-github-issue", status_code=201)
async def create_github_issue_endpoint(
    job_id: str,
    body: CreateIssueRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Create a GitHub issue from a failure analysis."""
    logger.debug(
        f"POST /results/{job_id}/create-github-issue: test_name={body.test_name}"
    )
    if not settings.github_issues_enabled:
        raise HTTPException(
            status_code=403,
            detail="GitHub issue creation is disabled on this server",
        )
    tests_repo_url = str(settings.tests_repo_url or "")
    github_token = (
        settings.github_token.get_secret_value() if settings.github_token else ""
    )

    if not tests_repo_url or not github_token:
        raise HTTPException(
            status_code=400,
            detail="TESTS_REPO_URL and GITHUB_TOKEN must be configured to create GitHub issues",
        )

    # Verify classification matches tracker
    failure, _result_data = await _load_effective_failure(
        job_id, body.test_name, body.child_job_name, body.child_build_number
    )
    if failure.analysis.classification == "PRODUCT BUG":
        raise HTTPException(
            status_code=422,
            detail="Cannot create GitHub issue for a PRODUCT BUG classification. Use Jira instead.",
        )

    username = request.cookies.get("jji_username", "")
    issue_body = body.body
    if username:
        issue_body += f"\n\n---\n_Reported by: {username} via jenkins-job-insight_"

    try:
        result = await create_github_issue(
            title=body.title,
            body=issue_body,
            repo_url=tests_repo_url,
            github_token=github_token,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid TESTS_REPO_URL: {exc}",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub API error: {exc.response.status_code}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub API unreachable: {exc}",
        ) from exc
    except (TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub API returned unexpected response: {exc}",
        ) from exc

    issue_url = _require_tracker_url(result, "GitHub")

    comment_id = await _add_tracker_comment(
        "GitHub Issue", job_id, body, result, username
    )

    return {
        "url": issue_url,
        "number": result.get("number", 0),
        "key": "",
        "title": body.title,
        "comment_id": comment_id,
    }


@app.post("/results/{job_id}/create-jira-bug", status_code=201)
async def create_jira_bug_endpoint(
    job_id: str,
    body: CreateIssueRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Create a Jira bug from a failure analysis."""
    logger.debug(f"POST /results/{job_id}/create-jira-bug: test_name={body.test_name}")

    if not settings.jira_enabled:
        raise HTTPException(
            status_code=403,
            detail="Jira integration is disabled on this server",
        )

    # Verify classification matches tracker
    failure, _result_data = await _load_effective_failure(
        job_id, body.test_name, body.child_job_name, body.child_build_number
    )
    if failure.analysis.classification == "CODE ISSUE":
        raise HTTPException(
            status_code=422,
            detail="Cannot create Jira bug for a CODE ISSUE classification. Use GitHub instead.",
        )

    username = request.cookies.get("jji_username", "")
    bug_body = body.body
    if username:
        bug_body += f"\n\n----\nReported by: {username} via jenkins-job-insight"

    try:
        result = await create_jira_bug(
            title=body.title,
            body=bug_body,
            settings=settings,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Jira API error: {exc.response.status_code}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Jira API unreachable: {exc}",
        ) from exc
    except (TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Jira API returned unexpected response: {exc}",
        ) from exc

    issue_url = _require_tracker_url(result, "Jira")

    comment_id = await _add_tracker_comment("Jira Bug", job_id, body, result, username)

    return {
        "url": issue_url,
        "key": result.get("key", ""),
        "title": body.title,
        "comment_id": comment_id,
    }


def _patch_failure_classification(
    failures: list[dict], test_name: str, classification: str
) -> None:
    """Patch classification for matching failures in a list.

    Also clears stale subtype fields: when switching to CODE ISSUE the
    old product_bug_report is removed, and vice-versa.
    """
    for f in failures:
        if f.get("test_name") == test_name:
            analysis = f.get("analysis", {})
            if isinstance(analysis, dict):
                analysis["classification"] = classification
                if classification == "CODE ISSUE":
                    analysis.pop("product_bug_report", None)
                elif classification == "PRODUCT BUG":
                    analysis.pop("code_fix", None)


def _apply_classification_override(
    result_data: dict,
    test_name: str,
    classification: str,
    child_job_name: str,
    child_build_number: int,
) -> None:
    """Mutate result_data to apply a classification override to matching failures."""
    if child_job_name:
        # Override in child job failures
        for child in result_data.get("child_job_analyses", []):
            if child.get("job_name") == child_job_name and (
                child_build_number == 0
                or child.get("build_number") == child_build_number
            ):
                _patch_failure_classification(
                    child.get("failures", []), test_name, classification
                )
            # Also check nested failed_children recursively
            _patch_children(
                child.get("failed_children", []),
                test_name,
                classification,
                child_job_name,
                child_build_number,
            )
    else:
        # Override in top-level failures
        _patch_failure_classification(
            result_data.get("failures", []), test_name, classification
        )


def _patch_children(
    children: list[dict],
    test_name: str,
    classification: str,
    child_job_name: str,
    child_build_number: int,
) -> None:
    """Recursively patch classification in nested children."""
    for child in children:
        if child.get("job_name") == child_job_name and (
            child_build_number == 0 or child.get("build_number") == child_build_number
        ):
            _patch_failure_classification(
                child.get("failures", []), test_name, classification
            )
        _patch_children(
            child.get("failed_children", []),
            test_name,
            classification,
            child_job_name,
            child_build_number,
        )


@app.put("/results/{job_id}/override-classification")
async def override_classification_endpoint(
    job_id: str,
    body: OverrideClassificationRequest,
    request: Request,
) -> dict:
    """Override the classification of a failure (CODE ISSUE / PRODUCT BUG)."""
    logger.debug(
        f"PUT /results/{job_id}/override-classification: test_name={body.test_name}, "
        f"classification={body.classification}"
    )
    await _validate_test_name_in_result(
        job_id, body.test_name, body.child_job_name, body.child_build_number
    )
    username = request.cookies.get("jji_username", "")

    # Look up parent_job_name for the test_classifications entry
    parent_job_name = await storage.get_parent_job_name_for_test(
        body.test_name, job_id=job_id
    )

    group_tests = await storage.override_classification(
        job_id=job_id,
        test_name=body.test_name,
        classification=body.classification,
        child_job_name=body.child_job_name,
        child_build_number=body.child_build_number,
        username=username,
        parent_job_name=parent_job_name,
    )

    # Persist the override into result_json so page refresh reflects it.
    # Uses an atomic read-modify-write inside a single SQLite transaction
    # so concurrent overrides by different reviewers cannot clobber each other.
    # Wrapped in try/except: the authoritative override is already committed
    # above; a failure here should not turn the response into a 500.
    # Patch ALL tests in the signature group so grouped siblings also update.
    def _patch_group(rd: dict) -> None:
        for t in group_tests:
            _apply_classification_override(
                rd,
                t,
                body.classification,
                body.child_job_name,
                body.child_build_number,
            )

    try:
        await patch_result_json(job_id, _patch_group)
    except Exception:
        logger.warning(
            f"Failed to patch stored result_json after override for job_id={job_id}",
            exc_info=True,
        )

    return {"status": "ok", "classification": body.classification}


@app.get("/results/{job_id}/review-status")
async def get_review_status(job_id: str) -> dict:
    """Get review summary for a job (used by dashboard)."""
    logger.debug(f"GET /results/{job_id}/review-status")
    return await storage.get_review_status(job_id)


@app.get("/results")
async def list_job_results(limit: int = Query(50, le=100)) -> list[dict]:
    """List recent analysis jobs."""
    logger.debug(f"GET /results: limit={limit}")
    return await list_results(limit)


@app.delete("/results/{job_id}")
async def delete_job_endpoint(job_id: str, request: Request) -> dict:
    """Delete an analyzed job and all related data.

    This project operates on a trusted network with no authentication.
    All users can perform all actions. The username cookie check below
    is a UI convenience (prevents accidental deletions from scripts),
    not a security boundary. See issue #55 for future auth plans.
    """
    # Basic sanity check — not security. All users are trusted.
    # This just ensures a human with a registered name is making the request.
    username = request.cookies.get("jji_username", "")
    if not username:
        raise HTTPException(
            status_code=401,
            detail="Please register a username first",
        )

    result = await storage.get_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    await storage.delete_job(job_id)
    logger.info(f"Deleted job {job_id} by user {username}")
    return {"status": "deleted", "job_id": job_id}


@app.get("/api/dashboard")
async def api_dashboard() -> list[dict]:
    """Return dashboard job list as JSON for the React frontend."""
    return await list_results_for_dashboard()


@app.get("/api/capabilities")
async def get_capabilities(settings: Settings = Depends(get_settings)) -> dict:
    """Report which post-analysis automation features are available.

    These capabilities indicate whether the server is configured to
    automatically create GitHub issues or Jira bugs from analysis results.
    They require server-level credentials (GITHUB_TOKEN, JIRA_API_TOKEN, etc.)
    and cannot be provided per-request.
    """
    return {
        "github_issues": settings.github_issues_enabled,
        "jira_bugs": settings.jira_enabled,
    }


@app.get("/history/failures")
async def get_all_failures_endpoint(
    search: str = Query(default=""),
    job_name: str = Query(default=""),
    classification: str = Query(default=""),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Get paginated failure history."""
    logger.debug(
        f"GET /history/failures: search={search!r}, job_name={job_name!r}, classification={classification!r}, limit={limit}, offset={offset}"
    )
    return await storage.get_all_failures(
        search=search,
        job_name=job_name,
        classification=classification,
        limit=limit,
        offset=offset,
    )


@app.get("/history/test/{test_name:path}")
async def get_test_history_endpoint(
    test_name: str,
    limit: int = Query(default=20, le=100),
    job_name: str = Query(default=""),
    exclude_job_id: str = Query(
        default="", description="Exclude results from this job ID"
    ),
) -> dict:
    """Get pass/fail history for a specific test."""
    logger.debug(f"GET /history/test/{test_name}: limit={limit}, job_name={job_name!r}")
    return await storage.get_test_history(
        test_name, limit=limit, job_name=job_name, exclude_job_id=exclude_job_id
    )


@app.get("/history/search")
async def search_by_signature_endpoint(
    signature: str = Query(...),
    exclude_job_id: str = Query(
        default="", description="Exclude results from this job ID"
    ),
) -> dict:
    """Find all tests that failed with the same error signature."""
    logger.debug(f"GET /history/search: signature={signature}")
    return await storage.search_by_signature(signature, exclude_job_id=exclude_job_id)


@app.get("/history/stats/{job_name:path}")
async def get_job_stats_endpoint(
    job_name: str,
    exclude_job_id: str = Query(
        default="", description="Exclude results from this job ID"
    ),
) -> dict:
    """Get aggregate statistics for a specific job."""
    logger.debug(f"GET /history/stats/{job_name}")
    return await storage.get_job_stats(job_name, exclude_job_id=exclude_job_id)


@app.post("/history/classify", status_code=201)
async def classify_test(request: Request, body: ClassifyTestRequest) -> dict:
    """Classify a test as FLAKY, REGRESSION, etc. Used by AI and humans."""
    logger.debug(
        f"POST /history/classify: test_name={body.test_name!r}, classification={body.classification!r}"
    )
    test_name = body.test_name.strip()
    classification = body.classification
    reason = body.reason
    job_name = body.job_name
    references = body.references
    classify_job_id = body.job_id

    if not test_name:
        raise HTTPException(status_code=400, detail="test_name is required")

    if classification == "KNOWN_BUG" and not str(references).strip():
        raise HTTPException(
            status_code=400,
            detail="KNOWN_BUG requires non-empty references (e.g., Jira tickets or historical bug URLs).",
        )

    created_by = request.cookies.get("jji_username", "ai")

    # Human classifications are visible immediately.
    # AI classifications become visible after analysis completes
    # and calls make_classifications_visible().
    visible = 0 if created_by == "ai" else 1

    # Look up parent job name from failure_history, scoped to this job
    parent_job_name = await storage.get_parent_job_name_for_test(
        test_name, job_id=classify_job_id
    )
    if not parent_job_name and classify_job_id:
        # Job might not be in failure_history yet (analysis in progress)
        result = await storage.get_result(classify_job_id)
        if result and result.get("result"):
            parent_job_name = result["result"].get("job_name", "")

    try:
        classification_id = await storage.set_test_classification(
            test_name=test_name,
            classification=classification,
            reason=reason,
            job_name=job_name,
            parent_job_name=parent_job_name,
            created_by=created_by,
            references=references,
            job_id=classify_job_id,
            child_build_number=body.child_build_number,
            visible=visible,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": classification_id}


@app.get("/history/classifications")
async def get_classifications(
    test_name: str = Query(default=""),
    classification: str = Query(default=""),
    job_name: str = Query(default=""),
    parent_job_name: str = Query(default=""),
    job_id: str = Query(default=""),
) -> dict:
    """Get test classifications."""
    logger.debug(
        f"GET /history/classifications: test_name={test_name!r}, classification={classification!r}, "
        f"job_name={job_name!r}, parent_job_name={parent_job_name!r}, job_id={job_id!r}"
    )
    classifications = await storage.get_test_classifications(
        test_name=test_name,
        classification=classification,
        job_name=job_name,
        parent_job_name=parent_job_name,
        job_id=job_id,
    )
    return {"classifications": classifications}


@app.get("/ai-configs")
async def get_ai_configs_endpoint() -> list[dict]:
    """Get distinct AI provider/model pairs from completed analyses."""
    logger.debug("GET /ai-configs")
    return await get_ai_configs()


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Serve the application favicon as an SVG image."""
    return Response(
        content=FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _serve_spa() -> HTMLResponse:
    """Read and serve the React SPA index.html."""
    index_file = _FRONTEND_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="Frontend not built")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


# SPA catch-all routes — must be AFTER all API routes
@app.get("/register", include_in_schema=False)
async def serve_spa_known_routes() -> HTMLResponse:
    """Serve the React SPA for known frontend routes."""
    return _serve_spa()


@app.get("/{path:path}", include_in_schema=False)
async def serve_frontend_catchall(request: Request, path: str) -> HTMLResponse:
    """Catch-all: serve the React SPA for any unmatched route."""
    if path == "api" or path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    accept = request.headers.get("accept", "")
    if "text/html" not in accept or "application/json" in accept:
        raise HTTPException(status_code=404, detail="Not found")
    return _serve_spa()


def run() -> None:
    """Entry point for the CLI."""
    import uvicorn

    reload = os.getenv("DEBUG", "").lower() == "true"
    uvicorn.run(
        "jenkins_job_insight.main:app", host="0.0.0.0", port=APP_PORT, reload=reload
    )
