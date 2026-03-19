"""Tests for bug creation module (AI content generation and external API calls)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from jenkins_job_insight.models import (
    AnalysisDetail,
    CodeFix,
    FailureAnalysis,
    ProductBugReport,
)


@pytest.fixture
def code_issue_failure() -> FailureAnalysis:
    """A CODE ISSUE failure with a code fix."""
    return FailureAnalysis(
        test_name="tests.auth.test_login.TestLogin.test_valid_credentials",
        error="AssertionError: Expected status 200, got 500",
        error_signature="abc123def456",  # pragma: allowlist secret
        analysis=AnalysisDetail(
            classification="CODE ISSUE",
            affected_tests=["tests.auth.test_login.TestLogin.test_valid_credentials"],
            details="The test fails because the login endpoint handler does not catch ValueError from the password validator.",
            code_fix=CodeFix(
                file="src/auth/handlers.py",
                line="42",
                change="Add try/except around password_validator.validate(password)",
            ),
        ),
    )


@pytest.fixture
def product_bug_failure() -> FailureAnalysis:
    """A PRODUCT BUG failure with a bug report."""
    return FailureAnalysis(
        test_name="tests.network.test_dns.TestDNS.test_resolve",
        error="TimeoutError: DNS resolution timed out after 30s",
        error_signature="xyz789ghi012",
        analysis=AnalysisDetail(
            classification="PRODUCT BUG",
            affected_tests=["tests.network.test_dns.TestDNS.test_resolve"],
            details="DNS resolution is failing intermittently on the internal resolver.",
            product_bug_report=ProductBugReport(
                title="DNS resolution timeout on internal resolver",
                severity="high",
                component="networking",
                description="Internal DNS resolver fails to resolve hostnames within 30s",
                evidence="TimeoutError at dns_client.py:88 - socket.timeout after 30000ms",
                jira_search_keywords=["DNS", "timeout", "resolver"],
            ),
        ),
    )


class TestGenerateGithubIssueContent:
    async def test_generates_title_and_body(self, code_issue_failure):
        from jenkins_job_insight.bug_creation import generate_github_issue_content

        with patch("jenkins_job_insight.bug_creation.call_ai_cli") as mock_ai:
            mock_ai.return_value = (
                True,
                json.dumps(
                    {
                        "title": "Fix: login handler missing ValueError catch",
                        "body": "## Test Failure\n\n**Test:** `tests.auth.test_login.TestLogin.test_valid_credentials`\n\n## Error\n\n```\nAssertionError: Expected status 200, got 500\n```\n\n## Analysis\n\nThe login endpoint handler does not catch ValueError.\n\n## Suggested Fix\n\n**File:** `src/auth/handlers.py` line 42\nAdd try/except around password_validator.",
                    }
                ),
            )

            result = await generate_github_issue_content(
                failure=code_issue_failure,
                report_url="https://jji.example.com/results/job-123.html",
                ai_provider="claude",
                ai_model="sonnet",
            )
            assert result["title"]
            assert result["body"]
            assert "test_valid_credentials" in result["body"]

    async def test_fallback_on_ai_failure(self, code_issue_failure):
        from jenkins_job_insight.bug_creation import generate_github_issue_content

        with patch("jenkins_job_insight.bug_creation.call_ai_cli") as mock_ai:
            mock_ai.return_value = (False, "AI CLI timed out")

            result = await generate_github_issue_content(
                failure=code_issue_failure,
                report_url="https://jji.example.com/results/job-123.html",
                ai_provider="claude",
                ai_model="sonnet",
            )
            # Should still return usable content built from failure data
            assert result["title"]
            assert result["body"]
            assert "test_valid_credentials" in result["body"]

    async def test_fallback_includes_code_fix(self, code_issue_failure):
        from jenkins_job_insight.bug_creation import generate_github_issue_content

        with patch("jenkins_job_insight.bug_creation.call_ai_cli") as mock_ai:
            mock_ai.return_value = (False, "AI CLI timed out")

            result = await generate_github_issue_content(
                failure=code_issue_failure,
                report_url="",
                ai_provider="claude",
                ai_model="sonnet",
            )
            assert "src/auth/handlers.py" in result["body"]
            assert "Suggested Fix" in result["body"]


class TestGenerateJiraBugContent:
    async def test_generates_summary_and_description(self, product_bug_failure):
        from jenkins_job_insight.bug_creation import generate_jira_bug_content

        with patch("jenkins_job_insight.bug_creation.call_ai_cli") as mock_ai:
            mock_ai.return_value = (
                True,
                json.dumps(
                    {
                        "title": "DNS resolution timeout on internal resolver",
                        "body": "h2. Summary\n\nDNS resolution is failing intermittently.\n\nh2. Evidence\n\nTimeoutError at dns_client.py:88",
                    }
                ),
            )

            result = await generate_jira_bug_content(
                failure=product_bug_failure,
                report_url="https://jji.example.com/results/job-456.html",
                ai_provider="claude",
                ai_model="sonnet",
            )
            assert result["title"]
            assert result["body"]

    async def test_fallback_on_ai_failure(self, product_bug_failure):
        from jenkins_job_insight.bug_creation import generate_jira_bug_content

        with patch("jenkins_job_insight.bug_creation.call_ai_cli") as mock_ai:
            mock_ai.return_value = (False, "error")

            result = await generate_jira_bug_content(
                failure=product_bug_failure,
                report_url="",
                ai_provider="claude",
                ai_model="sonnet",
            )
            assert result["title"] == "DNS resolution timeout on internal resolver"
            assert "test_resolve" in result["body"]


class TestSearchGithubDuplicates:
    async def test_finds_similar_issues(self):
        from jenkins_job_insight.bug_creation import search_github_duplicates

        mock_response = httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "number": 42,
                        "title": "Login fails with valid credentials",
                        "html_url": "https://github.com/org/repo/issues/42",
                        "state": "open",
                    }
                ],
            },
        )
        with patch("jenkins_job_insight.bug_creation.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            results = await search_github_duplicates(
                title="Login fails with valid credentials",
                repo_url="https://github.com/org/repo",
                github_token="ghp_test",
            )
            assert len(results) == 1
            assert results[0]["number"] == 42

    async def test_returns_empty_on_error(self):
        from jenkins_job_insight.bug_creation import search_github_duplicates

        with patch("jenkins_job_insight.bug_creation.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.RequestError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            results = await search_github_duplicates(
                title="Login fails",
                repo_url="https://github.com/org/repo",
                github_token="ghp_test",
            )
            assert results == []

    async def test_returns_empty_on_bad_url(self):
        from jenkins_job_insight.bug_creation import search_github_duplicates

        results = await search_github_duplicates(
            title="Login fails",
            repo_url="not-a-github-url",
            github_token="ghp_test",
        )
        assert results == []


def _mock_request() -> httpx.Request:
    """Create a dummy httpx.Request for use in mock responses."""
    return httpx.Request("POST", "https://example.com")


class TestCreateGithubIssue:
    async def test_creates_issue(self):
        from jenkins_job_insight.bug_creation import create_github_issue

        mock_response = httpx.Response(
            201,
            json={
                "number": 99,
                "title": "Bug: login fails",
                "html_url": "https://github.com/org/repo/issues/99",
            },
            request=_mock_request(),
        )
        with patch("jenkins_job_insight.bug_creation.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await create_github_issue(
                title="Bug: login fails",
                body="## Details\nLogin returns 500",
                repo_url="https://github.com/org/repo",
                github_token="ghp_test",
            )
            assert result["url"] == "https://github.com/org/repo/issues/99"
            assert result["number"] == 99

    async def test_creates_issue_with_labels(self):
        from jenkins_job_insight.bug_creation import create_github_issue

        mock_response = httpx.Response(
            201,
            json={
                "number": 100,
                "title": "Bug: login fails",
                "html_url": "https://github.com/org/repo/issues/100",
            },
            request=_mock_request(),
        )
        with patch("jenkins_job_insight.bug_creation.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await create_github_issue(
                title="Bug: login fails",
                body="Details",
                repo_url="https://github.com/org/repo",
                github_token="ghp_test",
                labels=["bug", "test-failure"],
            )
            assert result["number"] == 100


class TestCreateJiraBug:
    async def test_creates_bug(self):
        from jenkins_job_insight.bug_creation import create_jira_bug

        mock_settings = MagicMock()
        mock_settings.jira_url = "https://jira.example.com"
        mock_settings.jira_project_key = "PROJ"
        mock_settings.jira_email = "test@example.com"
        mock_settings.jira_api_token = MagicMock()
        mock_settings.jira_api_token.get_secret_value.return_value = "token123"
        mock_settings.jira_pat = None
        mock_settings.jira_ssl_verify = True

        mock_response = httpx.Response(
            201,
            json={
                "key": "PROJ-456",
                "self": "https://jira.example.com/rest/api/3/issue/10001",
            },
            request=_mock_request(),
        )
        with patch("jenkins_job_insight.bug_creation.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await create_jira_bug(
                title="DNS timeout on resolver",
                body="DNS resolution fails intermittently",
                settings=mock_settings,
            )
            assert result["key"] == "PROJ-456"
            assert "jira.example.com" in result["url"]

    async def test_creates_bug_server_dc(self):
        """Test Jira Server/DC auth (Bearer PAT, no email)."""
        from jenkins_job_insight.bug_creation import create_jira_bug

        mock_settings = MagicMock()
        mock_settings.jira_url = "https://jira-server.example.com"
        mock_settings.jira_project_key = "PROJ"
        mock_settings.jira_email = None
        mock_settings.jira_api_token = None
        mock_settings.jira_pat = MagicMock()
        mock_settings.jira_pat.get_secret_value.return_value = "pat123"
        mock_settings.jira_ssl_verify = False

        mock_response = httpx.Response(
            201,
            json={"key": "PROJ-789"},
            request=_mock_request(),
        )
        with patch("jenkins_job_insight.bug_creation.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await create_jira_bug(
                title="DNS timeout",
                body="Description",
                settings=mock_settings,
            )
            assert result["key"] == "PROJ-789"


class TestParseGithubRepoUrl:
    def test_standard_url(self):
        from jenkins_job_insight.bug_creation import _parse_github_repo_url

        owner, repo = _parse_github_repo_url("https://github.com/myorg/myrepo")
        assert owner == "myorg"
        assert repo == "myrepo"

    def test_url_with_git_suffix(self):
        from jenkins_job_insight.bug_creation import _parse_github_repo_url

        owner, repo = _parse_github_repo_url("https://github.com/myorg/myrepo.git")
        assert owner == "myorg"
        assert repo == "myrepo"

    def test_url_with_dots_in_repo_name(self):
        """Finding 5: Repo names with dots should be parsed correctly."""
        from jenkins_job_insight.bug_creation import _parse_github_repo_url

        owner, repo = _parse_github_repo_url("https://github.com/org/my.repo")
        assert owner == "org"
        assert repo == "my.repo"

    def test_url_with_dots_and_git_suffix(self):
        """Finding 5: Repo names with dots AND .git suffix."""
        from jenkins_job_insight.bug_creation import _parse_github_repo_url

        owner, repo = _parse_github_repo_url("https://github.com/org/my.repo.git")
        assert owner == "org"
        assert repo == "my.repo"

    def test_invalid_url(self):
        from jenkins_job_insight.bug_creation import _parse_github_repo_url

        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_github_repo_url("not-a-url")


class TestBuildFailbackContent:
    def test_github_fallback_with_product_bug(self, product_bug_failure):
        from jenkins_job_insight.bug_creation import (
            _build_failure_context,
            _build_fallback_github_content,
        )

        ctx = _build_failure_context(product_bug_failure)
        result = _build_fallback_github_content(
            ctx, "https://jenkins.example.com/job/1", ""
        )
        assert result["title"] == "DNS resolution timeout on internal resolver"
        assert "test_resolve" in result["body"]

    def test_jira_fallback_with_product_bug(self, product_bug_failure):
        from jenkins_job_insight.bug_creation import (
            _build_failure_context,
            _build_fallback_jira_content,
        )

        ctx = _build_failure_context(product_bug_failure)
        result = _build_fallback_jira_content(ctx, "", "https://report.example.com")
        assert result["title"] == "DNS resolution timeout on internal resolver"
        assert "h2." in result["body"]

    def test_github_fallback_without_product_bug(self, code_issue_failure):
        from jenkins_job_insight.bug_creation import (
            _build_failure_context,
            _build_fallback_github_content,
        )

        ctx = _build_failure_context(code_issue_failure)
        result = _build_fallback_github_content(ctx, "", "")
        assert "test_valid_credentials" in result["title"]
