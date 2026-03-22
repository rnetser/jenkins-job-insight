import asyncio
import json
import os
import re
import urllib.parse
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from xml.etree.ElementTree import ParseError

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import SecretStr
from simple_logger.logger import get_logger

from ai_cli_runner import VALID_AI_PROVIDERS, run_parallel_with_limit
from jenkins_job_insight.analyzer import (
    analyze_failure_group,
    analyze_job,
    get_failure_signature,
)
from jenkins_job_insight.config import Settings, get_settings
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
    AnalysisResult,
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
from jenkins_job_insight.html_report import (
    FAVICON_SVG,
    format_result_as_html,
    format_status_page,
    generate_dashboard_html,
    generate_history_html,
    generate_register_html,
)
from jenkins_job_insight.output import send_callback
from jenkins_job_insight.repository import RepositoryManager
from jenkins_job_insight import storage
from jenkins_job_insight.storage import (
    get_ai_configs,
    get_effective_classification,
    get_html_report,
    get_result,
    init_db,
    list_results,
    list_results_for_dashboard,
    populate_failure_history,
    save_html_report,
    save_result,
    update_status,
)

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))

_HOST_RE = re.compile(
    r"^([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"  # first label
    r"(\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"  # additional labels
    r"(:\d+)?$"  # optional port
)

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


async def deliver_results(
    result: AnalysisResult,
    body: AnalyzeRequest,
    settings: Settings,
) -> None:
    """Deliver analysis results to callback webhook.

    Uses request values if provided, otherwise falls back to settings.

    Args:
        result: The analysis result to deliver.
        body: The original analyze request containing optional callback URL.
        settings: Application settings with default callback URL.
    """
    logger.debug(f"deliver_results: job_id={result.job_id}, status={result.status}")
    callback_url = body.callback_url or settings.callback_url
    callback_headers = body.callback_headers or settings.callback_headers
    if callback_url:
        try:
            await send_callback(str(callback_url), result, callback_headers)
        except Exception:
            logger.exception("Failed to send callback to %s", callback_url)


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


def _extract_base_url(request: Request) -> str:
    """Extract the external base URL from incoming request headers.

    Priority:
        1. X-Forwarded-Proto + X-Forwarded-Host (reverse proxy scenario)
        2. Host header + request scheme (direct access)
        3. Fallback to request.url components

    Args:
        request: The incoming FastAPI/Starlette request.

    Returns:
        Base URL without trailing slash (e.g. "https://example.com").
    """
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")

    if forwarded_proto and forwarded_host:
        # Take first value from comma-separated list (multi-hop proxies)
        proto = forwarded_proto.split(",")[0].strip().lower()
        host = forwarded_host.split(",")[0].strip()
        scheme = proto if proto in ("http", "https") else "https"
        if _HOST_RE.match(host):
            base_url = f"{scheme}://{host}".rstrip("/")
            logger.debug("Base URL from X-Forwarded headers: %s", base_url)
            return base_url

    host = request.headers.get("host")
    if host and _HOST_RE.match(host):
        base_url = f"{request.url.scheme}://{host}".rstrip("/")
        logger.debug("Base URL from Host header: %s", base_url)
        return base_url

    base_url = f"{request.url.scheme}://{request.url.netloc}".rstrip("/")
    logger.debug("Base URL from request URL fallback: %s", base_url)
    return base_url


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Jenkins Job Insight",
    description="Analyzes Jenkins job failures and classifies them as code or product issues",
    version="0.1.0",
    lifespan=lifespan,
)


class UsernameMiddleware(BaseHTTPMiddleware):
    """Middleware that checks for jji_username cookie and redirects to /register if missing."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ("/register", "/health", "/favicon.ico") or path.startswith(
            "/register"
        ):
            return await call_next(request)

        username = request.cookies.get("jji_username", "")
        if not username:
            # Only redirect browser requests, not API calls
            accept = request.headers.get("accept", "")
            if "text/html" in accept:
                return RedirectResponse(url="/register", status_code=303)

        request.state.username = username
        return await call_next(request)


app.add_middleware(UsernameMiddleware)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Redirect root to dashboard."""
    return RedirectResponse(url="/dashboard")


