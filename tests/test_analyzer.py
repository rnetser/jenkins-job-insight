"""Tests for analyzer module."""

import tempfile
from pathlib import Path

import jenkins
import pytest
from fastapi import HTTPException

from jenkins_job_insight.analyzer import (
    ANALYSIS_PROMPT,
    build_failures_from_response,
    ensure_string,
    handle_jenkins_exception,
    load_analysis_prompt,
)


class TestLoadAnalysisPrompt:
    """Tests for the load_analysis_prompt function."""

    def test_load_analysis_prompt_returns_default_when_file_not_exists(self) -> None:
        """Test that default prompt is returned when file doesn't exist."""
        result = load_analysis_prompt("/nonexistent/path/PROMPT.md")
        assert result == ANALYSIS_PROMPT

    def test_load_analysis_prompt_returns_file_content_when_exists(self) -> None:
        """Test that file content is returned when file exists."""
        custom_prompt = "You are a custom analysis assistant."

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as tmp_file:
            tmp_file.write(custom_prompt)
            tmp_path = tmp_file.name

        try:
            result = load_analysis_prompt(tmp_path)
            assert result == custom_prompt
        finally:
            Path(tmp_path).unlink()

    def test_load_analysis_prompt_reads_multiline_content(self) -> None:
        """Test that multiline content is properly read."""
        custom_prompt = """You are a Jenkins failure analyzer.

TASK:
Analyze the build failure and provide insights.

OUTPUT:
Provide JSON response with:
- summary
- failures list
"""

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as tmp_file:
            tmp_file.write(custom_prompt)
            tmp_path = tmp_file.name

        try:
            result = load_analysis_prompt(tmp_path)
            assert result == custom_prompt
            assert "TASK:" in result
            assert "OUTPUT:" in result
        finally:
            Path(tmp_path).unlink()


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


class TestEnsureString:
    """Tests for the ensure_string helper function."""

    def test_ensure_string_with_none(self) -> None:
        """Test that None returns empty string."""
        result = ensure_string(None)
        assert result == ""

    def test_ensure_string_with_string(self) -> None:
        """Test that string values pass through unchanged."""
        result = ensure_string("hello world")
        assert result == "hello world"

    def test_ensure_string_with_empty_string(self) -> None:
        """Test that empty string returns empty string."""
        result = ensure_string("")
        assert result == ""

    def test_ensure_string_with_dict(self) -> None:
        """Test that dict is formatted as key: value pairs."""
        input_dict = {"file": "test.py", "suggestion": "Fix the import"}
        result = ensure_string(input_dict)
        assert "file: test.py" in result
        assert "suggestion: Fix the import" in result

    def test_ensure_string_with_list(self) -> None:
        """Test that list items are joined with newlines."""
        input_list = ["First line", "Second line", "Third line"]
        result = ensure_string(input_list)
        assert result == "First line\nSecond line\nThird line"

    def test_ensure_string_with_number(self) -> None:
        """Test that numbers are converted to strings."""
        result = ensure_string(42)
        assert result == "42"


class TestBuildFailuresFromResponse:
    """Tests for the build_failures_from_response function."""

    def test_build_failures_with_dict_fix_suggestion(self) -> None:
        """Test that dict fix_suggestion is converted to string."""
        response_data = {
            "failures": [
                {
                    "test_name": "test_example",
                    "error": "AssertionError",
                    "classification": "code_issue",
                    "explanation": "Test failed",
                    "fix_suggestion": {
                        "file": "utilities/virtctl.py",
                        "suggestion": "Fix the logic that is not working properly.",
                    },
                }
            ]
        }
        failures = build_failures_from_response(response_data)
        assert len(failures) == 1
        assert failures[0].fix_suggestion is not None
        assert "file: utilities/virtctl.py" in failures[0].fix_suggestion
        assert "Fix the logic" in failures[0].fix_suggestion

    def test_build_failures_with_dict_explanation(self) -> None:
        """Test that dict explanation is converted to string."""
        response_data = {
            "failures": [
                {
                    "test_name": "test_example",
                    "error": "Error message",
                    "classification": "code_issue",
                    "explanation": {"cause": "Bug in code", "details": "More info"},
                }
            ]
        }
        failures = build_failures_from_response(response_data)
        assert len(failures) == 1
        assert "cause: Bug in code" in failures[0].explanation
        assert "details: More info" in failures[0].explanation

    def test_build_failures_with_dict_error(self) -> None:
        """Test that dict error is converted to string."""
        response_data = {
            "failures": [
                {
                    "test_name": "test_example",
                    "error": {"type": "AssertionError", "message": "Expected True"},
                    "classification": "code_issue",
                    "explanation": "Test failed",
                }
            ]
        }
        failures = build_failures_from_response(response_data)
        assert len(failures) == 1
        assert "type: AssertionError" in failures[0].error
        assert "message: Expected True" in failures[0].error

    def test_build_failures_with_dict_bug_report_fields(self) -> None:
        """Test that dict bug_report fields are converted to strings."""
        response_data = {
            "failures": [
                {
                    "test_name": "test_example",
                    "error": "Error",
                    "classification": "product_bug",
                    "explanation": "Product issue",
                    "bug_report": {
                        "title": {"summary": "Bug found", "component": "API"},
                        "description": {"what": "Broken", "where": "Service X"},
                        "severity": "high",
                        "evidence": ["Log line 1", "Log line 2"],
                    },
                }
            ]
        }
        failures = build_failures_from_response(response_data)
        assert len(failures) == 1
        assert failures[0].bug_report is not None
        assert "summary: Bug found" in failures[0].bug_report.title
        assert "what: Broken" in failures[0].bug_report.description

    def test_build_failures_with_string_values(self) -> None:
        """Test that normal string values work correctly."""
        response_data = {
            "failures": [
                {
                    "test_name": "test_normal",
                    "error": "Simple error message",
                    "classification": "code_issue",
                    "explanation": "Simple explanation",
                    "fix_suggestion": "Simple fix",
                }
            ]
        }
        failures = build_failures_from_response(response_data)
        assert len(failures) == 1
        assert failures[0].error == "Simple error message"
        assert failures[0].explanation == "Simple explanation"
        assert failures[0].fix_suggestion == "Simple fix"

    def test_build_failures_with_none_fix_suggestion(self) -> None:
        """Test that None fix_suggestion remains None."""
        response_data = {
            "failures": [
                {
                    "test_name": "test_no_suggestion",
                    "error": "Error",
                    "classification": "code_issue",
                    "explanation": "No fix available",
                }
            ]
        }
        failures = build_failures_from_response(response_data)
        assert len(failures) == 1
        assert failures[0].fix_suggestion is None
