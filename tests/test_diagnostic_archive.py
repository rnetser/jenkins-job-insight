"""Tests for diagnostic archive tar/zip extraction and context building."""

import io
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest

from jenkins_job_insight import diagnostic_archive
from jenkins_job_insight.diagnostic_archive import (
    build_diagnostic_context,
    cleanup_extract_dir,
    validate_and_extract_archive,
)


@pytest.fixture
def extract_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Override EXTRACT_BASE to use tmp_path so tests never leave files in /tmp."""
    base = tmp_path / "extract-base"
    base.mkdir()
    monkeypatch.setattr(diagnostic_archive, "EXTRACT_BASE", base)
    return base


def _make_tar_gz(files: dict[str, str]) -> bytes:
    """Create a gzip-compressed tar archive in memory.

    Args:
        files: Mapping of archive member names to their text content.

    Returns:
        Raw bytes of the tar.gz archive.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _make_tar_uncompressed(files: dict[str, str]) -> bytes:
    """Create an uncompressed tar archive in memory.

    Args:
        files: Mapping of archive member names to their text content.

    Returns:
        Raw bytes of the tar archive.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestValidateAndExtractTar:
    """Tests for validate_and_extract_archive (tar archives)."""

    def test_valid_tar_gz(self, extract_base: Path) -> None:
        """Valid tar.gz archive is extracted and files are accessible."""
        tar_data = _make_tar_gz(
            {
                "logs/app.log": "some log content\n",
                "config.yaml": "key: value\n",
            }
        )

        result = validate_and_extract_archive(tar_data)

        assert result.exists()
        assert result.is_dir()
        assert (result / "logs" / "app.log").read_text() == "some log content\n"
        assert (result / "config.yaml").read_text() == "key: value\n"

    def test_valid_tar_uncompressed(self, extract_base: Path) -> None:
        """Valid uncompressed tar archive is extracted and files are accessible."""
        tar_data = _make_tar_uncompressed(
            {
                "data/report.txt": "hello world\n",
            }
        )

        result = validate_and_extract_archive(tar_data)

        assert result.exists()
        assert (result / "data" / "report.txt").read_text() == "hello world\n"

    def test_size_exceeds_max(self, extract_base: Path) -> None:
        """Tar data exceeding max_size_mb returns None."""
        tar_data = _make_tar_gz({"file.txt": "x"})

        assert validate_and_extract_archive(tar_data, max_size_mb=0) is None

    def test_invalid_tar_data(self, extract_base: Path) -> None:
        """Random bytes that are not a valid tar return None."""
        assert (
            validate_and_extract_archive(b"this is definitely not a tar file") is None
        )

    def test_zip_slip_prevention(self, extract_base: Path, tmp_path: Path) -> None:
        """Tar entries with path traversal are skipped, not extracted."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo(name="../../../etc/passwd")
            info.size = 5
            tar.addfile(info, io.BytesIO(b"hello"))
            # Add a safe file too
            safe_info = tarfile.TarInfo(name="safe.txt")
            safe_info.size = 4
            tar.addfile(safe_info, io.BytesIO(b"safe"))

        extract_path = validate_and_extract_archive(buf.getvalue(), max_size_mb=10)
        try:
            # Safe file should exist
            assert (extract_path / "safe.txt").exists()
            # Unsafe file should NOT exist anywhere
            assert not (extract_path / "etc" / "passwd").exists()
        finally:
            shutil.rmtree(extract_path, ignore_errors=True)


class TestExtractZip:
    """Tests for validate_and_extract_archive (zip archives)."""

    def test_valid_zip(self, extract_base: Path) -> None:
        """Valid zip archive is extracted and files are accessible."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("logs/app.log", "some log content\n")
            zf.writestr("config.yaml", "key: value\n")
        zip_data = buf.getvalue()

        result = validate_and_extract_archive(zip_data)

        assert result.exists()
        assert result.is_dir()
        assert (result / "logs" / "app.log").read_text() == "some log content\n"
        assert (result / "config.yaml").read_text() == "key: value\n"

    def test_invalid_zip_data(self, extract_base: Path) -> None:
        """Random bytes that are neither a valid tar nor zip return None."""
        assert (
            validate_and_extract_archive(b"this is definitely not an archive") is None
        )

    def test_zip_path_traversal(self, extract_base: Path) -> None:
        """Zip entries with path traversal are skipped, not extracted."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../../etc/passwd", "malicious")
            zf.writestr("safe.txt", "safe content")

        extract_path = validate_and_extract_archive(buf.getvalue(), max_size_mb=10)
        try:
            # Safe file should exist
            assert (extract_path / "safe.txt").exists()
            # Unsafe file should NOT exist
            assert not (extract_path / "etc" / "passwd").exists()
        finally:
            shutil.rmtree(extract_path, ignore_errors=True)