@app.get("/register", response_class=HTMLResponse)
async def register_page() -> str:
    """Show registration page for new users."""
    return generate_register_html()


@app.post("/register")
async def register(request: Request) -> RedirectResponse:
    """Set username cookie and redirect to dashboard."""
    form = await request.form()
    username = str(form.get("username", "")).strip()
    if not username:
        return RedirectResponse(url="/register", status_code=303)
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="jji_username",
        value=username,
        max_age=365 * 24 * 60 * 60,  # 1 year
        samesite="lax",
    )
    return response


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
    if not model:
        raise HTTPException(
            status_code=400,
            detail="No AI model configured. Set AI_MODEL env var or pass ai_model in request body.",
        )
    return provider, model


def _resolve_ai_config(body: AnalyzeRequest) -> tuple[str, str]:
    """Resolve AI config from an AnalyzeRequest."""
    return _resolve_ai_config_values(body.ai_provider, body.ai_model)


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
    #   callback_url     - resolved in deliver_results()
    #   callback_headers - resolved in deliver_results()
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

    # SecretStr fields need wrapping
    if body.jira_api_token is not None:
        overrides["jira_api_token"] = SecretStr(body.jira_api_token)
    if body.jira_pat is not None:
        overrides["jira_pat"] = SecretStr(body.jira_pat)
    if body.github_token is not None:
        overrides["github_token"] = SecretStr(body.github_token)

    # AnalyzeRequest-specific fields (Jenkins overrides)
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


async def process_analysis_with_id(
    job_id: str, body: AnalyzeRequest, settings: Settings, base_url: str = ""
) -> None:
    """Background task to process analysis with a pre-generated job_id.

    Args:
        job_id: Pre-generated job ID for tracking.
        body: The analysis request.
        settings: Application settings.
        base_url: External base URL for constructing absolute result URLs.
    """
    logger.info(
        f"Analysis request received for {body.job_name} #{body.build_number} "
        f"(job_id: {job_id})"
    )
    try:
        logger.debug(
            f"process_analysis_with_id: updating status to running, job_id={job_id}"
        )
        await update_status(job_id, "running")

        ai_provider, ai_model = _resolve_ai_config(body)
        logger.debug(
            f"process_analysis_with_id: ai_provider={ai_provider}, ai_model={ai_model}"
        )

        server_url = _build_internal_server_url()

        result = await analyze_job(
            body,
            settings,
            ai_provider=ai_provider,
            ai_model=ai_model,
            job_id=job_id,
            server_url=server_url,
        )

        # Enrich PRODUCT BUG failures with Jira matches
        if _resolve_enable_jira(body, settings):
            logger.debug(
                f"process_analysis_with_id: enriching with Jira matches, job_id={job_id}"
            )
            await _enrich_result_with_jira(
                result.failures + list(result.child_job_analyses),
                settings,
                ai_provider,
                ai_model,
            )

        logger.debug(
            f"process_analysis_with_id: saving completed result, job_id={job_id}"
        )
        result_data = result.model_dump(mode="json")
        result_data["base_url"] = base_url
        result_data["result_url"] = f"{base_url}/results/{job_id}"
        result_data["html_report_url"] = f"{base_url}/results/{job_id}.html"

        # Save to storage
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
        await _invalidate_cached_html(job_id)

        await deliver_results(result, body, settings)

    except Exception as e:
        logger.exception(f"Analysis failed for job {job_id}")
        await update_status(job_id, "failed", {"error": str(e)})


