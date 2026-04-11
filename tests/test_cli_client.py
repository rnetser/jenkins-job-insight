"""Tests for the JJI CLI client."""

import json
from unittest.mock import patch

import httpx
import pytest

from jenkins_job_insight.cli.client import JJIClient, JJIError

BASE_URL = "http://localhost:8700"


def _make_client(handler, username: str = "") -> JJIClient:
    """Create a JJIClient with a mock transport for testing.

    The mock httpx.Client is created with base_url set so that
    relative paths (e.g. "/health") resolve correctly.
    """
    cookies = {}
    if username:
        cookies["jji_username"] = username

    mock_http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=BASE_URL,
        cookies=cookies,
    )
    client = JJIClient(BASE_URL, username=username)
    client._client.close()
    client._client = mock_http
    return client


class TestJJIError:
    def test_error_stores_status_and_detail(self):
        err = JJIError(status_code=404, detail="Job not found")
        assert err.status_code == 404
        assert err.detail == "Job not found"
        assert "404" in str(err)
        assert "Job not found" in str(err)

    def test_error_without_detail(self):
        err = JJIError(status_code=500)
        assert err.status_code == 500
        assert err.detail == ""


class TestJJIClientHealth:
    def test_health(self):
        client = _make_client(
            lambda request: httpx.Response(200, json={"status": "healthy"})
        )
        result = client.health()
        assert result == {"status": "healthy"}

    def test_health_server_down(self):
        def raise_connect_error(request):
            raise httpx.ConnectError("Connection refused")

        client = _make_client(raise_connect_error)
        with pytest.raises(JJIError) as exc_info:
            client.health()
        assert exc_info.value.status_code == 0


class TestJJIClientResults:
    def test_list_results(self):
        sample_results = [
            {
                "job_id": "abc-123",
                "status": "completed",
                "jenkins_url": "https://jenkins.example.com/job/test/1/",
            },
            {
                "job_id": "def-456",
                "status": "running",
                "jenkins_url": "https://jenkins.example.com/job/test/2/",
            },
        ]
        client = _make_client(lambda request: httpx.Response(200, json=sample_results))
        result = client.list_results(limit=50)
        assert len(result) == 2
        assert result[0]["job_id"] == "abc-123"

    def test_get_result(self):
        sample = {
            "job_id": "abc-123",
            "status": "completed",
            "result": {"summary": "All good"},
        }
        client = _make_client(lambda request: httpx.Response(200, json=sample))
        result = client.get_result("abc-123")
        assert result["job_id"] == "abc-123"

    def test_get_result_not_found(self):
        client = _make_client(
            lambda request: httpx.Response(404, json={"detail": "Job not found"})
        )
        with pytest.raises(JJIError) as exc_info:
            client.get_result("nonexistent")
        assert exc_info.value.status_code == 404

    def test_delete_job(self):
        client = _make_client(
            lambda request: httpx.Response(
                200, json={"status": "deleted", "job_id": "abc-123"}
            )
        )
        result = client.delete_job("abc-123")
        assert result["status"] == "deleted"


class TestJJIClientDashboard:
    def test_dashboard(self):
        """Test dashboard returns all jobs."""

        def dashboard_handler(request):
            assert request.method == "GET"
            assert request.url.path == "/api/dashboard"
            return httpx.Response(
                200, json=[{"job_id": "test-1", "status": "completed"}]
            )

        client = _make_client(dashboard_handler)
        result = client.dashboard()
        assert len(result) == 1
        assert result[0]["job_id"] == "test-1"


class TestJJIClientAnalyze:
    def test_analyze(self):
        response_data = {
            "status": "queued",
            "job_id": "new-job-1",
            "message": "Analysis job queued.",
        }

        def handler(request):
            body = _parse_analyze_request(request)
            assert "sync" not in body
            return httpx.Response(202, json=response_data)

        client = _make_client(handler)
        result = client.analyze("my-job", 42)
        assert result["status"] == "queued"
        assert result["job_id"] == "new-job-1"


