"""Diagnostic archive extraction and context building for test failure analysis."""

import io
import os
import re
import shutil
import tarfile
import urllib.parse
import uuid
import zipfile
from pathlib import Path

import requests

from simple_logger.logger import get_logger

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))

EXTRACT_BASE = Path("/tmp/jenkins-insight")

ERROR_PATTERN = re.compile(
    r"\b(error|fail(ed|ure)?|exception|traceback|assert(ion)?|warn(ing)?|critical|fatal)\b",
    re.IGNORECASE,
)

POD_ISSUE_PATTERNS = re.compile(
    r"\b(CrashLoopBackOff|OOMKilled|ImagePullBackOff|ErrImagePull|CreateContainerError"
    r"|RunContainerError|Pending|Failed|Unknown|Evicted)\b"
)


def _extract_tar(
    fileobj: io.BytesIO, extract_dir: Path, max_extracted_bytes: int = 0
) -> None:
    """Extract a tar archive with security checks.

    Logs warnings for unsafe entries and skips them. On Python 3.12+,
    falls back to manual filtering if data_filter rejects entries.

    Args:
        fileobj: BytesIO containing the tar data.
        extract_dir: Directory to extract into.
        max_extracted_bytes: Maximum allowed total extracted size in bytes.
            If 0, no pre-extraction size check is performed.

    Raises:
        tarfile.TarError: If the data is not a valid tar archive or exceeds size limit.
    """
    tar = tarfile.open(fileobj=fileobj, mode="r:*")
    with tar:
        if hasattr(tarfile, "data_filter"):
            if max_extracted_bytes > 0:
                total_size = sum(m.size for m in tar.getmembers() if m.isreg())
                if total_size > max_extracted_bytes:
                    raise tarfile.TarError(
                        f"Estimated extracted size ({total_size / (1024 * 1024):.1f} MB) "
                        f"exceeds limit ({max_extracted_bytes / (1024 * 1024):.0f} MB)"
                    )
            try:
                tar.extractall(path=extract_dir, filter="data")
                return
            except Exception as exc:
                logger.warning(
                    f"Tar data_filter rejected entries, falling back to manual filtering: {exc}"
                )

        # Manual member-by-member extraction with filtering
        boundary = str(extract_dir.resolve()) + os.sep
        safe_members = []
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                if member.linkname.startswith("/") or ".." in member.linkname.split(
                    "/"
                ):
                    logger.warning(
                        f"Skipping tar entry with unsafe linkname: "
                        f"{member.name} -> {member.linkname}"
                    )
                    continue
                link_target = Path(extract_dir / member.linkname).resolve()
                if not str(link_target).startswith(boundary):
                    logger.warning(
                        f"Skipping tar entry with symlink outside extraction dir: "
                        f"{member.name} -> {member.linkname}"
                    )
                    continue
            member_path = Path(extract_dir / member.name).resolve()
            if not str(member_path).startswith(boundary):
                logger.warning(f"Skipping tar entry with unsafe path: {member.name}")
                continue
            if member.name.startswith("/") or ".." in member.name.split("/"):
                logger.warning(
                    f"Skipping tar entry with unsafe path component: {member.name}"
                )
                continue
            safe_members.append(member)

        if max_extracted_bytes > 0:
            total_size = sum(m.size for m in safe_members if m.isreg())
            if total_size > max_extracted_bytes:
                raise tarfile.TarError(
                    f"Estimated extracted size ({total_size / (1024 * 1024):.1f} MB) "
                    f"exceeds limit ({max_extracted_bytes / (1024 * 1024):.0f} MB)"
                )

        tar.extractall(path=extract_dir, members=safe_members)


