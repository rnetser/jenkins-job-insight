"""Shared fixtures for jenkins-job-insight tests."""

import os
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

from jenkins_job_insight.config import Settings
from jenkins_job_insight.models import (
    AnalysisResult,
    AnalyzeRequest,
    BugReport,
    FailureAnalysis,
)


@pytest.fixture
def mock_env_vars() -> Generator[dict[str, str], None, None]:
    """Provide minimal environment variables for Settings."""
    env = {
        "JENKINS_URL": "https://jenkins.example.com",
        "JENKINS_USER": "testuser",
        "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
    }
    with patch.dict(os.environ, env, clear=False):
        yield env


@pytest.fixture
def full_env_vars() -> Generator[dict[str, str], None, None]:
    """Provide full environment variables including AI config."""
    env = {
        "JENKINS_URL": "https://jenkins.example.com",
        "JENKINS_USER": "testuser",
        "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
        "GEMINI_API_KEY": "test-gemini-key",  # pragma: allowlist secret
        "SLACK_WEBHOOK_URL": "https://hooks.slack.com/test",
    }
    with patch.dict(os.environ, env, clear=False):
        yield env


@pytest.fixture
def settings(mock_env_vars: dict[str, str]) -> Settings:
    """Create Settings instance with mocked environment."""
    return Settings()


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    """Create a temporary database path for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def sample_analyze_request() -> AnalyzeRequest:
    """Create a sample analyze request for testing."""
    return AnalyzeRequest(
        job_name="my-job",
        build_number=123,
        tests_repo_url="https://github.com/example/repo",
    )


@pytest.fixture
def sample_bug_report() -> BugReport:
    """Create a sample bug report for testing."""
    return BugReport(
        title="Login fails with valid credentials",
        description="Users cannot log in even with correct username and password",
        severity="high",
        component="auth",
        evidence="Error: Authentication service returned 500",
    )


@pytest.fixture
def sample_failure_analysis(sample_bug_report: BugReport) -> FailureAnalysis:
    """Create a sample failure analysis for testing."""
    return FailureAnalysis(
        test_name="test_login_success",
        error="AssertionError: Expected 200, got 500",
        classification="product_bug",
        explanation="The authentication service is returning an error",
        fix_suggestion=None,
        bug_report=sample_bug_report,
    )


@pytest.fixture
def sample_analysis_result(
    sample_failure_analysis: FailureAnalysis,
) -> AnalysisResult:
    """Create a sample analysis result for testing."""
    return AnalysisResult(
        job_id="test-job-123",
        jenkins_url="https://jenkins.example.com/job/my-job/123/",
        status="completed",
        summary="1 failure analyzed: 1 product bug found",
        failures=[sample_failure_analysis],
    )


@pytest.fixture
def mock_jenkins_client() -> MagicMock:
    """Create a mock Jenkins client."""
    mock = MagicMock()
    mock.get_build_console.return_value = (
        "Build started\nTest failed: test_example\nBuild finished"
    )
    mock.get_build_info_safe.return_value = {
        "result": "FAILURE",
        "building": False,
        "number": 123,
    }
    return mock


@pytest.fixture
def mock_ai_client() -> MagicMock:
    """Create a mock AI client."""
    mock = MagicMock()
    mock.analyze.return_value = """{
        "summary": "1 failure found",
        "failures": [
            {
                "test_name": "test_example",
                "error": "AssertionError",
                "classification": "code_issue",
                "explanation": "Test assertion is incorrect",
                "fix_suggestion": "Update the assertion"
            }
        ]
    }"""
    return mock