class TestJJIClientHistory:
    def test_get_test_history(self):
        sample = {"test_name": "tests.TestA.test_one", "failures": 3, "recent_runs": []}

        def handler(request):
            assert request.method == "GET"
            assert "/history/test/tests.TestA.test_one" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.get_test_history("tests.TestA.test_one")
        assert result["test_name"] == "tests.TestA.test_one"

    def test_search_by_signature(self):
        sample = {"signature": "sig-abc", "total_occurrences": 5, "tests": []}

        def handler(request):
            assert request.method == "GET"
            assert "signature=sig-abc" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.search_by_signature("sig-abc")
        assert result["signature"] == "sig-abc"

    def test_get_job_stats(self):
        sample = {"job_name": "ocp-e2e", "total_builds_analyzed": 10}

        def handler(request):
            assert request.method == "GET"
            assert "/history/stats/ocp-e2e" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.get_job_stats("ocp-e2e")
        assert result["job_name"] == "ocp-e2e"

    def test_get_all_failures(self):
        sample = {"failures": [], "total": 0, "limit": 50, "offset": 0}

        def handler(request):
            assert request.method == "GET"
            assert "/history/failures" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.get_all_failures()
        assert "failures" in result


class TestJJIClientClassifications:
    def test_classify_test(self):
        response_data = {"id": 1}

        def handler(request):
            assert request.method == "POST"
            return httpx.Response(201, json=response_data)

        client = _make_client(handler)
        result = client.classify_test(
            test_name="tests.TestA.test_one",
            classification="FLAKY",
            reason="intermittent DNS",
            job_id="job-123",
        )
        assert result["id"] == 1

    def test_get_classifications(self):
        sample = {
            "classifications": [
                {"test_name": "tests.TestA.test_one", "classification": "FLAKY"}
            ]
        }

        def handler(request):
            assert request.method == "GET"
            assert "/history/classifications" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.get_classifications()
        assert "classifications" in result


class TestJJIClientComments:
    def test_get_comments(self):
        sample = {"comments": [], "reviews": {}}

        def handler(request):
            assert request.method == "GET"
            assert "/results/job-1/comments" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.get_comments("job-1")
        assert "comments" in result

    def test_add_comment(self):
        response_data = {"id": 42}

        def handler(request):
            assert request.method == "POST"
            return httpx.Response(201, json=response_data)

        client = _make_client(handler)
        result = client.add_comment(
            job_id="job-1",
            test_name="tests.TestA.test_one",
            comment="Opened JIRA-123",
        )
        assert result["id"] == 42

    def test_delete_comment(self):
        def handler(request):
            assert request.method == "DELETE"
            return httpx.Response(200, json={"status": "deleted"})

        client = _make_client(handler)
        result = client.delete_comment("job-1", 42)
        assert result["status"] == "deleted"


class TestJJIClientTimeout:
    def test_timeout_raises_error(self):
        def raise_timeout(request):
            raise httpx.ReadTimeout("Read timed out")

        client = _make_client(raise_timeout)
        with pytest.raises(JJIError) as exc_info:
            client.health()
        assert exc_info.value.status_code == 0
        assert "timed out" in exc_info.value.detail.lower()


class TestJJIClientUsername:
    def test_username_sent_as_cookie(self):
        def check_cookie(request):
            assert request.headers.get("cookie") is not None
            assert "jji_username=testuser" in request.headers.get("cookie", "")
            return httpx.Response(200, json={"status": "deleted", "job_id": "abc"})

        client = _make_client(check_cookie, username="testuser")
        client.delete_job("abc")


class TestMalformedUrl:
    def test_malformed_url_error(self):
        """Malformed URLs should raise JJIError, not raw httpx exception."""
        client = JJIClient(server_url="not-a-valid-url")
        with pytest.raises(JJIError) as exc_info:
            client.health()
        assert exc_info.value.status_code == 0


