"""Diagnostic archive extraction and context building for test failure analysis."""

import io
import os
import re
import shutil
import tarfile
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path

from simple_logger.logger import get_logger

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))

EXTRACT_BASE = Path("/tmp/jenkins-insight")

# Maximum ratio of extracted size to compressed size. Typical compression ratios
# for diagnostic archives (logs, YAML, JSON) range from 3-8x; 10x provides safe
# headroom while still catching decompression bombs.
EXTRACT_SIZE_MULTIPLIER = 10


class _SizeLimitExceeded(Exception):
    """Raised when archive extraction exceeds size limit."""

    pass


ERROR_PATTERN = re.compile(
    r"\b(error|fail(ed|ure)?|exception|traceback|assert(ion)?|warn(ing)?|critical|fatal)\b",
    re.IGNORECASE,
)

ABNORMAL_STATUS_PATTERNS = re.compile(
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
        tarfile.TarError: If the data is not a valid tar archive.
        _SizeLimitExceeded: If the estimated extracted size exceeds the limit.
    """
    tar = tarfile.open(fileobj=fileobj, mode="r:*")
    with tar:
        if hasattr(tarfile, "data_filter"):
            if max_extracted_bytes > 0:
                total_size = sum(m.size for m in tar.getmembers() if m.isreg())
                if total_size > max_extracted_bytes:
                    raise _SizeLimitExceeded(
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
                member_parent = Path(extract_dir / member.name).parent
                link_target = (member_parent / member.linkname).resolve()
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
                raise _SizeLimitExceeded(
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
        _SizeLimitExceeded: If the estimated extracted size exceeds the limit.
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
                raise _SizeLimitExceeded(
                    f"Estimated extracted size ({total_size / (1024 * 1024):.1f} MB) "
                    f"exceeds limit ({max_extracted_bytes / (1024 * 1024):.0f} MB)"
                )

        zf.extractall(path=extract_dir, members=safe_names)


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
    max_extracted_bytes = max_size_mb * EXTRACT_SIZE_MULTIPLIER * 1024 * 1024
    try:
        _extract_tar(fileobj, extract_dir, max_extracted_bytes=max_extracted_bytes)
    except _SizeLimitExceeded as exc:
        shutil.rmtree(extract_dir, ignore_errors=True)
        logger.warning(f"Archive rejected: {exc}")
        return None
    except Exception:
        fileobj.seek(0)
        try:
            _extract_zip(fileobj, extract_dir, max_extracted_bytes=max_extracted_bytes)
        except _SizeLimitExceeded as exc:
            shutil.rmtree(extract_dir, ignore_errors=True)
            logger.warning(f"Archive rejected: {exc}")
            return None
        except Exception as exc:
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
    extracts error/warning lines, abnormal status indicators, and warning
    events into a human-readable context string.

    Args:
        extract_path: Root directory of the extracted archive.
        max_lines: Maximum number of lines in the returned context string.

    Returns:
        A structured text summary with sections for log errors, warning
        events, and abnormal status indicators. Returns a note if no relevant content is found.
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
    status_issues: list[str] = []
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

    # Scan YAML/JSON files for abnormal status indicators
    for resource_file in yaml_json_files:
        try:
            text = resource_file.read_text(errors="replace")
            relative_name = str(resource_file.relative_to(extract_path))
            for line in text.splitlines():
                if ABNORMAL_STATUS_PATTERNS.search(line):
                    status_issues.append(f"[{relative_name}]: {line.strip()}")
        except OSError:
            continue

    # Build the structured context
    sections: list[str] = [
        "=== DIAGNOSTIC ARCHIVE CONTEXT ===",
        f"Extracted archive path: {extract_path}",
        f"Archive contains {len(all_files)} files.",
        "",
        "IMPORTANT: You MUST read the files at the path above. The summary below is only a preview.",
        "Use the extracted path to read full log files, events, and resource status.",
        "",
    ]

    if error_lines:
        sections.append("--- Error/Warning Lines from Logs ---")
        sections.extend(error_lines)
        sections.append("")

    if event_lines:
        sections.append("--- Warning Events ---")
        sections.extend(event_lines)
        sections.append("")

    if status_issues:
        sections.append("--- Abnormal Status Indicators ---")
        sections.extend(status_issues)
        sections.append("")

    if not error_lines and not event_lines and not status_issues:
        sections.append(
            "No errors, warnings, or status issues found in diagnostic archive."
        )
        # Include file listing so AI knows what's available
        sections.append("")
        sections.append("--- Files in Archive ---")
        sections.extend(all_files)
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
        f"{len(event_lines)} event lines, {len(status_issues)} status issues, "
        f"total context: {len(context)} chars"
    )
    return context


def fetch_all_artifacts(
    artifact_list: list[dict],
    download_fn: Callable[[str], bytes | None],
    max_size_mb: int = 500,
    max_context_lines: int = 200,
) -> tuple[str, Path | None]:
    """Download all build artifacts, extract archives, and build diagnostic context.

    Downloads each artifact using the provided download function. Archive files
    (tar/zip) are extracted. Non-archive files are stored directly. All content
    is placed under a single artifacts directory for AI consumption.

    Args:
        artifact_list: List of artifact dicts from Jenkins API (each has 'relativePath').
        download_fn: Callable that takes an artifact relative path and returns bytes or None.
        max_size_mb: Maximum allowed artifact size in megabytes (per artifact).
        max_context_lines: Maximum lines in the context summary.

    Returns:
        Tuple of (context_string, artifacts_dir_or_None).
        The caller must call cleanup_extract_dir(artifacts_dir) when done.
    """
    if not artifact_list:
        logger.debug("No artifacts found for build, skipping artifact download")
        return "", None

    artifacts_dir = EXTRACT_BASE / f"artifacts-{uuid.uuid4().hex[:8]}"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading {len(artifact_list)} artifacts to {artifacts_dir}")

    downloaded_count = 0
    for artifact in artifact_list:
        relative_path = artifact.get("relativePath", "")
        if not relative_path:
            continue

        data = download_fn(relative_path)
        if data is None:
            logger.warning(f"Skipping artifact '{relative_path}': download failed")
            continue

        downloaded_count += 1

        # Try to extract if it looks like an archive
        if _is_archive(relative_path):
            extract_subdir = artifacts_dir / _strip_archive_extension(relative_path)
            extract_subdir.mkdir(parents=True, exist_ok=True)
            extract_result = validate_and_extract_archive(data, max_size_mb)
            if extract_result is not None:
                # Move extracted contents into the artifacts dir under a subdir
                # named after the archive
                _move_contents(extract_result, extract_subdir)
                cleanup_extract_dir(extract_result)
                logger.info(f"Extracted archive artifact '{relative_path}'")
                continue
            else:
                logger.warning(
                    f"Could not extract '{relative_path}', storing as raw file"
                )

        # Store as raw file
        dest = artifacts_dir / relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    if downloaded_count == 0:
        cleanup_extract_dir(artifacts_dir)
        return (
            "NOTE: No artifacts could be downloaded. Analysis will proceed without artifact data.",
            None,
        )

    context = build_diagnostic_context(artifacts_dir, max_context_lines)
    logger.info(
        f"Artifacts context length: {len(context)} chars, "
        f"{len(context.splitlines())} lines"
    )
    return context, artifacts_dir


def _is_archive(filename: str) -> bool:
    """Check if a filename looks like a tar or zip archive."""
    lower = filename.lower()
    return lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".zip"))


def _strip_archive_extension(filename: str) -> str:
    """Strip archive extension from filename for use as directory name."""
    lower = filename.lower()
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tar", ".zip"):
        if lower.endswith(ext):
            return filename[: -len(ext)]
    return filename


def _move_contents(src: Path, dest: Path) -> None:
    """Move all contents from src directory into dest directory.

    Validates symlinks to prevent path traversal via race conditions.
    Logs warnings for unsafe entries and overwrites for conflicting targets.
    """
    src_boundary = str(src.resolve()) + os.sep
    for item in src.iterdir():
        if item.is_symlink():
            link_target = item.resolve()
            if not str(link_target).startswith(src_boundary):
                logger.warning(f"Skipping unsafe symlink during move: {item.name}")
                continue

        target = dest / item.name
        if target.exists():
            logger.debug(f"Overwriting existing target during move: {target}")
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.move(str(item), str(target))


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
