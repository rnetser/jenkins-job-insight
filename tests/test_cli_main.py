"""Tests for jji CLI commands using typer test runner."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from jenkins_job_insight.cli.main import app

runner = CliRunner()

_TEST_SERVER = "http://test-server:8000"


@pytest.fixture
def mock_client():
    """Provide a mocked JJIClient for all CLI tests.

    Sets JJI_SERVER_URL so the main_callback does not exit early,
    and patches _get_client so no real HTTP calls are made.
    """
    with (
        patch.dict(os.environ, {"JJI_SERVER_URL": _TEST_SERVER}),
        patch("jenkins_job_insight.cli.main._get_client") as mock_get,
    ):
        client = MagicMock()
        mock_get.return_value = client
        yield client


class TestHealthCommand:
    def test_health(self, mock_client):
        mock_client.health.return_value = {"status": "healthy"}
        result = runner.invoke(app, ["health"])
        assert result.exit_code == 0
        assert "healthy" in result.output

    def test_health_json(self, mock_client):
        mock_client.health.return_value = {"status": "healthy"}
        result = runner.invoke(app, ["--json", "health"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "healthy"


class TestResultsCommands:
    def test_results_list(self, mock_client):
        mock_client.list_results.return_value = [
            {
                "job_id": "abc-123",
                "status": "completed",
                "jenkins_url": "https://jenkins.example.com/job/test/1/",
                "created_at": "2026-03-18",
            },
        ]
        result = runner.invoke(app, ["results", "list"])
        assert result.exit_code == 0
        assert "abc-123" in result.output

    def test_results_show(self, mock_client):
        mock_client.get_result.return_value = {
            "job_id": "abc-123",
            "status": "completed",
            "result": {"summary": "1 failure analyzed"},
        }
        result = runner.invoke(app, ["results", "show", "abc-123"])
        assert result.exit_code == 0
        assert "abc-123" in result.output

    def test_results_delete(self, mock_client):
        mock_client.delete_job.return_value = {"status": "deleted", "job_id": "abc-123"}
        result = runner.invoke(app, ["results", "delete", "abc-123"])
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()


class TestAnalyzeCommand:
    def test_analyze_async(self, mock_client):
        mock_client.analyze.return_value = {
            "status": "queued",
            "job_id": "new-1",
            "message": "Analysis job queued.",
        }
        result = runner.invoke(app, ["analyze", "my-job", "42"])
        assert result.exit_code == 0
        assert "queued" in result.output.lower() or "new-1" in result.output

    def test_analyze_sync(self, mock_client):
        mock_client.analyze.return_value = {
            "job_id": "sync-1",
            "status": "completed",
            "summary": "Done",
        }
        result = runner.invoke(app, ["analyze", "my-job", "42", "--sync"])
        assert result.exit_code == 0
        assert "completed" in result.output.lower() or "sync-1" in result.output


class TestStatusCommand:
    def test_status(self, mock_client):
        mock_client.get_result.return_value = {
            "job_id": "abc-123",
            "status": "running",
        }
        result = runner.invoke(app, ["status", "abc-123"])
        assert result.exit_code == 0
        assert "running" in result.output.lower()


class TestHistoryCommands:
    def test_history_test(self, mock_client):
        mock_client.get_test_history.return_value = {
            "test_name": "tests.TestA.test_one",
            "failures": 3,
            "failure_rate": 0.6,
            "last_classification": "PRODUCT BUG",
            "recent_runs": [],
            "comments": [],
        }
        result = runner.invoke(app, ["history", "test", "tests.TestA.test_one"])
        assert result.exit_code == 0
        assert "tests.TestA.test_one" in result.output

    def test_history_search(self, mock_client):
        mock_client.search_by_signature.return_value = {
            "signature": "sig-abc",
            "total_occurrences": 5,
            "unique_tests": 2,
            "tests": [{"test_name": "tests.TestA.test_one", "occurrences": 3}],
        }
        result = runner.invoke(app, ["history", "search", "--signature", "sig-abc"])
        assert result.exit_code == 0
        assert "sig-abc" in result.output or "tests.TestA" in result.output

    def test_history_stats(self, mock_client):
        mock_client.get_job_stats.return_value = {
            "job_name": "ocp-e2e",
            "total_builds_analyzed": 10,
            "builds_with_failures": 3,
        }
        result = runner.invoke(app, ["history", "stats", "ocp-e2e"])
        assert result.exit_code == 0
        assert "ocp-e2e" in result.output

    def test_history_trends(self, mock_client):
        mock_client.get_trends.return_value = {
            "period": "daily",
            "data": [{"date": "2026-03-18", "failures": 5, "unique_tests": 3}],
        }
        result = runner.invoke(app, ["history", "trends"])
        assert result.exit_code == 0
        assert "2026-03-18" in result.output or "daily" in result.output

    def test_history_failures(self, mock_client):
        mock_client.get_all_failures.return_value = {
            "failures": [
                {
                    "test_name": "tests.TestA.test_one",
                    "classification": "PRODUCT BUG",
                    "job_name": "ocp-e2e",
                },
            ],
            "total": 1,
            "limit": 50,
            "offset": 0,
        }
        result = runner.invoke(app, ["history", "failures"])
        assert result.exit_code == 0
        assert "tests.TestA" in result.output


class TestClassifyCommand:
    def test_classify(self, mock_client):
        mock_client.classify_test.return_value = {"id": 1}
        result = runner.invoke(
            app,
            [
                "classify",
                "tests.TestA.test_one",
                "--type",
                "FLAKY",
                "--reason",
                "intermittent DNS",
                "--job-id",
                "job-123",
            ],
        )
        assert result.exit_code == 0
        assert "1" in result.output or "classified" in result.output.lower()


class TestClassificationsCommand:
    def test_classifications_list(self, mock_client):
        mock_client.get_classifications.return_value = {
            "classifications": [
                {
                    "test_name": "tests.TestA.test_one",
                    "classification": "FLAKY",
                    "reason": "DNS",
                },
            ],
        }
        result = runner.invoke(app, ["classifications", "list"])
        assert result.exit_code == 0
        assert "FLAKY" in result.output


class TestCommentsCommands:
    def test_comments_list(self, mock_client):
        mock_client.get_comments.return_value = {
            "comments": [
                {
                    "id": 1,
                    "test_name": "tests.TestA.test_one",
                    "comment": "Fixed in PR #42",
                    "username": "alice",
                    "created_at": "2026-03-18",
                },
            ],
            "reviews": {},
        }
        result = runner.invoke(app, ["comments", "list", "job-1"])
        assert result.exit_code == 0
        assert "Fixed in PR #42" in result.output or "tests.TestA" in result.output

    def test_comments_add(self, mock_client):
        mock_client.add_comment.return_value = {"id": 42}
        result = runner.invoke(
            app,
            [
                "comments",
                "add",
                "job-1",
                "--test",
                "tests.TestA.test_one",
                "-m",
                "Opened JIRA-123",
            ],
        )
        assert result.exit_code == 0
        assert "42" in result.output or "added" in result.output.lower()

    def test_comments_delete(self, mock_client):
        mock_client.delete_comment.return_value = {"status": "deleted"}
        result = runner.invoke(app, ["comments", "delete", "job-1", "42"])
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()


class TestServerFlag:
    def test_custom_server(self):
        with (
            patch("jenkins_job_insight.cli.main._get_client") as mock_get,
            patch("jenkins_job_insight.cli.main._state", {}) as mock_state,
        ):
            client = MagicMock()
            client.health.return_value = {"status": "healthy"}
            mock_get.return_value = client
            result = runner.invoke(app, ["--server", "http://custom:9000", "health"])
            assert result.exit_code == 0
            mock_get.assert_called_once()
            # Verify the server URL was stored in state by the callback
            assert mock_state.get("server_url") == "http://custom:9000"

    def test_missing_server_url(self):
        """CLI exits with error when no server URL is configured."""
        with patch.dict(os.environ, {}, clear=False):
            # Ensure JJI_SERVER_URL is not set
            env = os.environ.copy()
            env.pop("JJI_SERVER_URL", None)
            with patch.dict(os.environ, env, clear=True):
                result = runner.invoke(app, ["health"])
                assert result.exit_code == 1
                assert "Server URL" in result.output


class TestErrorHandling:
    def test_connection_error(self, mock_client):
        from jenkins_job_insight.cli.client import JJIError

        mock_client.health.side_effect = JJIError(
            status_code=0, detail="Connection refused"
        )
        result = runner.invoke(app, ["health"])
        assert result.exit_code != 0
        assert "Connection" in result.output or "Error" in result.output

    def test_http_error(self, mock_client):
        from jenkins_job_insight.cli.client import JJIError

        mock_client.get_result.side_effect = JJIError(
            status_code=404, detail="Job not found"
        )
        result = runner.invoke(app, ["results", "show", "nonexistent"])
        assert result.exit_code != 0
        assert "404" in result.output or "not found" in result.output.lower()

    def test_401_error_hints_about_user_flag(self, mock_client):
        """_handle_error should hint about --user when server returns 401."""
        from jenkins_job_insight.cli.client import JJIError

        mock_client.delete_job.side_effect = JJIError(
            status_code=401, detail="Please register a username first"
        )
        result = runner.invoke(app, ["results", "delete", "job-123"])
        assert result.exit_code == 1
        assert "--user" in result.output


class TestNullFieldHandling:
    def test_history_test_with_null_failure_rate(self, mock_client):
        """history test should handle None failure_rate without crashing."""
        mock_client.get_test_history.return_value = {
            "test_name": "tests.TestFoo.test_bar",
            "total_runs": 10,
            "failures": 3,
            "passes": None,
            "failure_rate": None,
            "first_seen": "2026-03-01",
            "last_seen": "2026-03-17",
            "last_classification": "PRODUCT BUG",
            "classifications": {"PRODUCT BUG": 3},
            "recent_runs": [],
            "consecutive_failures": 3,
            "note": "estimated",
        }
        result = runner.invoke(app, ["history", "test", "tests.TestFoo.test_bar"])
        assert result.exit_code == 0
        assert "test_bar" in result.output or "TestFoo" in result.output

    def test_history_stats_with_null_failure_rate(self, mock_client):
        """history stats should handle None overall_failure_rate without crashing."""
        mock_client.get_job_stats.return_value = {
            "job_name": "ocp-e2e",
            "total_builds_analyzed": 0,
            "builds_with_failures": 0,
            "overall_failure_rate": None,
            "most_common_failures": [],
        }
        result = runner.invoke(app, ["history", "stats", "ocp-e2e"])
        assert result.exit_code == 0
        assert "ocp-e2e" in result.output


class TestJsonPerCommand:
    def test_json_flag_after_subcommand(self, mock_client):
        """--json should work when placed after the subcommand."""
        mock_client.health.return_value = {"status": "healthy"}
        result = runner.invoke(app, ["health", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "healthy"

    def test_json_flag_after_nested_subcommand(self, mock_client):
        """--json should work after a nested subcommand like 'results list'."""
        mock_client.list_results.return_value = [
            {
                "job_id": "abc-123",
                "status": "completed",
                "jenkins_url": "https://jenkins.example.com/job/test/1/",
                "created_at": "2026-03-18",
            },
        ]
        result = runner.invoke(app, ["results", "list", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["job_id"] == "abc-123"