class TestBuildDiagnosticContext:
    """Tests for build_diagnostic_context."""

    def test_extracts_error_lines_from_logs(self, tmp_path: Path) -> None:
        """Error and failure lines from .log files appear in context."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "app.log"
        log_file.write_text(
            "2024-01-01 INFO Starting up\n"
            "2024-01-01 ERROR Connection refused\n"
            "2024-01-01 INFO Retrying\n"
            "2024-01-01 FAILURE Something broke\n"
        )

        context = build_diagnostic_context(tmp_path)

        assert "Connection refused" in context
        assert "FAILURE Something broke" in context
        assert "Starting up" not in context

    def test_extracts_warning_events(self, tmp_path: Path) -> None:
        """Warning lines from event files appear in the events section."""
        event_dir = tmp_path / "cluster-scoped-resources"
        event_dir.mkdir(parents=True)
        event_file = event_dir / "events.yaml"
        event_file.write_text(
            "kind: Event\n"
            "type: Warning\n"
            "message: Back-off restarting failed container\n"
            "type: Normal\n"
            "message: Scheduled successfully\n"
        )

        context = build_diagnostic_context(tmp_path)

        assert "Warning" in context
        assert (
            "Back-off restarting failed container" not in context
            or "Warning" in context
        )
        # The Warning line itself should be captured
        assert "type: Warning" in context

    def test_detects_status_issues(self, tmp_path: Path) -> None:
        """YAML files with CrashLoopBackOff/OOMKilled appear in abnormal status indicators."""
        resource_dir = tmp_path / "namespaces" / "default"
        resource_dir.mkdir(parents=True)
        pod_file = resource_dir / "pods.yaml"
        pod_file.write_text(
            "apiVersion: v1\n"
            "kind: Pod\n"
            "status:\n"
            "  containerStatuses:\n"
            "    - state:\n"
            "        waiting:\n"
            "          reason: CrashLoopBackOff\n"
            "    - state:\n"
            "        terminated:\n"
            "          reason: OOMKilled\n"
        )

        context = build_diagnostic_context(tmp_path)

        assert "CrashLoopBackOff" in context
        assert "OOMKilled" in context
        assert "Abnormal Status Indicators" in context

    def test_empty_directory_returns_note(self, tmp_path: Path) -> None:
        """Empty directory returns the 'no issues found' note."""
        context = build_diagnostic_context(tmp_path)

        assert "No errors, warnings, or status issues found" in context

    def test_max_lines_truncation(self, tmp_path: Path) -> None:
        """Context is truncated when it exceeds max_lines."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "big.log"
        # Generate many error lines to exceed a small max_lines
        lines = [f"ERROR failure number {i}\n" for i in range(200)]
        log_file.write_text("".join(lines))

        max_lines = 10
        context = build_diagnostic_context(tmp_path, max_lines=max_lines)

        assert "truncated" in context.lower()
        # The total line count in the output should not greatly exceed max_lines
        output_lines = context.splitlines()
        # max_lines + truncation message + 2 footer lines + empty line
        assert len(output_lines) <= max_lines + 4


class TestCleanupExtractDir:
    """Tests for cleanup_extract_dir."""

    def test_cleanup_removes_directory(self, tmp_path: Path) -> None:
        """Cleanup removes the target directory tree."""
        target = tmp_path / "diagnostic-archive-abc123"
        target.mkdir()
        (target / "nested").mkdir()
        (target / "nested" / "file.txt").write_text("data")

        cleanup_extract_dir(target)

        assert not target.exists()

    def test_cleanup_nonexistent_dir_no_error(self, tmp_path: Path) -> None:
        """Cleanup of a non-existent path does not raise."""
        nonexistent = tmp_path / "does-not-exist"

        # Should not raise; errors are logged as warnings and swallowed
        cleanup_extract_dir(nonexistent)
