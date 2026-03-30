"""Tests for analyzer module."""

from unittest.mock import AsyncMock, patch

import jenkins
import pytest
from fastapi import HTTPException

from jenkins_job_insight.analyzer import (
    _call_ai_cli_with_retry,
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


class TestCallAiCliWithRetry:
    """Tests for the _call_ai_cli_with_retry function."""

    @pytest.mark.asyncio
    async def test_success_no_retry(self) -> None:
        """Test that a successful first call does not retry."""
        with patch(
            "jenkins_job_insight.analyzer.call_ai_cli", new_callable=AsyncMock
        ) as mock:
            mock.return_value = (True, "result")
            success, output = await _call_ai_cli_with_retry(
                "prompt", ai_provider="test"
            )
            assert success is True
            assert output == "result"
            assert mock.call_count == 1

    @pytest.mark.asyncio
    async def test_retryable_error_retries(self) -> None:
        """Test that a retryable error triggers a retry and succeeds."""
        with (
            patch(
                "jenkins_job_insight.analyzer.call_ai_cli", new_callable=AsyncMock
            ) as mock,
            patch("jenkins_job_insight.analyzer.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock.side_effect = [
                (False, "ENOENT: no such file or directory, rename config"),
                (True, "success after retry"),
            ]
            success, output = await _call_ai_cli_with_retry(
                "prompt", ai_provider="test", max_retries=1
            )
            assert success is True
            assert output == "success after retry"
            assert mock.call_count == 2

    @pytest.mark.asyncio
    async def test_non_retryable_error_no_retry(self) -> None:
        """Test that a non-retryable error does not trigger a retry."""
        with patch(
            "jenkins_job_insight.analyzer.call_ai_cli", new_callable=AsyncMock
        ) as mock:
            mock.return_value = (False, "some other error")
            success, output = await _call_ai_cli_with_retry(
                "prompt", ai_provider="test", max_retries=3
            )
            assert success is False
            assert "some other error" in output
            assert mock.call_count == 1

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self) -> None:
        """Test that retries stop after max_retries is exhausted."""
        with (
            patch(
                "jenkins_job_insight.analyzer.call_ai_cli", new_callable=AsyncMock
            ) as mock,
            patch("jenkins_job_insight.analyzer.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock.return_value = (False, "ENOENT: no such file or directory")
            success, _ = await _call_ai_cli_with_retry(
                "prompt", ai_provider="test", max_retries=2
            )
            assert success is False
            assert mock.call_count == 3  # initial + 2 retries