class TestJJIClientBugCreation:
    def test_preview_github_issue(self):
        response_data = {
            "title": "Fix: login handler",
            "body": "## Details...",
            "similar_issues": [],
        }

        def handler(request):
            assert request.method == "POST"
            assert "/preview-github-issue" in str(request.url)
            return httpx.Response(200, json=response_data)

        client = _make_client(handler)
        result = client.preview_github_issue(
            job_id="job-1",
            test_name="tests.TestA.test_one",
        )
        assert result["title"] == "Fix: login handler"

    def test_preview_jira_bug(self):
        response_data = {
            "title": "DNS timeout",
            "body": "h2. Summary...",
            "similar_issues": [],
        }

        def handler(request):
            assert request.method == "POST"
            assert "/preview-jira-bug" in str(request.url)
            return httpx.Response(200, json=response_data)

        client = _make_client(handler)
        result = client.preview_jira_bug(
            job_id="job-1",
            test_name="tests.TestA.test_one",
        )
        assert result["title"] == "DNS timeout"

    def test_create_github_issue(self):
        response_data = {
            "url": "https://github.com/org/repo/issues/99",
            "key": "",
            "title": "Bug: login fails",
            "comment_id": 42,
        }

        def handler(request):
            assert request.method == "POST"
            assert "/create-github-issue" in str(request.url)
            return httpx.Response(201, json=response_data)

        client = _make_client(handler)
        result = client.create_github_issue(
            job_id="job-1",
            test_name="tests.TestA.test_one",
            title="Bug: login fails",
            body="Details...",
        )
        assert result["url"] == "https://github.com/org/repo/issues/99"

    def test_create_jira_bug(self):
        response_data = {
            "url": "https://jira.example.com/browse/PROJ-456",
            "key": "PROJ-456",
            "title": "DNS timeout",
            "comment_id": 43,
        }

        def handler(request):
            assert request.method == "POST"
            assert "/create-jira-bug" in str(request.url)
            return httpx.Response(201, json=response_data)

        client = _make_client(handler)
        result = client.create_jira_bug(
            job_id="job-1",
            test_name="tests.TestA.test_one",
            title="DNS timeout",
            body="Description...",
        )
        assert result["key"] == "PROJ-456"

    def test_override_classification(self):
        def handler(request):
            assert request.method == "PUT"
            assert "/override-classification" in str(request.url)
            return httpx.Response(
                200, json={"status": "ok", "classification": "PRODUCT BUG"}
            )

        client = _make_client(handler)
        result = client.override_classification(
            job_id="job-1",
            test_name="tests.TestA.test_one",
            classification="PRODUCT BUG",
        )
        assert result["classification"] == "PRODUCT BUG"

    def test_preview_github_issue_with_child_job(self):
        def handler(request):
            body = json.loads(request.content)
            assert body["child_job_name"] == "child-runner"
            assert body["child_build_number"] == 5
            return httpx.Response(
                200,
                json={"title": "Fix", "body": "Body", "similar_issues": []},
            )

        client = _make_client(handler)
        result = client.preview_github_issue(
            job_id="job-1",
            test_name="tests.TestA.test_one",
            child_job_name="child-runner",
            child_build_number=5,
        )
        assert result["title"] == "Fix"


class TestJJIClientCapabilities:
    def test_capabilities(self):
        def handler(request):
            assert request.method == "GET"
            assert request.url.path == "/api/capabilities"
            return httpx.Response(200, json={"github_issues": True, "jira_bugs": False})

        client = _make_client(handler)
        result = client.capabilities()
        assert result["github_issues"] is True
        assert result["jira_bugs"] is False


