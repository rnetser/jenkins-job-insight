"""Tests for Pydantic models."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from jenkins_job_insight.models import (
    AnalysisDetail,
    AnalysisResult,
    AnalyzeRequest,
    CodeFix,
    CreateIssueRequest,
    CreateIssueResponse,
    FailureAnalysis,
    JiraMatch,
    JobStatus,
    OverrideClassificationRequest,
    PreviewIssueRequest,
    PreviewIssueResponse,
    ProductBugReport,
    SimilarIssue,
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

    def test_analyze_request_with_optional_fields(self) -> None:
        """Test creating AnalyzeRequest with all optional fields."""
        request = AnalyzeRequest(
            job_name="test",
            build_number=123,
            tests_repo_url="https://github.com/example/repo",
            callback_url="https://callback.example.com/webhook",
            callback_headers={"Authorization": "Bearer token"},
        )
        assert request.callback_url is not None
        assert request.callback_headers == {"Authorization": "Bearer token"}

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


class TestProductBugReport:
    """Tests for the ProductBugReport model."""

    def test_creation_with_defaults(self) -> None:
        """Test creating with all defaults."""
        report = ProductBugReport()
        assert report.title == ""
        assert report.severity == ""
        assert report.component == ""

    def test_creation_with_values(self) -> None:
        """Test creating with all fields."""
        report = ProductBugReport(
            title="Bug title",
            severity="high",
            component="auth",
            description="Something is broken",
            evidence="Stack trace here",
        )
        assert report.title == "Bug title"
        assert report.severity == "high"


class TestCodeFix:
    """Tests for the CodeFix model."""

    def test_creation_with_defaults(self) -> None:
        """Test creating with all defaults."""
        fix = CodeFix()
        assert fix.file == ""
        assert fix.line == ""
        assert fix.change == ""

    def test_creation_with_values(self) -> None:
        """Test creating with all fields."""
        fix = CodeFix(file="src/main.py", line="42", change="Fix the bug")
        assert fix.file == "src/main.py"
        assert fix.line == "42"


class TestAnalysisDetail:
    """Tests for the AnalysisDetail model."""

    def test_creation_with_defaults(self) -> None:
        """Test creating with all defaults."""
        detail = AnalysisDetail()
        assert detail.classification == ""
        assert detail.affected_tests == []
        assert detail.details == ""
        assert detail.code_fix is False
        assert detail.product_bug_report is False

    def test_creation_with_code_fix(self) -> None:
        """Test creating with a code fix."""
        detail = AnalysisDetail(
            classification="CODE ISSUE",
            details="Missing import",
            code_fix=CodeFix(file="test.py", line="1", change="Add import"),
        )
        assert detail.code_fix
        assert detail.code_fix.file == "test.py"
        assert not detail.product_bug_report

    def test_creation_with_bug_report(self) -> None:
        """Test creating with a product bug report."""
        detail = AnalysisDetail(
            classification="PRODUCT BUG",
            details="API broken",
            product_bug_report=ProductBugReport(title="API bug", severity="high"),
        )
        assert detail.product_bug_report
        assert detail.product_bug_report.title == "API bug"
        assert not detail.code_fix


class TestFailureAnalysis:
    """Tests for the FailureAnalysis model."""

    def test_failure_analysis_creation(self) -> None:
        """Test creating a valid FailureAnalysis."""
        analysis = FailureAnalysis(
            test_name="test_example",
            error="AssertionError: Expected True, got False",
            analysis=AnalysisDetail(
                classification="CODE ISSUE",
                details="The test assertion is wrong",
            ),
        )
        assert analysis.test_name == "test_example"
        assert analysis.error == "AssertionError: Expected True, got False"
        assert analysis.analysis.classification == "CODE ISSUE"

    def test_failure_analysis_with_full_detail(self) -> None:
        """Test FailureAnalysis with full AnalysisDetail content."""
        analysis = FailureAnalysis(
            test_name="test_login",
            error="HTTP 500 Internal Server Error",
            analysis=AnalysisDetail(
                classification="PRODUCT BUG",
                affected_tests=["test_login"],
                details="The authentication service is failing with a 500 error.",
                product_bug_report=ProductBugReport(
                    title="Authentication fails with valid credentials",
                    severity="high",
                    component="auth",
                    description="Users cannot log in",
                    evidence="HTTP 500 response",
                ),
            ),
        )
        assert analysis.test_name == "test_login"
        assert analysis.analysis.classification == "PRODUCT BUG"
        assert analysis.analysis.product_bug_report
        assert (
            analysis.analysis.product_bug_report.title
            == "Authentication fails with valid credentials"
        )

    def test_failure_analysis_required_fields(self) -> None:
        """Test that all required fields must be provided."""
        with pytest.raises(ValidationError):
            FailureAnalysis(
                test_name="test_example",
                # missing error and analysis
            )

        with pytest.raises(ValidationError):
            FailureAnalysis(
                test_name="test_example",
                error="Error message",
                # missing analysis
            )


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


class TestJiraMatch:
    """Tests for the JiraMatch model."""

    def test_creation_with_required_fields(self) -> None:
        """Test creating with only required fields."""
        match = JiraMatch(key="PROJ-123", summary="Bug title")
        assert match.key == "PROJ-123"
        assert match.summary == "Bug title"
        assert match.status == ""
        assert match.priority == ""
        assert match.url == ""
        assert match.score == 0.0

    def test_creation_with_all_fields(self) -> None:
        """Test creating with all fields."""
        match = JiraMatch(
            key="PROJ-456",
            summary="Login fails",
            status="Open",
            priority="High",
            url="https://jira.example.com/browse/PROJ-456",
            score=0.85,
        )
        assert match.key == "PROJ-456"
        assert match.status == "Open"
        assert match.score == 0.85


class TestProductBugReportJiraFields:
    """Tests for Jira-related fields on ProductBugReport."""

    def test_defaults_to_empty_lists(self) -> None:
        """Jira fields default to empty lists for backward compatibility."""
        report = ProductBugReport()
        assert report.jira_search_keywords == []
        assert report.jira_matches == []

    def test_with_search_keywords(self) -> None:
        """Test creating with search keywords."""
        report = ProductBugReport(
            title="Bug",
            jira_search_keywords=["login", "auth"],
        )
        assert report.jira_search_keywords == ["login", "auth"]

    def test_with_jira_matches(self) -> None:
        """Test creating with Jira matches."""
        matches = [
            JiraMatch(key="PROJ-1", summary="Match 1"),
            JiraMatch(key="PROJ-2", summary="Match 2"),
        ]
        report = ProductBugReport(
            title="Bug",
            jira_matches=matches,
        )
        assert len(report.jira_matches) == 2
        assert report.jira_matches[0].key == "PROJ-1"

    def test_serialization_includes_jira_fields(self) -> None:
        """Test that Jira fields are included in serialization."""
        report = ProductBugReport(
            title="Bug",
            jira_search_keywords=["kw1"],
            jira_matches=[JiraMatch(key="PROJ-1", summary="Match")],
        )
        data = report.model_dump()
        assert "jira_search_keywords" in data
        assert "jira_matches" in data
        assert len(data["jira_matches"]) == 1


class TestPreviewIssueRequest:
    """Tests for the PreviewIssueRequest model."""

    def test_valid_request(self) -> None:
        """Test creating a valid PreviewIssueRequest."""
        req = PreviewIssueRequest(test_name="tests.TestFoo.test_bar")
        assert req.test_name == "tests.TestFoo.test_bar"
        assert req.child_job_name == ""
        assert req.child_build_number == 0

    def test_child_job_fields_validation(self) -> None:
        """Test that child_job_name requires child_build_number."""
        with pytest.raises(ValueError, match="child_build_number must be positive"):
            PreviewIssueRequest(
                test_name="tests.TestFoo.test_bar",
                child_job_name="child-job",
                child_build_number=0,
            )


class TestCreateIssueRequest:
    """Tests for the CreateIssueRequest model."""

    def test_valid_request(self) -> None:
        """Test creating a valid CreateIssueRequest."""
        req = CreateIssueRequest(
            test_name="tests.TestFoo.test_bar",
            title="Bug: login fails",
            body="## Details\nLogin returns 500",
        )
        assert req.title == "Bug: login fails"
        assert req.body == "## Details\nLogin returns 500"

    def test_title_required(self) -> None:
        """Test that empty title is rejected."""
        with pytest.raises(ValueError):
            CreateIssueRequest(
                test_name="tests.TestFoo.test_bar",
                title="",
                body="some body",
            )

    def test_whitespace_only_title_rejected(self) -> None:
        """Test that whitespace-only title is rejected."""
        with pytest.raises(ValueError):
            CreateIssueRequest(
                test_name="tests.TestFoo.test_bar",
                title="   ",
                body="some body",
            )


class TestOverrideClassificationRequest:
    """Tests for the OverrideClassificationRequest model."""

    def test_valid_code_issue(self) -> None:
        """Test CODE ISSUE classification."""
        req = OverrideClassificationRequest(
            test_name="tests.TestFoo.test_bar",
            classification="CODE ISSUE",
        )
        assert req.classification == "CODE ISSUE"

    def test_valid_product_bug(self) -> None:
        """Test PRODUCT BUG classification."""
        req = OverrideClassificationRequest(
            test_name="tests.TestFoo.test_bar",
            classification="PRODUCT BUG",
        )
        assert req.classification == "PRODUCT BUG"

    def test_invalid_classification(self) -> None:
        """Test that invalid classification is rejected."""
        with pytest.raises(ValueError):
            OverrideClassificationRequest(
                test_name="tests.TestFoo.test_bar",
                classification="UNKNOWN",
            )


class TestSimilarIssue:
    """Tests for the SimilarIssue model."""

    def test_defaults(self) -> None:
        """Test default values."""
        issue = SimilarIssue()
        assert issue.number is None
        assert issue.key == ""
        assert issue.title == ""
        assert issue.url == ""
        assert issue.status == ""

    def test_github_style(self) -> None:
        """Test GitHub-style similar issue."""
        issue = SimilarIssue(
            number=42,
            title="Login fails",
            url="https://github.com/org/repo/issues/42",
            status="open",
        )
        assert issue.number == 42
        assert issue.title == "Login fails"

    def test_jira_style(self) -> None:
        """Test Jira-style similar issue."""
        issue = SimilarIssue(
            key="PROJ-123",
            title="DNS timeout",
            url="https://jira.example.com/browse/PROJ-123",
            status="Open",
        )
        assert issue.key == "PROJ-123"


class TestPreviewIssueResponse:
    """Tests for the PreviewIssueResponse model."""

    def test_creation(self) -> None:
        """Test creating a PreviewIssueResponse."""
        resp = PreviewIssueResponse(
            title="Bug title",
            body="## Details",
            similar_issues=[SimilarIssue(number=1, title="Similar")],
        )
        assert resp.title == "Bug title"
        assert len(resp.similar_issues) == 1

    def test_empty_similar_issues(self) -> None:
        """Test that similar_issues defaults to empty list."""
        resp = PreviewIssueResponse(title="Bug", body="Details")
        assert resp.similar_issues == []


class TestCreateIssueResponse:
    """Tests for the CreateIssueResponse model."""

    def test_creation(self) -> None:
        """Test creating a CreateIssueResponse."""
        resp = CreateIssueResponse(
            url="https://github.com/org/repo/issues/99",
            title="Bug fix",
            comment_id=42,
        )
        assert resp.url == "https://github.com/org/repo/issues/99"
        assert resp.key == ""
        assert resp.comment_id == 42

    def test_jira_response(self) -> None:
        """Test Jira-style CreateIssueResponse."""
        resp = CreateIssueResponse(
            url="https://jira.example.com/browse/PROJ-456",
            key="PROJ-456",
            title="DNS timeout",
        )
        assert resp.key == "PROJ-456"
        assert resp.comment_id == 0
