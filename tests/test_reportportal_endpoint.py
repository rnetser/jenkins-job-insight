"""Tests for Report Portal API endpoint and auto-push hook."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def _rp_disabled_env():
    """Environment with RP disabled."""
    env = {
        "JENKINS_URL": "https://jenkins.example.com",
        "JENKINS_USER": "testuser",
        "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
        "AI_PROVIDER": "claude",
        "AI_MODEL": "test-model",
    }
    with patch.dict(os.environ, env, clear=True):
        from jenkins_job_insight.config import get_settings

        get_settings.cache_clear()
        yield
        get_settings.cache_clear()


@pytest.fixture
def _rp_enabled_env():
    """Environment with RP enabled."""
    env = {
        "JENKINS_URL": "https://jenkins.example.com",
        "JENKINS_USER": "testuser",
        "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
        "AI_PROVIDER": "claude",
        "AI_MODEL": "test-model",
        "REPORTPORTAL_URL": "http://rp.example.com",
        "REPORTPORTAL_API_TOKEN": "rp-token",  # pragma: allowlist secret
        "REPORTPORTAL_PROJECT": "my-project",
        "PUBLIC_BASE_URL": "https://jji.example.com",
    }
    with patch.dict(os.environ, env, clear=True):
        from jenkins_job_insight.config import get_settings

        get_settings.cache_clear()
        yield
        get_settings.cache_clear()


class TestPushReportPortalEndpoint:
    """Test POST /results/{job_id}/push-reportportal."""

    def test_returns_400_when_rp_disabled(self, _rp_disabled_env):
        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/some-job-id/push-reportportal")
        assert response.status_code == 400
        detail = response.json()["detail"].lower()
        assert "disabled" in detail or "not configured" in detail

    @patch("jenkins_job_insight.main.get_result")
    def test_returns_404_when_job_not_found(self, mock_get_result, _rp_enabled_env):
        mock_get_result.return_value = None
        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/nonexistent-id/push-reportportal")
        assert response.status_code == 404

    @patch(
        "jenkins_job_insight.main.ReportPortalClient",
    )
    @patch("jenkins_job_insight.main.get_result")
    def test_returns_422_on_invalid_stored_failures(
        self, mock_get_result, mock_rp_class, _rp_enabled_env
    ):
        mock_get_result.return_value = {
            "result": {
                "failures": [{"bad_field": "not_a_valid_failure"}],
                "jenkins_url": "http://jenkins.example.com/job/test/1/",
                "job_name": "test-job",
            }
        }
        mock_rp = MagicMock()
        mock_rp.find_launch.return_value = 42
        mock_rp.get_failed_items.return_value = [{"id": 1, "name": "test_a"}]
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/corrupt-job/push-reportportal")
        assert response.status_code == 422
        assert "validation error" in response.json()["detail"].lower()

    @patch(
        "jenkins_job_insight.main.get_history_classification", new_callable=AsyncMock
    )
    @patch("jenkins_job_insight.main.get_result")
    @patch("jenkins_job_insight.main.ReportPortalClient")
    def test_returns_result_on_success(
        self, mock_rp_class, mock_get_result, mock_get_cls, _rp_enabled_env
    ):
        mock_get_cls.return_value = ""
        # Mock stored result
        mock_get_result.return_value = {
            "status": "completed",
            "result": {
                "job_name": "my-job",
                "build_number": 42,
                "jenkins_url": "https://jenkins.example.com/job/my-job/42/",
                "failures": [
                    {
                        "test_name": "test_a",
                        "error": "err",
                        "analysis": {
                            "classification": "PRODUCT BUG",
                            "details": "Bug found",
                        },
                    }
                ],
            },
        }
        # Mock RP client (supports context manager protocol)
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.return_value = 100
        mock_rp.get_failed_items.return_value = [
            {"id": 1, "name": "test_a", "status": "FAILED"}
        ]
        mock_rp.match_failures.return_value = [
            ({"id": 1, "name": "test_a"}, MagicMock(test_name="test_a"))
        ]
        mock_rp.push_classifications.return_value = {
            "pushed": 1,
            "unmatched": [],
            "errors": [],
            "launch_id": 100,
        }
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/some-job-id/push-reportportal")
        assert response.status_code == 200, f"Response: {response.text}"
        data = response.json()
        assert data["pushed"] == 1
        mock_rp.__exit__.assert_called_once()

    @patch(
        "jenkins_job_insight.main.get_history_classification", new_callable=AsyncMock
    )
    @patch("jenkins_job_insight.main.get_result")
    @patch("jenkins_job_insight.main.ReportPortalClient")
    def test_infrastructure_classification_passed_to_rp(
        self, mock_rp_class, mock_get_result, mock_get_cls, _rp_enabled_env
    ):
        """INFRASTRUCTURE history classification maps to RP System Issue."""
        mock_get_cls.return_value = "INFRASTRUCTURE"
        mock_get_result.return_value = {
            "status": "completed",
            "result": {
                "job_name": "my-job",
                "build_number": 42,
                "jenkins_url": "https://jenkins.example.com/job/my-job/42/",
                "failures": [
                    {
                        "test_name": "test_infra",
                        "error": "timeout",
                        "analysis": {
                            "classification": "PRODUCT BUG",
                            "details": "Network timeout",
                        },
                    }
                ],
            },
        }
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.return_value = 200
        mock_rp.get_failed_items.return_value = [
            {"id": 10, "name": "test_infra", "status": "FAILED"}
        ]
        # match_failures returns a pair where the FailureAnalysis has test_name
        mock_failure = MagicMock()
        mock_failure.test_name = "test_infra"
        mock_rp.match_failures.return_value = [
            ({"id": 10, "name": "test_infra"}, mock_failure)
        ]
        mock_rp.push_classifications.return_value = {
            "pushed": 1,
            "unmatched": [],
            "errors": [],
            "launch_id": 200,
        }
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/some-job-id/push-reportportal")
        assert response.status_code == 200, f"Response: {response.text}"
        # Verify push_classifications was called with INFRASTRUCTURE in history_classifications
        push_call = mock_rp.push_classifications.call_args
        history_arg = (
            push_call[0][2]
            if len(push_call[0]) > 2
            else push_call[1].get("history_classifications", {})
        )
        assert history_arg.get("test_infra") == "INFRASTRUCTURE"

    @patch(
        "jenkins_job_insight.main.get_history_classification", new_callable=AsyncMock
    )
    @patch("jenkins_job_insight.main.get_result")
    @patch("jenkins_job_insight.main.ReportPortalClient")
    def test_no_overlap_returns_error(
        self, mock_rp_class, mock_get_result, mock_get_cls, _rp_enabled_env
    ):
        """When RP items and JJI failures have no name overlap, return an error."""
        mock_get_cls.return_value = ""
        mock_get_result.return_value = {
            "result": {
                "job_name": "my-job",
                "build_number": 1,
                "jenkins_url": "https://jenkins.example.com/job/my-job/1/",
                "failures": [
                    {
                        "test_name": "test_alpha",
                        "error": "err",
                        "analysis": {
                            "classification": "PRODUCT BUG",
                            "details": "d",
                        },
                    }
                ],
            },
        }
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.return_value = 99
        mock_rp.get_failed_items.return_value = [
            {"id": 1, "name": "test_beta", "status": "FAILED"}
        ]
        mock_rp.match_failures.return_value = []  # no overlap
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/some-job-id/push-reportportal")
        assert response.status_code == 200
        data = response.json()
        assert data["pushed"] == 0
        assert len(data["errors"]) == 1
        assert "No overlap" in data["errors"][0]
        assert "test_beta" in data["errors"][0]
        assert "test_alpha" in data["errors"][0]

    @patch("jenkins_job_insight.main.ReportPortalClient")
    @patch("jenkins_job_insight.main.get_result")
    def test_verify_ssl_passed_to_rp_client(
        self, mock_get_result, mock_rp_class, _rp_enabled_env
    ):
        """REPORTPORTAL_VERIFY_SSL is forwarded to the ReportPortalClient."""
        with patch.dict(os.environ, {"REPORTPORTAL_VERIFY_SSL": "false"}):
            from jenkins_job_insight.config import get_settings

            get_settings.cache_clear()
            mock_get_result.return_value = {
                "result": {
                    "job_name": "test-job",
                    "jenkins_url": "https://jenkins.example.com/job/test/1/",
                    "failures": [],
                }
            }
            mock_rp = MagicMock()
            mock_rp.__enter__ = MagicMock(return_value=mock_rp)
            mock_rp.__exit__ = MagicMock(return_value=False)
            mock_rp.find_launch.return_value = 1
            mock_rp.get_failed_items.return_value = []
            mock_rp.push_classifications.return_value = {
                "pushed": 0,
                "unmatched": [],
                "errors": [],
                "launch_id": 1,
            }
            mock_rp_class.return_value = mock_rp

            from jenkins_job_insight.main import app

            client = TestClient(app, raise_server_exceptions=False)
            client.post("/results/some-job/push-reportportal")

            # Verify verify_ssl=False was passed
            mock_rp_class.assert_called_once()
            call_kwargs = mock_rp_class.call_args[1]
            assert call_kwargs["verify_ssl"] is False
            get_settings.cache_clear()

    @patch(
        "jenkins_job_insight.main.get_history_classification", new_callable=AsyncMock
    )
    @patch("jenkins_job_insight.main.get_result")
    @patch("jenkins_job_insight.main.ReportPortalClient")
    def test_child_job_push_uses_child_data(
        self, mock_rp_class, mock_get_result, mock_get_cls, _rp_enabled_env
    ):
        """Child job params scope push to child's failures and job name."""
        mock_get_cls.return_value = ""
        mock_get_result.return_value = {
            "status": "completed",
            "result": {
                "job_name": "parent-pipeline",
                "build_number": 1,
                "jenkins_url": "https://jenkins.example.com/job/parent/1/",
                "failures": [],
                "child_job_analyses": [
                    {
                        "job_name": "child-job",
                        "build_number": 42,
                        "jenkins_url": "https://jenkins.example.com/job/child-job/42/",
                        "failures": [
                            {
                                "test_name": "test_child_a",
                                "error": "err",
                                "analysis": {
                                    "classification": "PRODUCT BUG",
                                    "details": "child bug",
                                },
                            }
                        ],
                        "failed_children": [],
                    }
                ],
            },
        }
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.return_value = 300
        mock_rp.get_failed_items.return_value = [
            {"id": 5, "name": "test_child_a", "status": "FAILED"}
        ]
        mock_failure = MagicMock()
        mock_failure.test_name = "test_child_a"
        mock_rp.match_failures.return_value = [
            ({"id": 5, "name": "test_child_a"}, mock_failure)
        ]
        mock_rp.push_classifications.return_value = {
            "pushed": 1,
            "unmatched": [],
            "errors": [],
            "launch_id": 300,
        }
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/results/some-job-id/push-reportportal",
            params={"child_job_name": "child-job", "child_build_number": 42},
        )
        assert response.status_code == 200, f"Response: {response.text}"
        data = response.json()
        assert data["pushed"] == 1
        # find_launch should be called with child job name, not parent
        mock_rp.find_launch.assert_called_once_with(
            "child-job", "https://jenkins.example.com/job/child-job/42/"
        )

    @patch("jenkins_job_insight.main.get_result")
    def test_child_job_not_found_returns_400(self, mock_get_result, _rp_enabled_env):
        """Returns 400 when the specified child job doesn't exist."""
        mock_get_result.return_value = {
            "status": "completed",
            "result": {
                "job_name": "parent-pipeline",
                "build_number": 1,
                "jenkins_url": "https://jenkins.example.com/job/parent/1/",
                "failures": [],
                "child_job_analyses": [],
            },
        }

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/results/some-job-id/push-reportportal",
            params={"child_job_name": "nonexistent", "child_build_number": 99},
        )
        assert response.status_code == 400
        assert "not found" in response.json()["detail"].lower()

    @patch(
        "jenkins_job_insight.main.get_history_classification", new_callable=AsyncMock
    )
    @patch("jenkins_job_insight.main.get_result")
    @patch("jenkins_job_insight.main.ReportPortalClient")
    def test_child_job_report_url_contains_anchor(
        self, mock_rp_class, mock_get_result, mock_get_cls, _rp_enabled_env
    ):
        """Report URL includes child anchor fragment."""
        mock_get_cls.return_value = ""
        mock_get_result.return_value = {
            "status": "completed",
            "result": {
                "job_name": "parent-pipeline",
                "build_number": 1,
                "jenkins_url": "https://jenkins.example.com/job/parent/1/",
                "failures": [],
                "child_job_analyses": [
                    {
                        "job_name": "child-job",
                        "build_number": 10,
                        "jenkins_url": "https://jenkins.example.com/job/child-job/10/",
                        "failures": [
                            {
                                "test_name": "test_x",
                                "error": "err",
                                "analysis": {
                                    "classification": "CODE ISSUE",
                                    "details": "d",
                                },
                            }
                        ],
                        "failed_children": [],
                    }
                ],
            },
        }
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.return_value = 400
        mock_rp.get_failed_items.return_value = [
            {"id": 7, "name": "test_x", "status": "FAILED"}
        ]
        mock_failure = MagicMock()
        mock_failure.test_name = "test_x"
        mock_rp.match_failures.return_value = [
            ({"id": 7, "name": "test_x"}, mock_failure)
        ]
        mock_rp.push_classifications.return_value = {
            "pushed": 1,
            "unmatched": [],
            "errors": [],
            "launch_id": 400,
        }
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/results/some-job-id/push-reportportal",
            params={"child_job_name": "child-job", "child_build_number": 10},
        )
        assert response.status_code == 200
        # Verify the report_url passed to push_classifications contains the anchor
        push_call = mock_rp.push_classifications.call_args
        report_url = push_call[0][1]  # second positional arg
        assert "#child-child-job-10" in report_url

    @patch(
        "jenkins_job_insight.main.get_history_classification", new_callable=AsyncMock
    )
    @patch("jenkins_job_insight.main.get_result")
    @patch("jenkins_job_insight.main.ReportPortalClient")
    def test_nested_child_job_push(
        self, mock_rp_class, mock_get_result, mock_get_cls, _rp_enabled_env
    ):
        """Recursively finds nested child job in failed_children."""
        mock_get_cls.return_value = ""
        mock_get_result.return_value = {
            "status": "completed",
            "result": {
                "job_name": "parent-pipeline",
                "build_number": 1,
                "jenkins_url": "https://jenkins.example.com/job/parent/1/",
                "failures": [],
                "child_job_analyses": [
                    {
                        "job_name": "child-1",
                        "build_number": 10,
                        "jenkins_url": "https://jenkins.example.com/job/child-1/10/",
                        "failures": [],
                        "failed_children": [
                            {
                                "job_name": "nested-child",
                                "build_number": 5,
                                "jenkins_url": "https://jenkins.example.com/job/nested-child/5/",
                                "failures": [
                                    {
                                        "test_name": "test_nested",
                                        "error": "err",
                                        "analysis": {
                                            "classification": "INFRASTRUCTURE",
                                            "details": "d",
                                        },
                                    }
                                ],
                                "failed_children": [],
                            }
                        ],
                    }
                ],
            },
        }
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.return_value = 500
        mock_rp.get_failed_items.return_value = [
            {"id": 9, "name": "test_nested", "status": "FAILED"}
        ]
        mock_failure = MagicMock()
        mock_failure.test_name = "test_nested"
        mock_rp.match_failures.return_value = [
            ({"id": 9, "name": "test_nested"}, mock_failure)
        ]
        mock_rp.push_classifications.return_value = {
            "pushed": 1,
            "unmatched": [],
            "errors": [],
            "launch_id": 500,
        }
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/results/some-job-id/push-reportportal",
            params={"child_job_name": "nested-child", "child_build_number": 5},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["pushed"] == 1
        # find_launch called with nested child's job name
        mock_rp.find_launch.assert_called_once_with(
            "nested-child", "https://jenkins.example.com/job/nested-child/5/"
        )