class TestJJIClientAiConfigs:
    def test_get_ai_configs(self):
        sample = [
            {"ai_provider": "claude", "ai_model": "opus-4"},
            {"ai_provider": "gemini", "ai_model": "2.5-pro"},
        ]

        def handler(request):
            assert request.method == "GET"
            assert "/ai-configs" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.get_ai_configs()
        assert len(result) == 2
        assert result[0]["ai_provider"] == "claude"

    def test_get_ai_configs_empty(self):
        def handler(request):
            return httpx.Response(200, json=[])

        client = _make_client(handler)
        result = client.get_ai_configs()
        assert result == []


class TestJJIClientPreviewWithAiConfig:
    def test_preview_github_issue_with_ai_config(self):
        def handler(request):
            body = json.loads(request.content)
            assert body["ai_provider"] == "claude"
            assert body["ai_model"] == "opus-4"
            return httpx.Response(
                200,
                json={"title": "Fix", "body": "Body", "similar_issues": []},
            )

        client = _make_client(handler)
        result = client.preview_github_issue(
            job_id="job-1",
            test_name="tests.TestA.test_one",
            ai_provider="claude",
            ai_model="opus-4",
        )
        assert result["title"] == "Fix"

    def test_preview_jira_bug_with_ai_config(self):
        def handler(request):
            body = json.loads(request.content)
            assert body["ai_provider"] == "gemini"
            assert body["ai_model"] == "2.5-pro"
            return httpx.Response(
                200,
                json={"title": "Bug", "body": "Desc", "similar_issues": []},
            )

        client = _make_client(handler)
        result = client.preview_jira_bug(
            job_id="job-1",
            test_name="tests.TestA.test_one",
            ai_provider="gemini",
            ai_model="2.5-pro",
        )
        assert result["title"] == "Bug"

    def test_preview_github_issue_without_ai_config_omits_fields(self):
        def handler(request):
            body = json.loads(request.content)
            assert "ai_provider" not in body
            assert "ai_model" not in body
            return httpx.Response(
                200,
                json={"title": "Fix", "body": "Body", "similar_issues": []},
            )

        client = _make_client(handler)
        client.preview_github_issue(
            job_id="job-1",
            test_name="tests.TestA.test_one",
        )


class TestJJIClientReview:
    def test_get_review_status(self):
        sample = {"total_failures": 5, "reviewed_count": 3, "comment_count": 7}

        def handler(request):
            assert request.method == "GET"
            assert "/results/job-1/review-status" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.get_review_status("job-1")
        assert result["total_failures"] == 5
        assert result["reviewed_count"] == 3
        assert result["comment_count"] == 7

    def test_set_reviewed(self):
        def handler(request):
            assert request.method == "PUT"
            assert "/results/job-1/reviewed" in str(request.url)
            body = json.loads(request.content)
            assert body["test_name"] == "tests.TestA.test_one"
            assert body["reviewed"] is True
            return httpx.Response(200, json={"status": "ok", "reviewed_by": "alice"})

        client = _make_client(handler, username="alice")
        result = client.set_reviewed(
            job_id="job-1", test_name="tests.TestA.test_one", reviewed=True
        )
        assert result["status"] == "ok"
        assert result["reviewed_by"] == "alice"

    def test_set_reviewed_with_child_job(self):
        def handler(request):
            body = json.loads(request.content)
            assert body["test_name"] == "tests.TestA.test_one"
            assert body["reviewed"] is False
            assert body["child_job_name"] == "child-runner"
            assert body["child_build_number"] == 5
            return httpx.Response(200, json={"status": "ok", "reviewed_by": "bob"})

        client = _make_client(handler)
        result = client.set_reviewed(
            job_id="job-1",
            test_name="tests.TestA.test_one",
            reviewed=False,
            child_job_name="child-runner",
            child_build_number=5,
        )
        assert result["status"] == "ok"

    def test_enrich_comments(self):
        def handler(request):
            assert request.method == "POST"
            assert "/results/job-1/enrich-comments" in str(request.url)
            return httpx.Response(200, json={"enriched": 3})

        client = _make_client(handler)
        result = client.enrich_comments("job-1")
        assert result["enriched"] == 3


