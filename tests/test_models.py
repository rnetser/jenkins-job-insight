"""Tests for Pydantic models."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from jenkins_job_insight.models import (
    AnalysisResult,
    AnalyzeRequest,
    BugReport,
    FailureAnalysis,
    JobStatus,
)


class TestAnalyzeRequest:
    """Tests for the AnalyzeRequest model."""

    def test_analyze_request_creation(self) -> None:
        """Test creating a valid AnalyzeRequest."""
        request = AnalyzeRequest(
            job_name="test",
            build_number=123,
            tests_repo_url="https://github.com/example/repo",
        )
        assert request.job_name == "test"
        assert request.build_number == 123
        assert str(request.tests_repo_url) == "https://github.com/example/repo"
        assert request.callback_url is None
        assert request.callback_headers is None
        assert request.slack_webhook_url is None

    def test_analyze_request_with_optional_fields(self) -> None:
        """Test creating AnalyzeRequest with all optional fields."""
        request = AnalyzeRequest(
            job_name="test",
            build_number=123,
            tests_repo_url="https://github.com/example/repo",
            callback_url="https://callback.example.com/webhook",
            callback_headers={"Authorization": "Bearer token"},
            slack_webhook_url="https://hooks.slack.com/services/xxx",
        )
        assert request.callback_url is not None
        assert request.callback_headers == {"Authorization": "Bearer token"}
        assert request.slack_webhook_url is not None

    def test_analyze_request_without_tests_repo_url(self) -> None:
        """Test creating AnalyzeRequest without tests_repo_url (now optional)."""
        request = AnalyzeRequest(
            job_name="test",
            build_number=123,
        )
        assert request.job_name == "test"
        assert request.build_number == 123
        assert request.tests_repo_url is None

    def test_analyze_request_invalid_tests_repo_url(self) -> None:
        """Test that invalid repo URL raises ValidationError."""
        with pytest.raises(ValidationError):
            AnalyzeRequest(
                job_name="test",
                build_number=123,
                tests_repo_url="not-a-valid-url",
            )


class TestBugReport:
    """Tests for the BugReport model."""

    def test_bug_report_creation(self) -> None:
        """Test creating a valid BugReport."""
        report = BugReport(
            title="Test Bug",
            description="Bug description",
            severity="high",
            component="auth",
            evidence="Error log excerpt",
        )
        assert report.title == "Test Bug"
        assert report.severity == "high"

    @pytest.mark.parametrize("severity", ["critical", "high", "medium", "low"])
    def test_bug_report_valid_severities(self, severity: str) -> None:
        """Test that all valid severity values are accepted."""
        report = BugReport(
            title="Test",
            description="Desc",
            severity=severity,
            component="comp",
            evidence="evidence",
        )
        assert report.severity == severity

    def test_bug_report_invalid_severity(self) -> None:
        """Test that invalid severity raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            BugReport(
                title="Test",
                description="Desc",
                severity="invalid",
                component="comp",
                evidence="evidence",
            )
        errors = exc_info.value.errors()
        assert any("severity" in str(e) for e in errors)


class TestFailureAnalysis:
    """Tests for the FailureAnalysis model."""

    def test_failure_analysis_code_issue(self) -> None:
        """Test creating a code_issue FailureAnalysis."""
        analysis = FailureAnalysis(
            test_name="test_example",
            error="AssertionError",
            classification="code_issue",
            explanation="The test assertion is wrong",
            fix_suggestion="Update the expected value",
        )
        assert analysis.classification == "code_issue"
        assert analysis.fix_suggestion is not None
        assert analysis.bug_report is None

    def test_failure_analysis_product_bug(self, sample_bug_report: BugReport) -> None:
        """Test creating a product_bug FailureAnalysis."""
        analysis = FailureAnalysis(
            test_name="test_login",
            error="HTTP 500",
            classification="product_bug",
            explanation="Server error",
            bug_report=sample_bug_report,
        )
        assert analysis.classification == "product_bug"
        assert analysis.bug_report is not None
        assert analysis.fix_suggestion is None

    @pytest.mark.parametrize("classification", ["code_issue", "product_bug"])
    def test_failure_analysis_valid_classifications(self, classification: str) -> None:
        """Test that valid classification values are accepted."""
        analysis = FailureAnalysis(
            test_name="test",
            error="error",
            classification=classification,
            explanation="explanation",
        )
        assert analysis.classification == classification

    def test_failure_analysis_invalid_classification(self) -> None:
        """Test that invalid classification raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            FailureAnalysis(
                test_name="test",
                error="error",
                classification="invalid",
                explanation="explanation",
            )
        errors = exc_info.value.errors()
        assert any("classification" in str(e) for e in errors)


class TestAnalysisResult:
    """Tests for the AnalysisResult model."""

    def test_analysis_result_creation(self) -> None:
        """Test creating a valid AnalysisResult."""
        result = AnalysisResult(
            job_id="job-123",
            jenkins_url="https://jenkins.example.com/job/test/123/",
            status="completed",
            summary="Analysis complete",
            failures=[],
        )
        assert result.job_id == "job-123"
        assert result.status == "completed"
        assert result.failures == []

    def test_analysis_result_with_failures(
        self, sample_failure_analysis: FailureAnalysis
    ) -> None:
        """Test AnalysisResult with failure list."""
        result = AnalysisResult(
            job_id="job-123",
            jenkins_url="https://jenkins.example.com/job/test/123/",
            status="completed",
            summary="1 failure found",
            failures=[sample_failure_analysis],
        )
        assert len(result.failures) == 1
        assert result.failures[0].test_name == sample_failure_analysis.test_name

    @pytest.mark.parametrize("status", ["pending", "running", "completed", "failed"])
    def test_analysis_result_valid_statuses(self, status: str) -> None:
        """Test that valid status values are accepted."""
        result = AnalysisResult(
            job_id="job-123",
            jenkins_url="https://jenkins.example.com/job/test/123/",
            status=status,
            summary="Summary",
        )
        assert result.status == status

    def test_analysis_result_invalid_status(self) -> None:
        """Test that invalid status raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            AnalysisResult(
                job_id="job-123",
                jenkins_url="https://jenkins.example.com/job/test/123/",
                status="invalid",
                summary="Summary",
            )
        errors = exc_info.value.errors()
        assert any("status" in str(e) for e in errors)

    def test_analysis_result_default_failures(self) -> None:
        """Test that failures defaults to empty list."""
        result = AnalysisResult(
            job_id="job-123",
            jenkins_url="https://jenkins.example.com/job/test/123/",
            status="completed",
            summary="Summary",
        )
        assert result.failures == []


class TestJobStatus:
    """Tests for the JobStatus model."""

    def test_job_status_creation(self) -> None:
        """Test creating a valid JobStatus."""
        now = datetime.now()
        status = JobStatus(
            job_id="job-123",
            status="running",
            created_at=now,
        )
        assert status.job_id == "job-123"
        assert status.status == "running"
        assert status.created_at == now

    @pytest.mark.parametrize(
        "status_val", ["pending", "running", "completed", "failed"]
    )
    def test_job_status_valid_statuses(self, status_val: str) -> None:
        """Test that valid status values are accepted."""
        status = JobStatus(
            job_id="job-123",
            status=status_val,
            created_at=datetime.now(),
        )
        assert status.status == status_val

    def test_job_status_invalid_status(self) -> None:
        """Test that invalid status raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            JobStatus(
                job_id="job-123",
                status="invalid",
                created_at=datetime.now(),
            )
        errors = exc_info.value.errors()
        assert any("status" in str(e) for e in errors)
