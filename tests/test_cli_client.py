"""Tests for the JJI CLI client."""

import json

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
    def test_dashboard_default(self):
        """Test dashboard with default limit."""

        def dashboard_handler(request):
            assert request.url.path == "/api/dashboard"
            assert request.url.params.get("limit") == "500"
            return httpx.Response(
                200, json=[{"job_id": "test-1", "status": "completed"}]
            )

        client = _make_client(dashboard_handler)
        result = client.dashboard()
        assert len(result) == 1
        assert result[0]["job_id"] == "test-1"

    def test_dashboard_custom_limit(self):
        """Test dashboard with custom limit."""

        def dashboard_handler(request):
            assert request.url.params.get("limit") == "10"
            return httpx.Response(200, json=[])

        client = _make_client(dashboard_handler)
        result = client.dashboard(limit=10)
        assert result == []


class TestJJIClientAnalyze:
    def test_analyze_async(self):
        response_data = {
            "status": "queued",
            "job_id": "new-job-1",
            "message": "Analysis job queued.",
        }

        def handler(request):
            assert "sync=false" in str(request.url) or "sync" not in str(request.url)
            return httpx.Response(202, json=response_data)

        client = _make_client(handler)
        result = client.analyze("my-job", 42, sync=False)
        assert result["status"] == "queued"
        assert result["job_id"] == "new-job-1"

    def test_analyze_sync(self):
        response_data = {"job_id": "sync-1", "status": "completed", "summary": "Done"}

        def handler(request):
            assert "sync=true" in str(request.url)
            return httpx.Response(200, json=response_data)

        client = _make_client(handler)
        result = client.analyze("my-job", 42, sync=True)
        assert result["status"] == "completed"


class TestJJIClientHistory:
    def test_get_test_history(self):
        sample = {"test_name": "tests.TestA.test_one", "failures": 3, "recent_runs": []}

        def handler(request):
            assert "/history/test/tests.TestA.test_one" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.get_test_history("tests.TestA.test_one")
        assert result["test_name"] == "tests.TestA.test_one"

    def test_search_by_signature(self):
        sample = {"signature": "sig-abc", "total_occurrences": 5, "tests": []}

        def handler(request):
            assert "signature=sig-abc" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.search_by_signature("sig-abc")
        assert result["signature"] == "sig-abc"

    def test_get_job_stats(self):
        sample = {"job_name": "ocp-e2e", "total_builds_analyzed": 10}

        def handler(request):
            assert "/history/stats/ocp-e2e" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.get_job_stats("ocp-e2e")
        assert result["job_name"] == "ocp-e2e"

    def test_get_trends(self):
        sample = {"period": "daily", "data": [{"date": "2026-03-18", "failures": 5}]}

        def handler(request):
            assert "/history/trends" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.get_trends()
        assert result["period"] == "daily"

    def test_get_all_failures(self):
        sample = {"failures": [], "total": 0, "limit": 50, "offset": 0}

        def handler(request):
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
            assert "/history/classifications" in str(request.url)
            return httpx.Response(200, json=sample)

        client = _make_client(handler)
        result = client.get_classifications()
        assert "classifications" in result


class TestJJIClientComments:
    def test_get_comments(self):
        sample = {"comments": [], "reviews": {}}

        def handler(request):
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
            assert request.url.path == "/capabilities"
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


class TestJJIClientAnalyzeExtras:
    def test_analyze_with_ai_provider(self):
        def handler(request):
            body = json.loads(request.content)
            assert body["ai_provider"] == "claude"
            assert body["ai_model"] == "opus"
            return httpx.Response(202, json={"status": "queued", "job_id": "x"})

        client = _make_client(handler)
        result = client.analyze(
            "my-job",
            1,
            sync=False,
            ai_provider="claude",
            ai_model="opus",
        )
        assert result["status"] == "queued"