class TestJJIClientExcludeJobId:
    def test_get_test_history_with_exclude(self):
        def handler(request):
            assert request.method == "GET"
            assert "exclude_job_id=job-99" in str(request.url)
            return httpx.Response(
                200, json={"test_name": "t", "failures": 0, "recent_runs": []}
            )

        client = _make_client(handler)
        client.get_test_history("t", exclude_job_id="job-99")

    def test_search_by_signature_with_exclude(self):
        def handler(request):
            assert request.method == "GET"
            assert "exclude_job_id=job-99" in str(request.url)
            return httpx.Response(
                200, json={"signature": "s", "total_occurrences": 0, "tests": []}
            )

        client = _make_client(handler)
        client.search_by_signature("s", exclude_job_id="job-99")

    def test_get_job_stats_with_exclude(self):
        def handler(request):
            assert request.method == "GET"
            assert "exclude_job_id=job-99" in str(request.url)
            return httpx.Response(
                200, json={"job_name": "j", "total_builds_analyzed": 0}
            )

        client = _make_client(handler)
        client.get_job_stats("j", exclude_job_id="job-99")


class TestJJIClientClassificationsParentJobName:
    def test_get_classifications_with_parent_job_name(self):
        def handler(request):
            assert request.method == "GET"
            assert "parent_job_name=parent-job" in str(request.url)
            return httpx.Response(200, json={"classifications": []})

        client = _make_client(handler)
        client.get_classifications(parent_job_name="parent-job")


class TestJJIClientVerifySSL:
    def test_verify_ssl_default_true(self):
        """Client should verify SSL by default."""
        with patch("jenkins_job_insight.cli.client.httpx.Client") as mock_httpx:
            JJIClient(server_url=BASE_URL)
            _, kwargs = mock_httpx.call_args
            assert kwargs.get("verify", True) is True

    def test_verify_ssl_false_passes_to_httpx(self):
        """Client should pass verify=False to httpx when verify_ssl=False."""
        with patch("jenkins_job_insight.cli.client.httpx.Client") as mock_httpx:
            JJIClient(server_url=BASE_URL, verify_ssl=False)
            _, kwargs = mock_httpx.call_args
            assert kwargs["verify"] is False

    def test_verify_ssl_true_passes_to_httpx(self):
        """Client should pass verify=True to httpx when verify_ssl=True."""
        with patch("jenkins_job_insight.cli.client.httpx.Client") as mock_httpx:
            JJIClient(server_url=BASE_URL, verify_ssl=True)
            _, kwargs = mock_httpx.call_args
            assert kwargs.get("verify", True) is True


def _parse_analyze_request(request):
    """Assert method/path and return parsed body for /analyze handlers."""
    assert request.method == "POST"
    assert request.url.path == "/analyze"
    return json.loads(request.content)


class TestJJIClientAnalyzeAdditionalRepos:
    def test_analyze_passes_additional_repos(self):
        """additional_repos is forwarded in the request body."""
        repos = [{"name": "infra", "url": "https://github.com/org/infra"}]

        def handler(request):
            body = _parse_analyze_request(request)
            assert body["additional_repos"] == repos
            return httpx.Response(202, json={"status": "queued", "job_id": "x"})

        client = _make_client(handler)
        result = client.analyze("my-job", 1, additional_repos=repos)
        assert result["status"] == "queued"