def _extract_zip(
    fileobj: io.BytesIO, extract_dir: Path, max_extracted_bytes: int = 0
) -> None:
    """Extract a zip archive with security checks.

    Logs warnings for unsafe entries and skips them instead of raising.

    Args:
        fileobj: BytesIO containing the zip data.
        extract_dir: Directory to extract into.
        max_extracted_bytes: Maximum allowed total extracted size in bytes.
            If 0, no pre-extraction size check is performed.

    Raises:
        zipfile.BadZipFile: If the data is not a valid zip file.
        zipfile.BadZipFile: If estimated extracted size exceeds limit.
    """
    with zipfile.ZipFile(fileobj) as zf:
        boundary = str(extract_dir.resolve()) + os.sep
        safe_names = []
        for info in zf.infolist():
            if info.filename.startswith("/") or ".." in info.filename.split("/"):
                logger.warning(f"Skipping zip entry with unsafe path: {info.filename}")
                continue
            member_path = Path(extract_dir / info.filename).resolve()
            if not str(member_path).startswith(boundary):
                logger.warning(f"Skipping zip entry with unsafe path: {info.filename}")
                continue
            safe_names.append(info)

        if max_extracted_bytes > 0:
            total_size = sum(info.file_size for info in safe_names)
            if total_size > max_extracted_bytes:
                raise zipfile.BadZipFile(
                    f"Estimated extracted size ({total_size / (1024 * 1024):.1f} MB) "
                    f"exceeds limit ({max_extracted_bytes / (1024 * 1024):.0f} MB)"
                )

        zf.extractall(path=extract_dir, members=safe_names)


def download_jenkins_artifact(
    jenkins_url: str,
    username: str,
    password: str,
    job_name: str,
    build_number: int,
    artifact_path: str,
    max_size_mb: int = 500,
    ssl_verify: bool = True,
) -> bytes | None:
    """Download a build artifact from Jenkins.

    Args:
        jenkins_url: Jenkins server base URL.
        username: Jenkins username.
        password: Jenkins password or API token.
        job_name: Name of the Jenkins job (can include folders).
        build_number: Build number containing the artifact.
        artifact_path: Relative path to the artifact within the build.
        max_size_mb: Maximum allowed artifact size in megabytes.
        ssl_verify: Whether to verify SSL certificates.

    Returns:
        Raw bytes of the artifact file, or None if download fails.
    """
    if ".." in artifact_path.split("/"):
        logger.warning(f"Artifact path contains unsafe component '..': {artifact_path}")
        return None

    # Strip common accidental prefixes — the API endpoint already includes /artifact/
    for prefix in ("artifact/", "artifacts/"):
        if artifact_path.startswith(prefix):
            artifact_path = artifact_path[len(prefix) :]
            logger.debug(
                f"Stripped '{prefix}' prefix from artifact path: {artifact_path}"
            )
            break

    job_path = "/job/".join(
        urllib.parse.quote(seg, safe="") for seg in job_name.split("/")
    )
    url = f"{jenkins_url.rstrip('/')}/job/{job_path}/{build_number}/artifact/{urllib.parse.quote(artifact_path, safe='/')}"
    logger.info(f"Downloading artifact: {url}")

    try:
        session = requests.Session()
        session.auth = (username, password)
        session.verify = ssl_verify

        response = session.get(url, stream=True, timeout=60)
        if response.status_code != 200:
            logger.warning(
                f"Failed to download artifact '{artifact_path}' from "
                f"{job_name} #{build_number}: HTTP {response.status_code}"
            )
            return None

        # Stream download with size tracking
        buffer = io.BytesIO()
        downloaded = 0
        max_bytes = max_size_mb * 1024 * 1024

        for chunk in response.iter_content(chunk_size=8192):
            downloaded += len(chunk)
            if downloaded > max_bytes:
                logger.warning(
                    f"Artifact download exceeded maximum size ({max_size_mb} MB)"
                )
                return None
            buffer.write(chunk)

        return buffer.getvalue()

    except Exception as e:
        logger.warning(f"Failed to download artifact: {e}")
        return None


