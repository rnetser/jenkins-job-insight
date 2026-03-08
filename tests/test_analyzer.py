"""Tests for analyzer module."""

import jenkins
import pytest
from fastapi import HTTPException

from jenkins_job_insight.analyzer import (
    JOB_INSIGHT_PROMPT_FILENAME,
    _read_repo_prompt,
    _resolve_custom_prompt,
    handle_jenkins_exception,
)


class TestHandleJenkinsException:
    """Tests for the handle_jenkins_exception function."""

    def test_handle_not_found_exception(self) -> None:
        """Test that NotFoundException returns 404."""
        exc = jenkins.NotFoundException("Job not found")
        with pytest.raises(HTTPException) as exc_info:
            handle_jenkins_exception(exc, "my-job", 123)
        assert exc_info.value.status_code == 404
        assert "my-job" in exc_info.value.detail
        assert "123" in exc_info.value.detail

    def test_handle_jenkins_exception_with_not_found_message(self) -> None:
        """Test that JenkinsException with 'not found' message returns 404."""
        exc = jenkins.JenkinsException("Job does not exist")
        with pytest.raises(HTTPException) as exc_info:
            handle_jenkins_exception(exc, "my-job", 456)
        assert exc_info.value.status_code == 404

    def test_handle_jenkins_exception_with_404_message(self) -> None:
        """Test that JenkinsException with '404' in message returns 404."""
        exc = jenkins.JenkinsException("Error 404: Resource not available")
        with pytest.raises(HTTPException) as exc_info:
            handle_jenkins_exception(exc, "my-job", 789)
        assert exc_info.value.status_code == 404

    def test_handle_jenkins_exception_unauthorized(self) -> None:
        """Test that unauthorized error returns 502 with auth message."""
        exc = jenkins.JenkinsException("401 Unauthorized")
        with pytest.raises(HTTPException) as exc_info:
            handle_jenkins_exception(exc, "my-job", 123)
        assert exc_info.value.status_code == 502
        assert "authentication failed" in exc_info.value.detail.lower()

    def test_handle_jenkins_exception_forbidden(self) -> None:
        """Test that forbidden error returns 502 with permission message."""
        exc = jenkins.JenkinsException("403 Forbidden")
        with pytest.raises(HTTPException) as exc_info:
            handle_jenkins_exception(exc, "my-job", 123)
        assert exc_info.value.status_code == 502
        assert "access denied" in exc_info.value.detail.lower()
        assert "my-job" in exc_info.value.detail

    def test_handle_jenkins_exception_generic(self) -> None:
        """Test that generic JenkinsException returns 502 with error details."""
        exc = jenkins.JenkinsException("Connection timeout")
        with pytest.raises(HTTPException) as exc_info:
            handle_jenkins_exception(exc, "my-job", 123)
        assert exc_info.value.status_code == 502
        assert "Jenkins error" in exc_info.value.detail

    def test_handle_non_jenkins_exception(self) -> None:
        """Test that non-Jenkins exceptions return 502 with connection error."""
        exc = ConnectionError("Failed to connect")
        with pytest.raises(HTTPException) as exc_info:
            handle_jenkins_exception(exc, "my-job", 123)
        assert exc_info.value.status_code == 502
        assert "Failed to connect to Jenkins" in exc_info.value.detail


class TestReadRepoPrompt:
    """Tests for _read_repo_prompt helper."""

    def test_returns_empty_when_no_repo_path(self) -> None:
        """Test that None repo_path returns empty string."""
        assert _read_repo_prompt(None) == ""

    def test_returns_empty_when_file_missing(self, tmp_path) -> None:
        """Test that missing prompt file returns empty string."""
        assert _read_repo_prompt(tmp_path) == ""

    def test_reads_prompt_file(self, tmp_path) -> None:
        """Test that existing prompt file content is returned."""
        prompt_file = tmp_path / JOB_INSIGHT_PROMPT_FILENAME
        prompt_file.write_text("Custom instructions here", encoding="utf-8")
        assert _read_repo_prompt(tmp_path) == "Custom instructions here"

    def test_strips_whitespace(self, tmp_path) -> None:
        """Test that whitespace is stripped from prompt file content."""
        prompt_file = tmp_path / JOB_INSIGHT_PROMPT_FILENAME
        prompt_file.write_text("  \n  Custom instructions  \n  ", encoding="utf-8")
        assert _read_repo_prompt(tmp_path) == "Custom instructions"

    def test_returns_empty_on_read_error(self, monkeypatch, tmp_path) -> None:
        """Test that read errors return empty string gracefully."""
        prompt_file = tmp_path / JOB_INSIGHT_PROMPT_FILENAME
        prompt_file.write_text("Custom instructions here", encoding="utf-8")

        def raise_os_error(*args, **kwargs) -> str:
            raise OSError("boom")

        monkeypatch.setattr(type(prompt_file), "read_text", raise_os_error)
        assert _read_repo_prompt(tmp_path) == ""


class TestResolveCustomPrompt:
    """Tests for _resolve_custom_prompt helper."""

    def test_prefers_raw_prompt_over_repo_prompt(self, tmp_path) -> None:
        """Test that request raw_prompt takes precedence over repo prompt."""
        prompt_file = tmp_path / JOB_INSIGHT_PROMPT_FILENAME
        prompt_file.write_text("Repo prompt", encoding="utf-8")

        assert (
            _resolve_custom_prompt("  Request prompt  ", tmp_path) == "Request prompt"
        )

    def test_falls_back_to_repo_prompt(self, tmp_path) -> None:
        """Test that repo prompt is used when raw_prompt is missing."""
        prompt_file = tmp_path / JOB_INSIGHT_PROMPT_FILENAME
        prompt_file.write_text("Repo prompt", encoding="utf-8")

        assert _resolve_custom_prompt(None, tmp_path) == "Repo prompt"

    def test_blank_raw_prompt_falls_back_to_repo_prompt(self, tmp_path) -> None:
        """Test that blank raw_prompt does not suppress the repo prompt."""
        prompt_file = tmp_path / JOB_INSIGHT_PROMPT_FILENAME
        prompt_file.write_text("Repo prompt", encoding="utf-8")

        assert _resolve_custom_prompt("   \n  ", tmp_path) == "Repo prompt"