class TestRPPushHTTPErrors:
    """Verify HTTP errors from RP API return proper error responses, not 500."""

    @patch("jenkins_job_insight.main.ReportPortalClient")
    @patch("jenkins_job_insight.main.get_result")
    def test_find_launch_401_returns_200_with_error(
        self, mock_get_result, mock_rp_class, _rp_enabled_env
    ):
        """A 401 from RP find_launch returns a push result with errors, not 500."""
        import requests as _requests

        mock_get_result.return_value = {
            "result": {
                "job_name": "my-job",
                "jenkins_url": "https://jenkins.example.com/job/my-job/1/",
                "failures": [
                    {
                        "test_name": "test_a",
                        "error": "err",
                        "analysis": {"classification": "PRODUCT BUG", "details": "d"},
                    }
                ],
            }
        }
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = '{"message": "Full authentication is required"}'
        mock_response.json.return_value = {"message": "Full authentication is required"}
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.side_effect = _requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/job1/push-reportportal")
        assert response.status_code == 200
        body = response.json()
        assert body["pushed"] == 0
        assert len(body["errors"]) == 1
        assert "401" in body["errors"][0]
        assert "Full authentication is required" in body["errors"][0]

    @patch("jenkins_job_insight.main.ReportPortalClient")
    @patch("jenkins_job_insight.main.get_result")
    def test_find_launch_connection_error_returns_200_with_error(
        self, mock_get_result, mock_rp_class, _rp_enabled_env
    ):
        """A ConnectionError from find_launch returns a push result with errors."""
        mock_get_result.return_value = {
            "result": {
                "job_name": "my-job",
                "jenkins_url": "https://jenkins.example.com/job/my-job/1/",
                "failures": [
                    {
                        "test_name": "test_a",
                        "error": "err",
                        "analysis": {"classification": "PRODUCT BUG", "details": "d"},
                    }
                ],
            }
        }
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.side_effect = ConnectionError("connection refused")
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/job2/push-reportportal")
        assert response.status_code == 200
        body = response.json()
        assert body["pushed"] == 0
        assert len(body["errors"]) == 1
        assert "ConnectionError" in body["errors"][0]
        assert "connection refused" in body["errors"][0]

    @patch("jenkins_job_insight.main.ReportPortalClient")
    @patch("jenkins_job_insight.main.get_result")
    def test_get_failed_items_error_returns_200_with_error(
        self, mock_get_result, mock_rp_class, _rp_enabled_env
    ):
        """An HTTPError from get_failed_items returns errors, not 500."""
        import requests as _requests

        mock_get_result.return_value = {
            "result": {
                "job_name": "my-job",
                "jenkins_url": "https://jenkins.example.com/job/my-job/1/",
                "failures": [
                    {
                        "test_name": "test_a",
                        "error": "err",
                        "analysis": {"classification": "PRODUCT BUG", "details": "d"},
                    }
                ],
            }
        }
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = '{"message": "Access denied"}'
        mock_response.json.return_value = {"message": "Access denied"}
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.return_value = 42
        mock_rp.get_failed_items.side_effect = _requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/job1/push-reportportal")
        assert response.status_code == 200
        body = response.json()
        assert body["pushed"] == 0
        assert body["launch_id"] == 42
        assert len(body["errors"]) == 1
        assert "403" in body["errors"][0]
        assert "Access denied" in body["errors"][0]
        assert "fetching failed items" in body["errors"][0]

    @patch("jenkins_job_insight.main.ReportPortalClient")
    @patch("jenkins_job_insight.main.get_result")
    def test_match_failures_error_returns_200_with_error(
        self, mock_get_result, mock_rp_class, _rp_enabled_env
    ):
        """An exception from match_failures returns errors, not 500."""
        mock_get_result.return_value = {
            "result": {
                "job_name": "my-job",
                "jenkins_url": "https://jenkins.example.com/job/my-job/1/",
                "failures": [
                    {
                        "test_name": "test_a",
                        "error": "err",
                        "analysis": {"classification": "PRODUCT BUG", "details": "d"},
                    }
                ],
            }
        }
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.return_value = 42
        mock_rp.get_failed_items.return_value = [{"id": 1, "name": "test_a"}]
        mock_rp.match_failures.side_effect = TypeError("unexpected None")
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/job2/push-reportportal")
        assert response.status_code == 200
        body = response.json()
        assert body["pushed"] == 0
        assert body["launch_id"] == 42
        assert len(body["errors"]) == 1
        assert "TypeError" in body["errors"][0]
        assert "matching RP items" in body["errors"][0]

    @patch("jenkins_job_insight.main.logger")
    @patch("jenkins_job_insight.main.ReportPortalClient")
    @patch("jenkins_job_insight.main.get_result")
    def test_ambiguous_launch_returns_200_with_error(
        self, mock_get_result, mock_rp_class, mock_logger, _rp_enabled_env
    ):
        """AmbiguousLaunchError from find_launch returns errors and logs WARNING."""
        from jenkins_job_insight.reportportal import AmbiguousLaunchError

        mock_get_result.return_value = {
            "result": {
                "job_name": "my-job",
                "jenkins_url": "https://jenkins.example.com/job/my-job/1/",
                "failures": [
                    {
                        "test_name": "test_a",
                        "error": "err",
                        "analysis": {"classification": "PRODUCT BUG", "details": "d"},
                    }
                ],
            }
        }
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.side_effect = AmbiguousLaunchError(
            count=3,
            job_name="my-job",
            jenkins_url="https://jenkins.example.com/job/my-job/1/",
        )
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/job1/push-reportportal")
        assert response.status_code == 200
        body = response.json()
        assert body["pushed"] == 0
        assert len(body["errors"]) == 1
        assert "Ambiguous" in body["errors"][0]
        assert "my-project" in body["errors"][0]

        # Ambiguous launch is logged at WARNING, not ERROR
        warning_calls = [
            c
            for c in mock_logger.warning.call_args_list
            if "ambiguous" in str(c).lower()
        ]
        assert warning_calls, "Expected WARNING log for ambiguous launch"
        error_calls = [
            c for c in mock_logger.error.call_args_list if "ambiguous" in str(c).lower()
        ]
        assert not error_calls, "Should NOT log ambiguous launch at ERROR"

    @patch("jenkins_job_insight.main.logger")
    @patch("jenkins_job_insight.main.ReportPortalClient")
    @patch("jenkins_job_insight.main.get_result")
    def test_rp_client_constructor_failure_returns_200_with_error(
        self, mock_get_result, mock_rp_class, mock_logger, _rp_enabled_env
    ):
        """RPClient constructor failure returns errors and logs ERROR."""
        mock_get_result.return_value = {
            "result": {
                "job_name": "my-job",
                "jenkins_url": "https://jenkins.example.com/job/my-job/1/",
                "failures": [
                    {
                        "test_name": "test_a",
                        "error": "err",
                        "analysis": {"classification": "PRODUCT BUG", "details": "d"},
                    }
                ],
            }
        }
        mock_rp_class.side_effect = ConnectionError("Name resolution failed")

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/job3/push-reportportal")
        assert response.status_code == 200
        body = response.json()
        assert body["pushed"] == 0
        assert len(body["errors"]) == 1
        assert "ConnectionError" in body["errors"][0]
        assert "Name resolution failed" in body["errors"][0]

        # Constructor failure is logged at ERROR
        error_calls = [
            c
            for c in mock_logger.error.call_args_list
            if "name resolution" in str(c).lower()
        ]
        assert error_calls, "Expected ERROR log for constructor failure"