def validate_and_extract_archive(
    archive_data: bytes, max_size_mb: int = 500
) -> Path | None:
    """Validate and extract a tar/tar.gz/tgz or zip archive to a temporary directory.

    Performs size validation, format validation, and path traversal security checks
    before extracting. Returns None on failure instead of raising.

    Args:
        archive_data: Raw bytes of the archive.
        max_size_mb: Maximum allowed size of ``archive_data`` in megabytes.

    Returns:
        Path to the directory where the archive was extracted, or None on failure.
    """
    size_mb = len(archive_data) / (1024 * 1024)
    if size_mb > max_size_mb:
        logger.warning(
            f"Archive size ({size_mb:.1f} MB) exceeds maximum allowed size ({max_size_mb} MB)"
        )
        return None

    extract_dir = EXTRACT_BASE / f"diagnostic-archive-{uuid.uuid4().hex[:8]}"
    extract_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Extracting archive ({size_mb:.1f} MB) to {extract_dir}")

    fileobj = io.BytesIO(archive_data)

    # Try tar first, then zip
    max_extracted_bytes = max_size_mb * 10 * 1024 * 1024
    try:
        _extract_tar(fileobj, extract_dir, max_extracted_bytes=max_extracted_bytes)
    except (tarfile.TarError, Exception):
        fileobj.seek(0)
        try:
            _extract_zip(fileobj, extract_dir, max_extracted_bytes=max_extracted_bytes)
        except (zipfile.BadZipFile, Exception) as exc:
            shutil.rmtree(extract_dir, ignore_errors=True)
            logger.warning(f"Invalid archive (not a valid tar or zip file): {exc}")
            return None

    # Post-extraction size check to prevent decompression bombs
    extracted_size = sum(
        f.stat().st_size for f in extract_dir.rglob("*") if f.is_file()
    )
    if extracted_size > max_extracted_bytes:
        shutil.rmtree(extract_dir)
        logger.warning(
            f"Extracted size ({extracted_size / (1024 * 1024):.1f} MB) exceeds maximum "
            f"allowed ({max_size_mb * 10} MB). Possible decompression bomb."
        )
        return None

    logger.info(f"Extraction complete: {extract_dir}")
    return extract_dir


def build_diagnostic_context(extract_path: Path, max_lines: int = 1000) -> str:
    """Walk an extracted archive directory and build a structured diagnostic summary.

    Discovers log files, YAML/JSON resource dumps, and event files, then
    extracts error/warning lines, pod issues, and warning-type Kubernetes
    events into a human-readable context string.

    Args:
        extract_path: Root directory of the extracted archive.
        max_lines: Maximum number of lines in the returned context string.

    Returns:
        A structured text summary with sections for log errors, Kubernetes
        events, and pod issues. Returns a note if no relevant content is found.
    """
    logger.info(f"Building diagnostic context from {extract_path}")

    log_files: list[Path] = []
    yaml_json_files: list[Path] = []
    event_files: list[Path] = []
    all_files: list[str] = []

    for file_path in extract_path.rglob("*"):
        if not file_path.is_file():
            continue

        relative = str(file_path.relative_to(extract_path))
        all_files.append(relative)
        name_lower = file_path.name.lower()

        if (
            name_lower.endswith((".log", ".txt"))
            or "/logs/" in relative
            or "/log/" in relative
        ):
            log_files.append(file_path)
        elif name_lower.endswith((".yaml", ".yml", ".json")):
            if "event" in name_lower or "event" in relative.lower():
                event_files.append(file_path)
            else:
                yaml_json_files.append(file_path)

    logger.info(
        f"Archive file discovery: {len(all_files)} total files, "
        f"{len(log_files)} log files, {len(event_files)} event files, "
        f"{len(yaml_json_files)} yaml/json files"
    )
    error_lines: list[str] = []
    event_lines: list[str] = []
    pod_issues: list[str] = []
    seen_errors: set[str] = set()

    # Extract error/warning lines from log files (deduplicated)
    for log_file in log_files:
        try:
            text = log_file.read_text(errors="replace")
            relative_name = str(log_file.relative_to(extract_path))
            for line in text.splitlines():
                if ERROR_PATTERN.search(line):
                    dedup_key = line.strip()
                    if dedup_key in seen_errors:
                        continue
                    seen_errors.add(dedup_key)
                    error_lines.append(f"[{relative_name}]: {line.rstrip()}")
        except OSError:
            continue

    # Extract warning events
    for event_file in event_files:
        try:
            text = event_file.read_text(errors="replace")
            relative_name = str(event_file.relative_to(extract_path))
            for line in text.splitlines():
                if "Warning" in line or "warning" in line:
                    event_lines.append(f"[{relative_name}]: {line.rstrip()}")
        except OSError:
            continue

    # Scan YAML/JSON files for pod issues
    for resource_file in yaml_json_files:
        try:
            text = resource_file.read_text(errors="replace")
            relative_name = str(resource_file.relative_to(extract_path))
            for line in text.splitlines():
                if POD_ISSUE_PATTERNS.search(line):
                    pod_issues.append(f"[{relative_name}]: {line.strip()}")
        except OSError:
            continue

    # Build the structured context
    sections: list[str] = [
        "=== DIAGNOSTIC ARCHIVE CONTEXT ===",
        f"Extracted archive path: {extract_path}",
        f"Archive contains {len(all_files)} files.",
        "",
        "IMPORTANT: You MUST read the files at the path above. The summary below is only a preview.",
        "Use the extracted path to read full log files, pod logs, events, and resource status.",
        "",
    ]

    if error_lines:
        sections.append("--- Error/Warning Lines from Logs ---")
        sections.extend(error_lines)
        sections.append("")

    if event_lines:
        sections.append("--- Kubernetes Events (Warnings) ---")
        sections.extend(event_lines)
        sections.append("")

    if pod_issues:
        sections.append("--- Pod Issues ---")
        sections.extend(pod_issues)
        sections.append("")

    if not error_lines and not event_lines and not pod_issues:
        sections.append(
            "No errors, warnings, or pod issues found in diagnostic archive."
        )
        # Include file listing so AI knows what's available
        sections.append("")
        sections.append("--- Files in Archive ---")
        sections.extend(all_files[:50])
        if len(all_files) > 50:
            sections.append(f"... and {len(all_files) - 50} more files")
        sections.append("")

    # Truncate to max_lines
    if len(sections) > max_lines:
        sections = sections[:max_lines]
        sections.append("... [truncated to max_lines limit]")

    # Always end with the path reminder
    sections.append(f"--- Full archive available at: {extract_path} ---")
    sections.append("You MUST explore the files above before classifying any failure.")

    context = "\n".join(sections)
    logger.info(
        f"Diagnostic context built: {len(error_lines)} error lines, "
        f"{len(event_lines)} event lines, {len(pod_issues)} pod issues, "
        f"total context: {len(context)} chars"
    )
    return context