class TestJJIClientAnalyzeExtras:
    def test_analyze_with_ai_provider(self):
        def handler(request):
            body = _parse_analyze_request(request)
            assert body["ai_provider"] == "claude"
            assert body["ai_model"] == "opus"
            return httpx.Response(202, json={"status": "queued", "job_id": "x"})

        client = _make_client(handler)
        result = client.analyze(
            "my-job",
            1,
            ai_provider="claude",
            ai_model="opus",
        )
        assert result["status"] == "queued"

    def test_analyze_with_all_optional_fields(self):
        """All optional kwargs should be included in the POST body."""
        extras = {
            "jenkins_url": "https://jenkins.local",
            "jenkins_user": "admin",
            "jenkins_password": "s3cret",  # pragma: allowlist secret
            "jenkins_ssl_verify": False,
            "jenkins_artifacts_max_size_mb": 50,
            "get_job_artifacts": True,
            "tests_repo_url": "https://github.com/org/tests",
            "jira_url": "https://jira.example.com",
            "jira_email": "user@example.com",
            "jira_api_token": "tok-123",
            "jira_pat": "pat-abc",
            "jira_project_key": "PROJ",
            "jira_ssl_verify": True,
            "jira_max_results": 25,
            "github_token": "ghp_abc123",
            "ai_cli_timeout": 10,
            "enable_jira": True,
            "raw_prompt": "extra instructions",
        }

        def handler(request):
            body = _parse_analyze_request(request)
            assert body["job_name"] == "my-job"
            assert body["build_number"] == 1
            for key, value in extras.items():
                assert body[key] == value, (
                    f"Expected {key}={value}, got {body.get(key)}"
                )
            return httpx.Response(202, json={"status": "queued", "job_id": "x"})

        client = _make_client(handler)
        result = client.analyze("my-job", 1, **extras)
        assert result["status"] == "queued"

    def test_analyze_with_wait_for_completion_fields(self):
        """Wait-for-completion fields should be included in the POST body."""

        def handler(request):
            body = _parse_analyze_request(request)
            assert body["wait_for_completion"] is False
            assert body["poll_interval_minutes"] == 5
            assert body["max_wait_minutes"] == 30
            return httpx.Response(202, json={"status": "queued", "job_id": "x"})

        client = _make_client(handler)
        result = client.analyze(
            "my-job",
            1,
            wait_for_completion=False,
            poll_interval_minutes=5,
            max_wait_minutes=30,
        )
        assert result["status"] == "queued"

    def test_analyze_with_peer_ai_configs(self):
        """peer_ai_configs kwargs should be included in the POST body."""

        def handler(request):
            body = _parse_analyze_request(request)
            assert body["peer_ai_configs"] == [
                {"ai_provider": "cursor", "ai_model": "gpt-5"},
                {"ai_provider": "gemini", "ai_model": "2.5-pro"},
            ]
            assert body["peer_analysis_max_rounds"] == 5
            return httpx.Response(202, json={"status": "queued", "job_id": "x"})

        client = _make_client(handler)
        result = client.analyze(
            "my-job",
            1,
            peer_ai_configs=[
                {"ai_provider": "cursor", "ai_model": "gpt-5"},
                {"ai_provider": "gemini", "ai_model": "2.5-pro"},
            ],
            peer_analysis_max_rounds=5,
        )
        assert result["status"] == "queued"

    def test_analyze_without_extras_sends_minimal_body(self):
        """Without extras, only job_name and build_number should be in the body."""

        def handler(request):
            body = _parse_analyze_request(request)
            assert body == {"job_name": "my-job", "build_number": 1}
            return httpx.Response(202, json={"status": "queued", "job_id": "x"})

        client = _make_client(handler)
        result = client.analyze("my-job", 1)
        assert result["status"] == "queued"


class TestJJIClientReAnalyze:
    def test_re_analyze(self):
        response_data = {
            "status": "queued",
            "job_id": "new-reanalysis-1",
            "message": "Re-analysis job queued.",
        }

        def handler(request):
            assert request.method == "POST"
            assert "/re-analyze/old-job-1" in str(request.url)
            body = json.loads(request.content)
            assert body == {}
            return httpx.Response(202, json=response_data)

        client = _make_client(handler)
        result = client.re_analyze("old-job-1")
        assert result["status"] == "queued"
        assert result["job_id"] == "new-reanalysis-1"