class TestRPPushDebugLogging:
    """Verify normal-state RP paths log at DEBUG, not ERROR."""

    @patch("jenkins_job_insight.main.logger")
    @patch("jenkins_job_insight.main.ReportPortalClient")
    @patch("jenkins_job_insight.main.get_result")
    def test_no_failed_items_logs_debug(
        self, mock_get_result, mock_rp_class, mock_logger, _rp_enabled_env
    ):
        mock_get_result.return_value = {
            "result": {
                "job_name": "my-job",
                "jenkins_url": "https://jenkins.example.com/job/my-job/1/",
                "failures": [],
            }
        }
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.return_value = 42
        mock_rp.get_failed_items.return_value = []
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/job1/push-reportportal")
        assert response.status_code == 200
        assert response.json()["pushed"] == 0

        # Normal state: logged at DEBUG, not ERROR
        debug_calls = [
            c
            for c in mock_logger.debug.call_args_list
            if "no failed items" in str(c).lower()
        ]
        assert debug_calls, "Expected DEBUG log for 'no failed items'"
        error_calls = [
            c
            for c in mock_logger.error.call_args_list
            if "no failed items" in str(c).lower()
        ]
        assert not error_calls, "Should NOT log 'no failed items' at ERROR"

    @patch("jenkins_job_insight.main.logger")
    @patch("jenkins_job_insight.main.ReportPortalClient")
    @patch("jenkins_job_insight.main.get_result")
    def test_no_jji_failures_logs_debug(
        self, mock_get_result, mock_rp_class, mock_logger, _rp_enabled_env
    ):
        mock_get_result.return_value = {
            "result": {
                "job_name": "my-job",
                "jenkins_url": "https://jenkins.example.com/job/my-job/1/",
                "failures": [],
            }
        }
        mock_rp = MagicMock()
        mock_rp.__enter__ = MagicMock(return_value=mock_rp)
        mock_rp.__exit__ = MagicMock(return_value=False)
        mock_rp.find_launch.return_value = 42
        mock_rp.get_failed_items.return_value = [
            {"id": 1, "name": "test_a", "status": "FAILED"}
        ]
        mock_rp_class.return_value = mock_rp

        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/results/job2/push-reportportal")
        assert response.status_code == 200
        assert response.json()["pushed"] == 0

        # Normal state: logged at DEBUG, not ERROR
        debug_calls = [
            c
            for c in mock_logger.debug.call_args_list
            if "no jji failures" in str(c).lower()
        ]
        assert debug_calls, "Expected DEBUG log for 'no JJI failures'"
        error_calls = [
            c
            for c in mock_logger.error.call_args_list
            if "no jji failures" in str(c).lower()
        ]
        assert not error_calls, "Should NOT log 'no JJI failures' at ERROR"


class TestCapabilitiesEndpoint:
    """Test that capabilities includes reportportal."""

    def test_capabilities_includes_rp_disabled(self, _rp_disabled_env):
        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "reportportal" in data
        assert data["reportportal"] is False

    def test_capabilities_includes_rp_enabled(self, _rp_enabled_env):
        from jenkins_job_insight.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "reportportal" in data
        assert data["reportportal"] is True