def cleanup_extract_dir(extract_path: Path) -> None:
    """Remove an extracted archive directory tree.

    Logs the cleanup operation. Errors during removal are logged as warnings
    and swallowed so that cleanup failures never crash the pipeline.

    Args:
        extract_path: Path to the extracted directory to remove.
    """
    logger.info(f"Cleaning up extracted archive directory: {extract_path}")
    try:
        shutil.rmtree(extract_path)
        logger.info(f"Cleanup complete: {extract_path}")
    except OSError as exc:
        logger.warning(f"Failed to clean up {extract_path}: {exc}")


def fetch_diagnostic_context(
    jenkins_url: str,
    username: str,
    password: str,
    job_name: str,
    build_number: int,
    artifact_path: str,
    max_size_mb: int = 500,
    ssl_verify: bool = True,
    max_context_lines: int = 200,
) -> tuple[str, Path | None]:
    """Download, extract, and build diagnostic context from a Jenkins artifact.

    Single entry point for the entire diagnostic archive pipeline. Downloads
    the artifact from Jenkins, extracts it, and builds a context summary.
    Never raises — returns a warning message on failure.

    Args:
        jenkins_url: Jenkins server base URL.
        username: Jenkins username.
        password: Jenkins password or API token.
        job_name: Name of the Jenkins job (can include folders).
        build_number: Build number containing the artifact.
        artifact_path: Relative path to the artifact within the build.
        max_size_mb: Maximum allowed artifact size in megabytes.
        ssl_verify: Whether to verify SSL certificates.
        max_context_lines: Maximum lines in the context summary.

    Returns:
        Tuple of (context_string, extract_path_or_None).
        The caller must call cleanup_extract_dir(extract_path) when done.
    """
    archive_data = download_jenkins_artifact(
        jenkins_url=jenkins_url,
        username=username,
        password=password,
        job_name=job_name,
        build_number=build_number,
        artifact_path=artifact_path,
        max_size_mb=max_size_mb,
        ssl_verify=ssl_verify,
    )
    if archive_data is None:
        return (
            f"NOTE: Diagnostic archive '{artifact_path}' could not be downloaded. "
            f"Analysis will proceed without diagnostic archive data.",
            None,
        )

    extract_path = validate_and_extract_archive(archive_data, max_size_mb)
    if extract_path is None:
        return (
            f"NOTE: Diagnostic archive '{artifact_path}' could not be extracted. "
            f"Analysis will proceed without diagnostic archive data.",
            None,
        )

    context = build_diagnostic_context(extract_path, max_context_lines)
    logger.info(
        f"Diagnostic archive context length: {len(context)} chars, "
        f"{len(context.splitlines())} lines"
    )
    return context, extract_path