@app.post("/analyze", status_code=202, response_model=None)
async def analyze(
    request: Request,
    body: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    *,
    sync: bool = Query(
        default=False, description="If true, wait for result and return it"
    ),
    settings: Settings = Depends(get_settings),
) -> dict | JSONResponse:
    """Submit a Jenkins job for analysis.

    By default (async mode), returns immediately with a job_id.
    With ?sync=true, blocks until analysis is complete and returns the full result.
    """
    logger.debug(f"Starting analysis for {body.job_name} #{body.build_number}")
    base_url = _extract_base_url(request)

    if sync:
        logger.info(
            f"Sync analysis request received for {body.job_name} #{body.build_number}"
        )

        merged = _merge_settings(body, settings)
        ai_provider, ai_model = _resolve_ai_config(body)

        server_url = _build_internal_server_url()

        result = await analyze_job(
            body,
            merged,
            ai_provider=ai_provider,
            ai_model=ai_model,
            server_url=server_url,
        )

        # Enrich PRODUCT BUG failures with Jira matches
        if _resolve_enable_jira(body, merged):
            await _enrich_result_with_jira(
                result.failures + list(result.child_job_analyses),
                merged,
                ai_provider,
                ai_model,
            )

        jenkins_url = build_jenkins_url(
            merged.jenkins_url, body.job_name, body.build_number
        )
        await save_result(
            result.job_id, jenkins_url, result.status, result.model_dump(mode="json")
        )
        logger.info(
            f"Sync analysis completed for {body.job_name} #{body.build_number} "
            f"(job_id: {result.job_id})"
        )

        # Populate failure history
        try:
            await populate_failure_history(
                result.job_id, result.model_dump(mode="json")
            )
        except Exception:
            logger.warning(
                "Failed to populate failure_history for job_id=%s",
                result.job_id,
                exc_info=True,
            )

        # Reveal classifications created during analysis
        await storage.make_classifications_visible(result.job_id)
        await _invalidate_cached_html(result.job_id)

        await deliver_results(result, body, merged)

        content = result.model_dump(mode="json")
        content["base_url"] = base_url
        content["result_url"] = f"{base_url}/results/{result.job_id}"
        content["html_report_url"] = f"{base_url}/results/{result.job_id}.html"

        return JSONResponse(content=content, status_code=200)

    # Async mode - queue background task
    # Generate job_id here so we can return it to the client for polling
    job_id = str(uuid.uuid4())
    merged = _merge_settings(body, settings)
    jenkins_url = build_jenkins_url(
        merged.jenkins_url, body.job_name, body.build_number
    )
    # Save initial pending state before queueing background task
    # Include job_name and build_number so the status page can display them
    await save_result(
        job_id,
        jenkins_url,
        "pending",
        {"job_name": body.job_name, "build_number": body.build_number},
    )
    background_tasks.add_task(process_analysis_with_id, job_id, body, merged, base_url)
    callback_url = body.callback_url or merged.callback_url
    message = "Analysis job queued."
    if callback_url:
        message += " Results will be delivered to callback."
    else:
        message += f" Poll /results/{job_id} for status."

    response: dict = {
        "status": "queued",
        "job_id": job_id,
        "message": message,
        "base_url": base_url,
        "result_url": f"{base_url}/results/{job_id}",
        "html_report_url": f"{base_url}/results/{job_id}.html",
    }

    return response


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
    base_url = _extract_base_url(request)

    if raw_xml := body.raw_xml:
        try:
            test_failures = extract_test_failures(raw_xml)
        except ParseError as e:
            raise HTTPException(status_code=400, detail=f"Invalid XML: {e}")

        if not test_failures:
            job_id = str(uuid.uuid4())
            analysis_result = FailureAnalysisResult(
                job_id=job_id,
                status="completed",
                summary="No test failures found in the provided XML.",
                enriched_xml=raw_xml,
            )
            result_data = analysis_result.model_dump(mode="json")
            result_data["base_url"] = base_url
            result_data["result_url"] = f"{base_url}/results/{job_id}"
            result_data["html_report_url"] = f"{base_url}/results/{job_id}.html"
            return JSONResponse(content=result_data)
    else:
        if not body.failures:
            raise HTTPException(status_code=400, detail="No failures provided")
        test_failures = body.failures

    merged = _merge_settings(body, settings)
    ai_provider, ai_model = _resolve_ai_config_values(body.ai_provider, body.ai_model)

    job_id = str(uuid.uuid4())
    logger.info(
        f"Direct failure analysis request received with {len(test_failures)} failures (job_id: {job_id})"
    )

    # Save initial pending state so GET /results/{job_id} works immediately
    await save_result(job_id, "", "pending", None)

    # Group failures by error signature for deduplication
    groups: dict[str, list] = defaultdict(list)
    for failure in test_failures:
        sig = get_failure_signature(failure)
        groups[sig].append(failure)

    logger.info(
        f"Grouped {len(test_failures)} failures into {len(groups)} unique error signatures"
    )

    # Optionally clone repo for AI code context
    repo_manager = RepositoryManager()
    repo_path = None
    custom_prompt = ""
    tests_repo_url = body.tests_repo_url or merged.tests_repo_url
    try:
        await update_status(job_id, "running")

        if tests_repo_url:
            repo_path = await asyncio.to_thread(repo_manager.clone, str(tests_repo_url))

        custom_prompt = (body.raw_prompt or "").strip()

        server_url = _build_internal_server_url()

        # Analyze each unique failure group in parallel
        coroutines = [
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
                raw_xml, all_analyses, f"{base_url}/results/{job_id}.html"
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
        result_data["base_url"] = base_url
        result_data["result_url"] = f"{base_url}/results/{job_id}"
        result_data["html_report_url"] = f"{base_url}/results/{job_id}.html"

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
        await _invalidate_cached_html(job_id)

        return JSONResponse(content=result_data)

    except Exception as e:
        logger.exception(f"Direct failure analysis failed for job {job_id}")
        analysis_result = FailureAnalysisResult(
            job_id=job_id,
            status="failed",
            summary=f"Analysis failed: {e}",
            ai_provider=ai_provider,
            ai_model=ai_model,
        )
        await update_status(job_id, "failed", analysis_result.model_dump(mode="json"))
        content = analysis_result.model_dump(mode="json")
        content["base_url"] = base_url
        content["result_url"] = f"{base_url}/results/{job_id}"
        return JSONResponse(content=content)

    finally:
        repo_manager.cleanup()


def _build_analysis_result(job_id: str, result_data: dict) -> AnalysisResult:
    """Reconstruct an AnalysisResult from stored result JSON.

    Handles both Jenkins analysis results (which have jenkins_url) and
    direct failure analysis results (which don't).

    Args:
        job_id: The job identifier.
        result_data: The stored result JSON dict.

    Returns:
        AnalysisResult suitable for HTML report generation.
    """
    if result_data.get("jenkins_url"):
        return AnalysisResult.model_validate(result_data)

    # Direct failure analysis — wrap in AnalysisResult
    return AnalysisResult(
        job_id=job_id,
        job_name=result_data.get("job_name", "Direct Failure Analysis"),
        build_number=result_data.get("build_number", 0),
        jenkins_url=None,
        status="completed",
        summary=result_data.get("summary", ""),
        ai_provider=result_data.get("ai_provider", ""),
        ai_model=result_data.get("ai_model", ""),
        failures=[
            FailureAnalysis.model_validate(f) for f in result_data.get("failures", [])
        ],
    )


@app.get("/results/{job_id}.html", response_class=HTMLResponse)
async def get_job_report(
    job_id: str,
    *,
    refresh: bool = Query(
        default=False, description="Force regeneration of the HTML report"
    ),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Serve an HTML report, generating it on-demand if needed.

    Reports are generated lazily from stored results and cached to disk.
    Pass ``?refresh=1`` to force regeneration (e.g. after a code update).
    """
    logger.debug(f"GET /results/{job_id}.html: refresh={refresh}")
    # NOTE: The cached HTML bakes in github_available / jira_available at render
    # time.  If integration settings change (e.g. GITHUB_TOKEN added), stale
    # button state may be served until the cache is refreshed (?refresh=1) or
    # a re-analysis invalidates it.  In practice this is rare because config
    # changes require a server restart, but callers can force regeneration via
    # the refresh query parameter.

    # Compute availability flags first so we can compare against what the cache
    # would contain.
    github_available = bool(settings.tests_repo_url and settings.github_token)
    jira_available = settings.jira_enabled

    # Try disk cache first (skip when refresh requested)
    if not refresh:
        html_content = await get_html_report(job_id)
        if html_content:
            return HTMLResponse(html_content)

    # Check if the job exists
    result = await get_result(job_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' not found.",
        )

    status = result.get("status", "unknown")
    if status in ("pending", "running"):
        return HTMLResponse(
            format_status_page(job_id, status, result),
            headers={"Refresh": "10"},
        )

    # Generate HTML on-demand from stored result data
    result_data = result.get("result")
    if result_data and status == "completed":
        analysis_result = _build_analysis_result(job_id, result_data)
        html_content = format_result_as_html(
            analysis_result,
            completed_at=result.get("created_at", ""),
            github_available=github_available,
            jira_available=jira_available,
        )
        try:
            await save_html_report(job_id, html_content)
        except OSError:
            logger.warning(
                "Failed to cache HTML report for job_id: %s", job_id, exc_info=True
            )
        logger.info(f"HTML report generated on-demand for job_id: {job_id}")
        return HTMLResponse(html_content)

    raise HTTPException(
        status_code=404,
        detail=f"No report available for job '{job_id}'.",
    )


@app.get("/results/{job_id}", response_model=None)
async def get_job_result(request: Request, job_id: str) -> dict:
    """Retrieve stored result by job_id."""
    logger.debug(f"GET /results/{job_id}")
    result = await get_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")
    base_url = _extract_base_url(request)
    result["base_url"] = base_url
    result["result_url"] = f"{base_url}/results/{job_id}"
    result_data = result.get("result")
    if isinstance(result_data, dict) and "html_report_url" in result_data:
        # Ensure it's a full URL (update legacy relative paths)
        if result_data["html_report_url"].startswith("/"):
            result_data["html_report_url"] = (
                f"{base_url}{result_data['html_report_url']}"
            )
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
    if status in ("pending", "running"):
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


async def _invalidate_cached_html(job_id: str) -> None:
    """Delete cached HTML report so next request regenerates it."""
    logger.debug(f"_invalidate_cached_html: job_id={job_id}")
    try:
        report_path = storage.REPORTS_DIR / f"{job_id}.html"
        await asyncio.to_thread(lambda: report_path.unlink(missing_ok=True))
    except OSError:
        pass  # Cache cleanup is best-effort


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
        raise HTTPException(status_code=400, detail=str(exc))
    await _invalidate_cached_html(job_id)
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

    await _invalidate_cached_html(job_id)
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
        raise HTTPException(status_code=400, detail=str(exc))
    await _invalidate_cached_html(job_id)
    return {"status": "ok"}


@app.post("/results/{job_id}/enrich-comments")
async def enrich_comments(
    job_id: str, settings: Settings = Depends(get_settings)
) -> dict:
    """Fetch live statuses for GitHub PRs and Jira tickets found in comments."""
    logger.debug(f"POST /results/{job_id}/enrich-comments")
    from jenkins_job_insight.comment_enrichment import (
        detect_github_prs,
        detect_jira_keys,
        fetch_github_pr_status,
        fetch_jira_ticket_status,
    )

    comments = await storage.get_comments_for_job(job_id)
    logger.debug(f"enrich_comments: job_id={job_id}, comments_count={len(comments)}")

    # Detect Cloud vs Server/DC auth once, matching JiraClient logic:
    # - Cloud: jira_email is set -> Basic auth with email:token
    # - Server/DC: no email -> Bearer PAT
    # Token resolution: prefer jira_api_token (backward compat), fall back to jira_pat
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
    tasks: list = []
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
    await _validate_test_name_in_result(
        job_id, body.test_name, body.child_job_name, body.child_build_number
    )
    stored = await storage.get_result(job_id)
    if not stored or not stored.get("result"):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    result_data = stored["result"]
    failure_dict = _find_failure_in_result(
        result_data, body.test_name, body.child_job_name, body.child_build_number
    )
    if not failure_dict:
        raise HTTPException(
            status_code=400,
            detail=f"Test '{body.test_name}' not found in job {job_id}",
        )
    failure = FailureAnalysis.model_validate(failure_dict)

    # Apply any classification override from test_classifications
    failure = await _resolve_effective_failure(
        job_id, failure, body.child_job_name, body.child_build_number
    )

    # AI config is best-effort for preview — fallback content is generated if not configured
    ai_provider = body.ai_provider or AI_PROVIDER
    ai_model = body.ai_model or AI_MODEL
    base_url = _extract_base_url(request)
    jenkins_url = result_data.get("jenkins_url", "")

    if body.include_links:
        report_url = f"{base_url}/results/{job_id}.html"
    else:
        report_url = f"results/{job_id}.html"
        job_name = result_data.get("job_name", "")
        build_number = result_data.get("build_number", 0)
        jenkins_url = f"{job_name} #{build_number}" if job_name else ""

    content = await generate_github_issue_content(
        failure=failure,
        report_url=report_url,
        ai_provider=ai_provider,
        ai_model=ai_model,
        jenkins_url=jenkins_url,
        include_links=body.include_links,
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
    await _validate_test_name_in_result(
        job_id, body.test_name, body.child_job_name, body.child_build_number
    )
    stored = await storage.get_result(job_id)
    if not stored or not stored.get("result"):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    result_data = stored["result"]
    failure_dict = _find_failure_in_result(
        result_data, body.test_name, body.child_job_name, body.child_build_number
    )
    if not failure_dict:
        raise HTTPException(
            status_code=400,
            detail=f"Test '{body.test_name}' not found in job {job_id}",
        )
    failure = FailureAnalysis.model_validate(failure_dict)

    # Apply any classification override from test_classifications
    failure = await _resolve_effective_failure(
        job_id, failure, body.child_job_name, body.child_build_number
    )

    # AI config is best-effort for preview — fallback content is generated if not configured
    ai_provider = body.ai_provider or AI_PROVIDER
    ai_model = body.ai_model or AI_MODEL
    base_url = _extract_base_url(request)
    jenkins_url = result_data.get("jenkins_url", "")

    if body.include_links:
        report_url = f"{base_url}/results/{job_id}.html"
    else:
        report_url = f"results/{job_id}.html"
        job_name = result_data.get("job_name", "")
        build_number = result_data.get("build_number", 0)
        jenkins_url = f"{job_name} #{build_number}" if job_name else ""

    content = await generate_jira_bug_content(
        failure=failure,
        report_url=report_url,
        ai_provider=ai_provider,
        ai_model=ai_model,
        jenkins_url=jenkins_url,
        include_links=body.include_links,
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
    await _validate_test_name_in_result(
        job_id, body.test_name, body.child_job_name, body.child_build_number
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
    stored = await storage.get_result(job_id)
    if not stored or not stored.get("result"):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    failure_dict = _find_failure_in_result(
        stored["result"], body.test_name, body.child_job_name, body.child_build_number
    )
    if failure_dict:
        failure = await _resolve_effective_failure(
            job_id,
            FailureAnalysis.model_validate(failure_dict),
            body.child_job_name,
            body.child_build_number,
        )
        if failure.analysis.classification == "PRODUCT BUG":
            raise HTTPException(
                status_code=400,
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

    # Auto-add comment with issue link (best-effort: the remote issue is
    # already created, so a failure here must not lose the created URL).
    comment_id = 0
    try:
        comment_text = f"GitHub Issue: {result['url']}"
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
        await _invalidate_cached_html(job_id)
    except Exception:
        logger.warning(
            "Failed to add comment/invalidate cache after GitHub issue creation "
            "for job_id=%s, issue url=%s",
            job_id,
            result["url"],
            exc_info=True,
        )

    return {
        "url": result["url"],
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
    await _validate_test_name_in_result(
        job_id, body.test_name, body.child_job_name, body.child_build_number
    )

    if not settings.jira_enabled:
        raise HTTPException(
            status_code=400,
            detail="Jira must be configured to create Jira bugs",
        )

    # Verify classification matches tracker
    stored = await storage.get_result(job_id)
    if not stored or not stored.get("result"):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    failure_dict = _find_failure_in_result(
        stored["result"], body.test_name, body.child_job_name, body.child_build_number
    )
    if failure_dict:
        failure = await _resolve_effective_failure(
            job_id,
            FailureAnalysis.model_validate(failure_dict),
            body.child_job_name,
            body.child_build_number,
        )
        if failure.analysis.classification == "CODE ISSUE":
            raise HTTPException(
                status_code=400,
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

    # Auto-add comment with issue link (best-effort: the remote bug is
    # already created, so a failure here must not lose the created URL).
    comment_id = 0
    try:
        comment_text = f"Jira Bug: {result['url']}"
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
        await _invalidate_cached_html(job_id)
    except Exception:
        logger.warning(
            "Failed to add comment/invalidate cache after Jira bug creation "
            "for job_id=%s, bug url=%s",
            job_id,
            result["url"],
            exc_info=True,
        )

    return {
        "url": result["url"],
        "key": result.get("key", ""),
        "title": body.title,
        "comment_id": comment_id,
    }


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

    await storage.override_classification(
        job_id=job_id,
        test_name=body.test_name,
        classification=body.classification,
        child_job_name=body.child_job_name,
        child_build_number=body.child_build_number,
        username=username,
        parent_job_name=parent_job_name,
    )
    await _invalidate_cached_html(job_id)
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


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    limit: int = Query(500, ge=1, le=10000),
) -> HTMLResponse:
    """Serve the dashboard page listing analysis reports.

    Args:
        request: The incoming request (used for base URL detection).
        limit: Maximum number of jobs to load from the database.
    """
    logger.debug(f"GET /dashboard: limit={limit}")
    base_url = _extract_base_url(request)
    jobs = await list_results_for_dashboard(limit)
    logger.debug(f"GET /dashboard: jobs_count={len(jobs)}")
    html_content = generate_dashboard_html(jobs, base_url, limit=limit)
    return HTMLResponse(html_content)


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request) -> HTMLResponse:
    """Serve the failure history page."""
    logger.debug("GET /history")
    base_url = _extract_base_url(request)
    html_content = generate_history_html(base_url)
    return HTMLResponse(html_content)


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


@app.get("/history/trends")
async def get_trends_endpoint(
    period: str = Query(default="daily"),
    days: int = Query(default=30, ge=1),
    job_name: str = Query(default=""),
    exclude_job_id: str = Query(
        default="", description="Exclude results from this job ID"
    ),
) -> dict:
    """Get failure rate trends over time."""
    logger.debug(
        f"GET /history/trends: period={period}, days={days}, job_name={job_name!r}"
    )
    return await storage.get_trends(
        period=period, days=days, job_name=job_name, exclude_job_id=exclude_job_id
    )


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
        raise HTTPException(status_code=400, detail=str(exc))
    if classify_job_id:
        await _invalidate_cached_html(classify_job_id)
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


def run() -> None:
    """Entry point for the CLI."""
    import uvicorn

    reload = os.getenv("DEBUG", "").lower() == "true"
    uvicorn.run(
        "jenkins_job_insight.main:app", host="0.0.0.0", port=APP_PORT, reload=reload
    )
