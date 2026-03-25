"""Tests for FastAPI main application."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from jenkins_job_insight import storage
from jenkins_job_insight.models import (
    AnalysisDetail,
    FailureAnalysis,
)


@pytest.fixture
def mock_settings():
    """Mock settings for tests."""
    env = {
        "JENKINS_URL": "https://jenkins.example.com",
        "JENKINS_USER": "testuser",
        "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
        "GEMINI_API_KEY": "test-key",  # pragma: allowlist secret
    }
    with patch.dict(os.environ, env, clear=True):
        # Clear the lru_cache to use fresh settings
        from jenkins_job_insight.config import get_settings

        get_settings.cache_clear()
        yield


@pytest.fixture
def test_client(mock_settings, temp_db_path: Path):
    """Create a test client with mocked dependencies."""
    with patch.object(storage, "DB_PATH", temp_db_path):
        from starlette.testclient import TestClient
        from jenkins_job_insight.main import app

        with TestClient(app) as client:
            yield client


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_check_returns_healthy(self, test_client) -> None:
        """Test that health check returns healthy status."""
        response = test_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    def test_health_check_method_not_allowed(self, test_client) -> None:
        """Test that POST to health returns 405."""
        response = test_client.post("/health")
        assert response.status_code == 405


class TestAnalyzeEndpoint:
    """Tests for the /analyze endpoint."""

    def test_analyze_async_returns_queued(self, test_client) -> None:
        """Test that async analyze returns queued status."""
        with patch("jenkins_job_insight.main.process_analysis_with_id"):
            response = test_client.post(
                "/analyze",
                json={
                    "job_name": "test",
                    "build_number": 123,
                    "tests_repo_url": "https://github.com/example/repo",
                },
            )
            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "queued"
            assert data["base_url"] == "http://testserver"
            assert data["result_url"].startswith("http://testserver/results/")

    def test_analyze_invalid_build_number(self, test_client) -> None:
        """Test that invalid build number returns 422."""
        response = test_client.post(
            "/analyze",
            json={
                "job_name": "test",
                "build_number": "not-a-number",
                "tests_repo_url": "https://github.com/example/repo",
            },
        )
        assert response.status_code == 422

    def test_analyze_invalid_tests_repo_url(self, test_client) -> None:
        """Test that invalid repo URL returns 422."""
        response = test_client.post(
            "/analyze",
            json={
                "job_name": "test",
                "build_number": 123,
                "tests_repo_url": "not-a-valid-url",
            },
        )
        assert response.status_code == 422

    def test_analyze_missing_required_field(self, test_client) -> None:
        """Test that missing required field returns 422."""
        response = test_client.post(
            "/analyze",
            json={
                "job_name": "test",
            },
        )
        assert response.status_code == 422

    def test_analyze_with_optional_fields(self, test_client) -> None:
        """Test analyze with optional fields."""
        with patch("jenkins_job_insight.main.process_analysis_with_id"):
            response = test_client.post(
                "/analyze",
                json={
                    "job_name": "test",
                    "build_number": 123,
                    "tests_repo_url": "https://github.com/example/repo",
                },
            )
            assert response.status_code == 202


class TestBaseUrlDetection:
    """Tests for base URL detection from request headers."""

    def test_base_url_from_forwarded_headers(self, test_client) -> None:
        """Test base URL detection from X-Forwarded-Proto and X-Forwarded-Host."""
        with patch("jenkins_job_insight.main.process_analysis_with_id"):
            response = test_client.post(
                "/analyze",
                json={"job_name": "test", "build_number": 1},
                headers={
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "myapp.example.com",
                },
            )
            assert response.status_code == 202
            data = response.json()
            assert data["base_url"] == "https://myapp.example.com"
            assert data["result_url"].startswith("https://myapp.example.com/results/")

    def test_base_url_from_forwarded_headers_with_port(self, test_client) -> None:
        """Test base URL detection with forwarded host including port."""
        with patch("jenkins_job_insight.main.process_analysis_with_id"):
            response = test_client.post(
                "/analyze",
                json={"job_name": "test", "build_number": 1},
                headers={
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "myapp.example.com:8443",
                },
            )
            assert response.status_code == 202
            data = response.json()
            assert data["base_url"] == "https://myapp.example.com:8443"

    def test_base_url_comma_separated_forwarded_headers(self, test_client) -> None:
        """Test that only the first value is used from comma-separated forwarded headers."""
        with patch("jenkins_job_insight.main.process_analysis_with_id"):
            response = test_client.post(
                "/analyze",
                json={"job_name": "test", "build_number": 1},
                headers={
                    "X-Forwarded-Proto": "https, http",
                    "X-Forwarded-Host": "external.example.com, internal.proxy",
                },
            )
            assert response.status_code == 202
            data = response.json()
            assert data["base_url"] == "https://external.example.com"

    def test_base_url_invalid_proto_defaults_to_https(self, test_client) -> None:
        """Test that invalid X-Forwarded-Proto defaults to https."""
        with patch("jenkins_job_insight.main.process_analysis_with_id"):
            response = test_client.post(
                "/analyze",
                json={"job_name": "test", "build_number": 1},
                headers={
                    "X-Forwarded-Proto": "ftp",
                    "X-Forwarded-Host": "myapp.example.com",
                },
            )
            assert response.status_code == 202
            data = response.json()
            assert data["base_url"] == "https://myapp.example.com"

    def test_base_url_invalid_forwarded_host_falls_back(self, test_client) -> None:
        """Test that invalid X-Forwarded-Host (with special chars) falls back to Host header."""
        with patch("jenkins_job_insight.main.process_analysis_with_id"):
            response = test_client.post(
                "/analyze",
                json={"job_name": "test", "build_number": 1},
                headers={
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "evil.com/<script>alert(1)</script>",
                },
            )
            assert response.status_code == 202
            data = response.json()
            # Should NOT contain the malicious host
            assert "evil.com/<script>" not in data["base_url"]
            # Should fall back to testserver
            assert data["base_url"] == "http://testserver"

    def test_base_url_leading_dot_hostname_rejected(self, test_client) -> None:
        """Test that leading dot in hostname is rejected by RFC-1123 validation."""
        with patch("jenkins_job_insight.main.process_analysis_with_id"):
            response = test_client.post(
                "/analyze",
                json={"job_name": "test", "build_number": 1},
                headers={
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": ".evil.com",
                },
            )
            assert response.status_code == 202
            data = response.json()
            assert data["base_url"] == "http://testserver"

    def test_base_url_default_from_host_header(self, test_client) -> None:
        """Test base URL from Host header when no forwarded headers present."""
        with patch("jenkins_job_insight.main.process_analysis_with_id"):
            # TestClient always sends Host: testserver
            response = test_client.post(
                "/analyze",
                json={"job_name": "test", "build_number": 1},
            )
            assert response.status_code == 202
            data = response.json()
            assert data["base_url"] == "http://testserver"


class TestAnalyzeFailuresEndpoint:
    """Tests for the POST /analyze-failures endpoint."""

    def test_analyze_failures_success(self, test_client) -> None:
        """Test that valid failures return 200 with correct structure."""
        mock_analysis = FailureAnalysis(
            test_name="test_foo",
            error="assert False",
            analysis=AnalysisDetail(
                classification="CODE ISSUE",
                details="Test assertion failed",
            ),
        )

        with patch("jenkins_job_insight.main.RepositoryManager") as mock_repo_cls:
            mock_repo_instance = mock_repo_cls.return_value
            mock_repo_instance.clone.return_value = None
            mock_repo_instance.cleanup.return_value = None

            with patch(
                "jenkins_job_insight.main.analyze_failure_group",
                new_callable=AsyncMock,
            ) as mock_analyze_group:
                mock_analyze_group.return_value = [mock_analysis]

                with patch(
                    "jenkins_job_insight.main.run_parallel_with_limit",
                    new_callable=AsyncMock,
                ) as mock_parallel:

                    async def run_coroutines(coroutines, **kwargs):
                        return [await coro for coro in coroutines]

                    mock_parallel.side_effect = run_coroutines

                    response = test_client.post(
                        "/analyze-failures",
                        json={
                            "failures": [
                                {
                                    "test_name": "test_foo",
                                    "error_message": "assert False",
                                    "stack_trace": "File test.py, line 10",
                                }
                            ],
                            "ai_provider": "claude",
                            "ai_model": "test-model",
                        },
                    )
                    assert response.status_code == 200
                    data = response.json()
                    assert data["status"] == "completed"
                    assert data["ai_provider"] == "claude"
                    assert data["ai_model"] == "test-model"
                    assert "job_id" in data
                    assert len(data["failures"]) == 1
                    assert data["failures"][0]["test_name"] == "test_foo"
                    assert data["base_url"] == "http://testserver"
                    assert data["result_url"].startswith("http://testserver/results/")

    def test_analyze_failures_empty_failures(self, test_client) -> None:
        """Test that empty failures list returns 422 (validator rejects empty list without raw_xml)."""
        response = test_client.post(
            "/analyze-failures",
            json={
                "failures": [],
                "ai_provider": "claude",
                "ai_model": "test-model",
            },
        )
        assert response.status_code == 422

    def test_analyze_failures_missing_ai_provider(self, test_client) -> None:
        """Test that missing AI provider (no env var, no body param) returns 400."""
        response = test_client.post(
            "/analyze-failures",
            json={
                "failures": [
                    {
                        "test_name": "test_foo",
                        "error_message": "assert False",
                    }
                ],
                "ai_model": "test-model",
            },
        )
        assert response.status_code == 400
        assert "AI provider" in response.json()["detail"]

    def test_analyze_failures_missing_ai_model(self, test_client) -> None:
        """Test that missing AI model returns 400."""
        response = test_client.post(
            "/analyze-failures",
            json={
                "failures": [
                    {
                        "test_name": "test_foo",
                        "error_message": "assert False",
                    }
                ],
                "ai_provider": "claude",
            },
        )
        assert response.status_code == 400
        assert "AI model" in response.json()["detail"]

    def test_analyze_failures_handles_analysis_exception(self, test_client) -> None:
        """Test that when analyze_failure_group raises, endpoint returns status 'failed'."""
        with patch("jenkins_job_insight.main.RepositoryManager") as mock_repo_cls:
            mock_repo_instance = mock_repo_cls.return_value
            mock_repo_instance.clone.return_value = None
            mock_repo_instance.cleanup.return_value = None

            with patch(
                "jenkins_job_insight.main.get_failure_signature",
                return_value="sig-a",
            ):
                with patch(
                    "jenkins_job_insight.main.run_parallel_with_limit",
                    new_callable=AsyncMock,
                ) as mock_parallel:

                    async def raise_after_closing(coroutines, **kwargs):
                        for coro in coroutines:
                            coro.close()
                        raise RuntimeError("AI CLI crashed")

                    mock_parallel.side_effect = raise_after_closing

                    response = test_client.post(
                        "/analyze-failures",
                        json={
                            "failures": [
                                {
                                    "test_name": "test_foo",
                                    "error_message": "assert False",
                                }
                            ],
                            "ai_provider": "claude",
                            "ai_model": "test-model",
                        },
                    )
                    assert response.status_code == 200
                    data = response.json()
                    assert data["status"] == "failed"
                    assert "AI CLI crashed" in data["summary"]

    def test_analyze_failures_partial_failure(self, test_client) -> None:
        """Test that when some failure groups succeed and others raise, the endpoint returns partial results.

        Posts 2 failures with different signatures. run_parallel_with_limit returns
        one successful analysis list and one RuntimeError exception. Verifies the
        endpoint returns status 'completed' with only the successful analysis and
        the summary reflects the correct counts.
        """
        mock_analysis = FailureAnalysis(
            test_name="test_a",
            error="err",
            analysis=AnalysisDetail(
                classification="CODE ISSUE",
                details="analysis",
            ),
        )

        with patch("jenkins_job_insight.main.RepositoryManager") as mock_repo_cls:
            mock_repo_instance = mock_repo_cls.return_value
            mock_repo_instance.clone.return_value = None
            mock_repo_instance.cleanup.return_value = None

            with patch(
                "jenkins_job_insight.main.get_failure_signature",
                side_effect=["sig-a", "sig-b"],
            ):
                with patch(
                    "jenkins_job_insight.main.run_parallel_with_limit",
                    new_callable=AsyncMock,
                ) as mock_parallel:

                    async def run_partial_failure(coroutines, **kwargs):
                        for coro in coroutines:
                            coro.close()
                        return [
                            [mock_analysis],
                            RuntimeError("AI CLI crashed"),
                        ]

                    mock_parallel.side_effect = run_partial_failure

                    with patch(
                        "jenkins_job_insight.main.save_result",
                        new_callable=AsyncMock,
                    ):
                        with patch(
                            "jenkins_job_insight.main.update_status",
                            new_callable=AsyncMock,
                        ):
                            response = test_client.post(
                                "/analyze-failures",
                                json={
                                    "failures": [
                                        {
                                            "test_name": "test_a",
                                            "error_message": "err",
                                            "stack_trace": "File a.py, line 1",
                                        },
                                        {
                                            "test_name": "test_b",
                                            "error_message": "different err",
                                            "stack_trace": "File b.py, line 2",
                                        },
                                    ],
                                    "ai_provider": "claude",
                                    "ai_model": "test-model",
                                },
                            )
                            assert response.status_code == 200
                            data = response.json()
                            assert data["status"] == "completed"
                            assert len(data["failures"]) == 1
                            assert data["failures"][0]["test_name"] == "test_a"
                            assert "2 test failures" in data["summary"]
                            assert "2 unique errors" in data["summary"]
                            assert "1 analyzed successfully" in data["summary"]

    def test_analyze_failures_deduplication(self, test_client) -> None:
        """Test that failures sharing the same signature are deduplicated.

        Three failures are submitted where two share a signature. Verify
        analyze_failure_group is called twice (once per unique signature),
        not three times.
        """
        mock_analysis_a = FailureAnalysis(
            test_name="test_foo",
            error="assert False",
            analysis=AnalysisDetail(
                classification="CODE ISSUE",
                details="assertion failure",
            ),
        )
        mock_analysis_b = FailureAnalysis(
            test_name="test_bar",
            error="KeyError: x",
            analysis=AnalysisDetail(
                classification="CODE ISSUE",
                details="missing key",
            ),
        )

        with patch("jenkins_job_insight.main.RepositoryManager") as mock_repo_cls:
            mock_repo_instance = mock_repo_cls.return_value
            mock_repo_instance.clone.return_value = None
            mock_repo_instance.cleanup.return_value = None

            # Return same signature for first two failures, different for third
            signatures = iter(["sig-a", "sig-a", "sig-b"])
            with patch(
                "jenkins_job_insight.main.get_failure_signature",
                side_effect=lambda f: next(signatures),
            ):
                with patch(
                    "jenkins_job_insight.main.analyze_failure_group",
                    new_callable=AsyncMock,
                ) as mock_analyze_group:
                    mock_analyze_group.side_effect = [
                        [mock_analysis_a, mock_analysis_a],
                        [mock_analysis_b],
                    ]

                    with patch(
                        "jenkins_job_insight.main.run_parallel_with_limit",
                        new_callable=AsyncMock,
                    ) as mock_parallel:
                        # Simulate run_parallel_with_limit calling the coroutines
                        async def run_coroutines(coroutines, **kwargs):
                            results = []
                            for coro in coroutines:
                                results.append(await coro)
                            return results

                        mock_parallel.side_effect = run_coroutines

                        response = test_client.post(
                            "/analyze-failures",
                            json={
                                "failures": [
                                    {
                                        "test_name": "test_foo",
                                        "error_message": "assert False",
                                        "stack_trace": "File test.py, line 10",
                                    },
                                    {
                                        "test_name": "test_baz",
                                        "error_message": "assert False",
                                        "stack_trace": "File test.py, line 10",
                                    },
                                    {
                                        "test_name": "test_bar",
                                        "error_message": "KeyError: x",
                                        "stack_trace": "File test.py, line 20",
                                    },
                                ],
                                "ai_provider": "claude",
                                "ai_model": "test-model",
                            },
                        )
                        assert response.status_code == 200
                        data = response.json()
                        assert data["status"] == "completed"
                        # analyze_failure_group called twice: once for sig-a group, once for sig-b
                        assert mock_analyze_group.call_count == 2


class TestAnalyzeFailuresRawXml:
    """Tests for the POST /analyze-failures endpoint with raw_xml input."""

    SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite" tests="2" failures="1" errors="0">
    <testcase classname="tests.test_auth" name="test_login" time="0.5">
        <failure message="assert False" type="AssertionError">
            at tests/test_auth.py:42
        </failure>
    </testcase>
    <testcase classname="tests.test_auth" name="test_logout" time="0.1"/>
</testsuite>"""

    SAMPLE_XML_NO_FAILURES = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite" tests="1" failures="0" errors="0">
    <testcase classname="tests.test_auth" name="test_ok" time="0.1"/>
</testsuite>"""

    def test_raw_xml_success(self, test_client) -> None:
        """Test that raw_xml with failures returns enriched XML."""
        mock_analysis = FailureAnalysis(
            test_name="tests.test_auth.test_login",
            error="assert False",
            analysis=AnalysisDetail(
                classification="CODE ISSUE",
                details="Test assertion failed",
            ),
        )

        with patch("jenkins_job_insight.main.RepositoryManager") as mock_repo_cls:
            mock_repo_instance = mock_repo_cls.return_value
            mock_repo_instance.clone.return_value = None
            mock_repo_instance.cleanup.return_value = None

            with patch(
                "jenkins_job_insight.main.run_parallel_with_limit",
                new_callable=AsyncMock,
            ) as mock_parallel:

                async def return_mock_results(coroutines, **kwargs):
                    for coro in coroutines:
                        coro.close()
                    return [[mock_analysis]]

                mock_parallel.side_effect = return_mock_results

                response = test_client.post(
                    "/analyze-failures",
                    json={
                        "raw_xml": self.SAMPLE_XML,
                        "ai_provider": "claude",
                        "ai_model": "test-model",
                    },
                )
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "completed"
                assert data["enriched_xml"] is not None
                assert "<?xml" in data["enriched_xml"]
                assert len(data["failures"]) == 1

    def test_raw_xml_no_failures(self, test_client) -> None:
        """Test that raw_xml with no failures returns the original XML."""
        response = test_client.post(
            "/analyze-failures",
            json={
                "raw_xml": self.SAMPLE_XML_NO_FAILURES,
                "ai_provider": "claude",
                "ai_model": "test-model",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert "No test failures" in data["summary"]
        assert data["enriched_xml"] is not None

    def test_raw_xml_invalid_xml(self, test_client) -> None:
        """Test that invalid XML returns 400."""
        response = test_client.post(
            "/analyze-failures",
            json={
                "raw_xml": "this is not valid xml <<<<",
                "ai_provider": "claude",
                "ai_model": "test-model",
            },
        )
        assert response.status_code == 400
        assert "Invalid XML" in response.json()["detail"]

    def test_raw_xml_and_failures_mutual_exclusion(self, test_client) -> None:
        """Test that providing both raw_xml and failures returns 422."""
        response = test_client.post(
            "/analyze-failures",
            json={
                "raw_xml": self.SAMPLE_XML,
                "failures": [
                    {
                        "test_name": "test_foo",
                        "error_message": "assert False",
                    }
                ],
                "ai_provider": "claude",
                "ai_model": "test-model",
            },
        )
        assert response.status_code == 422

    def test_neither_raw_xml_nor_failures_returns_422(self, test_client) -> None:
        """Test that providing neither raw_xml nor failures returns 422."""
        response = test_client.post(
            "/analyze-failures",
            json={
                "ai_provider": "claude",
                "ai_model": "test-model",
            },
        )
        assert response.status_code == 422

    def test_failures_mode_still_works(self, test_client) -> None:
        """Test that existing failures mode is backwards compatible."""
        mock_analysis = FailureAnalysis(
            test_name="test_foo",
            error="assert False",
            analysis=AnalysisDetail(
                classification="CODE ISSUE",
                details="Test assertion failed",
            ),
        )

        with patch("jenkins_job_insight.main.RepositoryManager") as mock_repo_cls:
            mock_repo_instance = mock_repo_cls.return_value
            mock_repo_instance.clone.return_value = None
            mock_repo_instance.cleanup.return_value = None

            with patch(
                "jenkins_job_insight.main.run_parallel_with_limit",
                new_callable=AsyncMock,
            ) as mock_parallel:

                async def return_mock_results(coroutines, **kwargs):
                    for coro in coroutines:
                        coro.close()
                    return [[mock_analysis]]

                mock_parallel.side_effect = return_mock_results

                response = test_client.post(
                    "/analyze-failures",
                    json={
                        "failures": [
                            {
                                "test_name": "test_foo",
                                "error_message": "assert False",
                                "stack_trace": "File test.py, line 10",
                            }
                        ],
                        "ai_provider": "claude",
                        "ai_model": "test-model",
                    },
                )
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "completed"
                assert data["enriched_xml"] is None  # No enriched_xml in failures mode

    def test_raw_xml_enriched_xml_contains_analysis(self, test_client) -> None:
        """Test that enriched_xml contains ai_classification properties."""
        mock_analysis = FailureAnalysis(
            test_name="tests.test_auth.test_login",
            error="assert False",
            analysis=AnalysisDetail(
                classification="PRODUCT BUG",
                details="Auth service down",
            ),
        )

        with patch("jenkins_job_insight.main.RepositoryManager") as mock_repo_cls:
            mock_repo_instance = mock_repo_cls.return_value
            mock_repo_instance.clone.return_value = None
            mock_repo_instance.cleanup.return_value = None

            with patch(
                "jenkins_job_insight.main.run_parallel_with_limit",
                new_callable=AsyncMock,
            ) as mock_parallel:

                async def return_mock_results(coroutines, **kwargs):
                    for coro in coroutines:
                        coro.close()
                    return [[mock_analysis]]

                mock_parallel.side_effect = return_mock_results

                response = test_client.post(
                    "/analyze-failures",
                    json={
                        "raw_xml": self.SAMPLE_XML,
                        "ai_provider": "claude",
                        "ai_model": "test-model",
                    },
                )
                data = response.json()
                assert data["enriched_xml"] is not None
                assert "ai_classification" in data["enriched_xml"]
                assert "PRODUCT BUG" in data["enriched_xml"]


class TestResultsEndpoints:
    """Tests for the /results endpoints."""

    async def test_get_result_existing(self, test_client, temp_db_path: Path) -> None:
        """Test retrieving an existing result."""
        with patch.object(storage, "DB_PATH", temp_db_path):
            await storage.init_db()
            await storage.save_result(
                job_id="job-123",
                jenkins_url="https://jenkins.example.com/job/test/1/",
                status="completed",
                result={"summary": "Done"},
            )

            response = test_client.get("/results/job-123")
            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] == "job-123"
            assert data["base_url"] == "http://testserver"
            assert data["result_url"] == "http://testserver/results/job-123"

    def test_get_result_not_found(self, test_client) -> None:
        """Test retrieving non-existent result returns 404."""
        response = test_client.get("/results/non-existent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_list_results(self, test_client, temp_db_path: Path) -> None:
        """Test listing results."""
        with patch.object(storage, "DB_PATH", temp_db_path):
            await storage.init_db()
            for i in range(3):
                await storage.save_result(
                    job_id=f"job-{i}",
                    jenkins_url=f"https://jenkins.example.com/job/test/{i}/",
                    status="completed",
                )

            response = test_client.get("/results")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 3

    async def test_list_results_with_limit(
        self, test_client, temp_db_path: Path
    ) -> None:
        """Test listing results with limit parameter."""
        with patch.object(storage, "DB_PATH", temp_db_path):
            await storage.init_db()
            for i in range(10):
                await storage.save_result(
                    job_id=f"job-limit-{i}",
                    jenkins_url=f"https://jenkins.example.com/job/test/{i}/",
                    status="completed",
                )

            response = test_client.get("/results?limit=5")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 5

    def test_list_results_limit_max(self, test_client) -> None:
        """Test that limit is capped at 100."""
        response = test_client.get("/results?limit=200")
        assert response.status_code == 422  # Validation error

    def test_list_results_empty(self, test_client) -> None:
        """Test listing results when empty."""
        response = test_client.get("/results")
        assert response.status_code == 200
        assert response.json() == []

    async def test_get_result_json_format_default(
        self, test_client, temp_db_path: Path
    ) -> None:
        """Test that default format returns JSON."""
        with patch.object(storage, "DB_PATH", temp_db_path):
            await storage.init_db()
            await storage.save_result(
                job_id="json-job-456",
                jenkins_url="https://jenkins.example.com/job/test/2/",
                status="completed",
                result={"summary": "Done"},
            )
            response = test_client.get("/results/json-job-456")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "json-job-456"


class TestAppLifespan:
    """Tests for application lifespan events."""

    def test_app_initializes_db_on_startup(
        self, mock_settings, temp_db_path: Path
    ) -> None:
        """Test that database is initialized on app startup."""
        with patch.object(storage, "DB_PATH", temp_db_path):
            from starlette.testclient import TestClient
            from jenkins_job_insight.main import app

            with TestClient(app):
                # After startup, DB should exist with results table
                assert temp_db_path.exists()


class TestOpenAPISchema:
    """Tests for OpenAPI schema."""

    def test_openapi_schema_available(self, test_client) -> None:
        """Test that OpenAPI schema is available."""
        response = test_client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "Jenkins Job Insight"
        assert schema["info"]["version"] == "0.1.0"

    def test_docs_available(self, test_client) -> None:
        """Test that docs endpoint is available."""
        response = test_client.get("/docs")
        assert response.status_code == 200


class TestSpaRoutes:
    """Tests for the React SPA route handlers."""

    @pytest.mark.parametrize(
        ("path", "follow_redirects"),
        [
            ("/dashboard", True),
            ("/register", True),
            ("/", False),
            ("/some/unknown/route", True),
        ],
    )
    def test_spa_route_serves_spa_or_404(
        self, test_client, path: str, follow_redirects: bool
    ) -> None:
        response = test_client.get(path, follow_redirects=follow_redirects)
        assert response.status_code in (200, 404)


class TestApiDashboardEndpoint:
    """Tests for the GET /api/dashboard endpoint."""

    def test_api_dashboard_returns_empty_list(self, test_client) -> None:
        """Test that GET /api/dashboard returns an empty list when no jobs exist."""
        response = test_client.get("/api/dashboard")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert data == []

    async def test_api_dashboard_returns_stored_jobs(
        self, test_client, temp_db_path: Path
    ) -> None:
        """Test that GET /api/dashboard returns stored jobs."""
        with patch.object(storage, "DB_PATH", temp_db_path):
            await storage.init_db()
            for i in range(3):
                await storage.save_result(
                    job_id=f"api-dash-{i}",
                    jenkins_url=f"https://jenkins.example.com/job/test/{i}/",
                    status="completed",
                    result={
                        "job_name": f"test-job-{i}",
                        "build_number": i,
                        "failures": [],
                    },
                )

            response = test_client.get("/api/dashboard")
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 3

    async def test_api_dashboard_limit_parameter(
        self, test_client, temp_db_path: Path
    ) -> None:
        """Test that the limit query parameter caps the number of results."""
        with patch.object(storage, "DB_PATH", temp_db_path):
            await storage.init_db()
            for i in range(10):
                await storage.save_result(
                    job_id=f"api-dash-limit-{i}",
                    jenkins_url=f"https://jenkins.example.com/job/test/{i}/",
                    status="completed",
                )

            response = test_client.get("/api/dashboard?limit=3")
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 3

    def test_api_dashboard_limit_exceeds_max(self, test_client) -> None:
        """Test that limit above the maximum (2000) returns a validation error."""
        response = test_client.get("/api/dashboard?limit=5000")
        assert response.status_code == 422

    def test_api_dashboard_default_limit(self, test_client) -> None:
        """Test that the default limit is 500 (no query parameter needed)."""
        with patch(
            "jenkins_job_insight.main.list_results_for_dashboard",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = []
            response = test_client.get("/api/dashboard")
            assert response.status_code == 200
            mock_list.assert_called_once_with(limit=500)

    async def test_api_dashboard_includes_job_metadata(
        self, test_client, temp_db_path: Path
    ) -> None:
        """Test that returned items include expected metadata fields."""
        with patch.object(storage, "DB_PATH", temp_db_path):
            await storage.init_db()
            await storage.save_result(
                job_id="api-dash-meta",
                jenkins_url="https://jenkins.example.com/job/test/1/",
                status="completed",
                result={
                    "job_name": "my-pipeline",
                    "build_number": 42,
                    "failures": [
                        {
                            "test_name": "test_fail",
                            "error": "assert False",
                            "analysis": {"classification": "CODE ISSUE"},
                        }
                    ],
                },
            )

            response = test_client.get("/api/dashboard")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            item = data[0]
            assert item["job_id"] == "api-dash-meta"
            assert item["status"] == "completed"
            assert "created_at" in item


class TestFaviconEndpoint:
    """Tests for the GET /favicon.ico endpoint."""

    def test_favicon_returns_svg(self, test_client) -> None:
        """Test that GET /favicon.ico returns 200 with image/svg+xml content type."""
        response = test_client.get("/favicon.ico")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/svg+xml"

    def test_favicon_contains_svg_content(self, test_client) -> None:
        """Test that the favicon response body contains a valid SVG tag."""
        response = test_client.get("/favicon.ico")
        assert response.status_code == 200
        assert "<svg" in response.text

    def test_favicon_has_cache_control(self, test_client) -> None:
        """Test that the favicon response has a Cache-Control header with max-age."""
        response = test_client.get("/favicon.ico")
        assert response.status_code == 200
        cache_control = response.headers.get("cache-control", "")
        assert "max-age" in cache_control


class TestCommentEndpoints:
    @pytest.mark.asyncio
    async def test_add_comment(self, test_client):
        from jenkins_job_insight import storage

        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_foo",
                    "error": "some error",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-test-1", "http://jenkins", "completed", result_data
        )
        response = test_client.post(
            "/results/job-test-1/comments",
            json={"test_name": "test_foo", "comment": "opened bug"},
        )
        assert response.status_code == 201
        assert "id" in response.json()

    @pytest.mark.asyncio
    async def test_get_comments(self, test_client):
        from jenkins_job_insight import storage

        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_foo",
                    "error": "err",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-test-2", "http://jenkins", "completed", result_data
        )
        await storage.add_comment("job-test-2", "test_foo", "comment 1")
        response = test_client.get("/results/job-test-2/comments")
        assert response.status_code == 200
        data = response.json()
        assert "comments" in data
        assert "reviews" in data
        assert len(data["comments"]) == 1

    @pytest.mark.asyncio
    async def test_add_comment_nonexistent_job(self, test_client):
        response = test_client.post(
            "/results/nonexistent/comments",
            json={"test_name": "test_foo", "comment": "test"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_add_comment_invalid_test_name(self, test_client):
        from jenkins_job_insight import storage

        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_foo",
                    "error": "err",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-test-3", "http://jenkins", "completed", result_data
        )
        response = test_client.post(
            "/results/job-test-3/comments",
            json={"test_name": "nonexistent_test", "comment": "test"},
        )
        assert response.status_code == 400


class TestReviewedEndpoint:
    @pytest.mark.asyncio
    async def test_set_reviewed(self, test_client):
        from jenkins_job_insight import storage

        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_foo",
                    "error": "err",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-rev-1", "http://jenkins", "completed", result_data
        )
        response = test_client.put(
            "/results/job-rev-1/reviewed",
            json={"test_name": "test_foo", "reviewed": True},
        )
        assert response.status_code == 200
        put_data = response.json()
        assert put_data["status"] == "ok"
        assert "reviewed_by" in put_data
        response = test_client.get("/results/job-rev-1/comments")
        data = response.json()
        assert "test_foo" in data["reviews"]
        assert data["reviews"]["test_foo"]["reviewed"] is True
        assert "username" in data["reviews"]["test_foo"]

    @pytest.mark.asyncio
    async def test_set_reviewed_nonexistent_job(self, test_client):
        response = test_client.put(
            "/results/nonexistent/reviewed",
            json={"test_name": "test_foo", "reviewed": True},
        )
        assert response.status_code == 404


class TestReviewStatusEndpoint:
    @pytest.mark.asyncio
    async def test_get_review_status(self, test_client):
        from jenkins_job_insight import storage

        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_a",
                    "error": "err",
                    "analysis": {"classification": "CODE ISSUE"},
                },
                {
                    "test_name": "test_b",
                    "error": "err",
                    "analysis": {"classification": "PRODUCT BUG"},
                },
            ],
        }
        await storage.save_result(
            "job-rs-1", "http://jenkins", "completed", result_data
        )
        await storage.set_reviewed("job-rs-1", "test_a", reviewed=True)
        await storage.add_comment("job-rs-1", "test_a", "bug opened")
        response = test_client.get("/results/job-rs-1/review-status")
        assert response.status_code == 200
        data = response.json()
        assert data["total_failures"] == 2
        assert data["reviewed_count"] == 1
        assert data["comment_count"] == 1


class TestChildScopeValidation:
    @pytest.mark.asyncio
    async def test_comment_child_job_without_build_number_accepted(self, test_client):
        """child_job_name with child_build_number=0 should be accepted (match any build)."""
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [],
            "child_job_analyses": [
                {
                    "job_name": "child-1",
                    "build_number": 5,
                    "failures": [
                        {
                            "test_name": "test_foo",
                            "error": "err",
                            "analysis": {"classification": "CODE ISSUE"},
                        }
                    ],
                    "failed_children": [],
                }
            ],
        }
        await storage.save_result(
            "job-val-1", "http://jenkins", "completed", result_data
        )
        response = test_client.post(
            "/results/job-val-1/comments",
            json={
                "test_name": "test_foo",
                "child_job_name": "child-1",
                "comment": "test",
            },
        )
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_comment_build_number_without_child_job_rejected(self, test_client):
        """child_build_number without child_job_name should be rejected (422)."""
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_foo",
                    "error": "err",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-val-2", "http://jenkins", "completed", result_data
        )
        response = test_client.post(
            "/results/job-val-2/comments",
            json={
                "test_name": "test_foo",
                "child_build_number": 42,
                "comment": "test",
            },
        )
        assert response.status_code == 422


class TestPreviewGithubIssue:
    """Tests for POST /results/{job_id}/preview-github-issue."""

    @pytest.mark.asyncio
    async def test_preview_returns_title_and_body(self, test_client):
        """POST /results/{job_id}/preview-github-issue returns generated content."""
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_login_success",
                    "error": "AssertionError: Expected 200, got 500",
                    "analysis": {
                        "classification": "CODE ISSUE",
                        "details": "Login handler missing catch",
                    },
                }
            ],
        }
        await storage.save_result(
            "job-preview-gh", "http://jenkins", "completed", result_data
        )
        with patch(
            "jenkins_job_insight.main.generate_github_issue_content"
        ) as mock_gen:
            mock_gen.return_value = {
                "title": "Fix: login handler missing catch",
                "body": "## Test Failure\n\nDetails...",
            }
            with patch("jenkins_job_insight.main.search_github_duplicates") as mock_dup:
                mock_dup.return_value = []
                response = test_client.post(
                    "/results/job-preview-gh/preview-github-issue",
                    json={"test_name": "test_login_success"},
                )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Fix: login handler missing catch"
        assert "body" in data
        assert "similar_issues" in data

    @pytest.mark.asyncio
    async def test_preview_not_found(self, test_client):
        response = test_client.post(
            "/results/nonexistent/preview-github-issue",
            json={"test_name": "tests.TestA.test_one"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_preview_invalid_test(self, test_client):
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_login_success",
                    "error": "err",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-preview-gh-2", "http://jenkins", "completed", result_data
        )
        response = test_client.post(
            "/results/job-preview-gh-2/preview-github-issue",
            json={"test_name": "nonexistent_test"},
        )
        assert response.status_code == 400


class TestPreviewJiraBug:
    """Tests for POST /results/{job_id}/preview-jira-bug."""

    @pytest.mark.asyncio
    async def test_preview_returns_title_and_body(self, test_client):
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_login_success",
                    "error": "TimeoutError",
                    "analysis": {
                        "classification": "PRODUCT BUG",
                        "details": "DNS timeout",
                    },
                }
            ],
        }
        await storage.save_result(
            "job-preview-jira", "http://jenkins", "completed", result_data
        )
        with patch("jenkins_job_insight.main.generate_jira_bug_content") as mock_gen:
            mock_gen.return_value = {
                "title": "DNS timeout on internal resolver",
                "body": "h2. Summary\n\nDNS resolution fails",
            }
            with patch("jenkins_job_insight.main.search_jira_duplicates") as mock_dup:
                mock_dup.return_value = []
                response = test_client.post(
                    "/results/job-preview-jira/preview-jira-bug",
                    json={"test_name": "test_login_success"},
                )
        assert response.status_code == 200
        data = response.json()
        assert data["title"]
        assert data["body"]


class TestCreateGithubIssue:
    """Tests for POST /results/{job_id}/create-github-issue."""

    @pytest.mark.asyncio
    async def test_creates_issue_and_adds_comment(self, test_client):
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_login_success",
                    "error": "err",
                    "error_signature": "sig123",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-create-gh", "http://jenkins", "completed", result_data
        )
        with patch("jenkins_job_insight.main.create_github_issue") as mock_create:
            mock_create.return_value = {
                "url": "https://github.com/org/repo/issues/99",
                "number": 99,
            }
            with patch.dict(
                os.environ,
                {
                    "TESTS_REPO_URL": "https://github.com/org/repo",
                    "GITHUB_TOKEN": "ghp_test",
                },
            ):
                from jenkins_job_insight.config import get_settings

                get_settings.cache_clear()
                response = test_client.post(
                    "/results/job-create-gh/create-github-issue",
                    json={
                        "test_name": "test_login_success",
                        "title": "Bug: login fails",
                        "body": "## Details\nLogin returns 500",
                    },
                )
                get_settings.cache_clear()
        assert response.status_code == 201
        data = response.json()
        assert "https://github.com" in data["url"]
        assert data["comment_id"] > 0

    @pytest.mark.asyncio
    async def test_create_missing_config_returns_400(self, test_client):
        """Creating a GitHub issue without TESTS_REPO_URL/GITHUB_TOKEN returns 400."""
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_foo",
                    "error": "err",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-create-gh-noconfig", "http://jenkins", "completed", result_data
        )
        response = test_client.post(
            "/results/job-create-gh-noconfig/create-github-issue",
            json={
                "test_name": "test_foo",
                "title": "Bug",
                "body": "Details",
            },
        )
        assert response.status_code == 400
        assert "TESTS_REPO_URL" in response.json()["detail"]


class TestCreateJiraBug:
    """Tests for POST /results/{job_id}/create-jira-bug."""

    @pytest.mark.asyncio
    async def test_creates_bug_and_adds_comment(self, test_client):
        from unittest.mock import PropertyMock
        from jenkins_job_insight.config import Settings

        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_login_success",
                    "error": "err",
                    "error_signature": "sig456",
                    "analysis": {"classification": "PRODUCT BUG"},
                }
            ],
        }
        await storage.save_result(
            "job-create-jira", "http://jenkins", "completed", result_data
        )
        with patch("jenkins_job_insight.main.create_jira_bug") as mock_create:
            mock_create.return_value = {
                "key": "PROJ-456",
                "url": "https://jira.example.com/browse/PROJ-456",
            }
            # Mock settings to have jira_enabled=True
            with patch.object(
                Settings, "jira_enabled", new_callable=PropertyMock, return_value=True
            ):
                response = test_client.post(
                    "/results/job-create-jira/create-jira-bug",
                    json={
                        "test_name": "test_login_success",
                        "title": "DNS timeout",
                        "body": "DNS resolution fails",
                    },
                )
        assert response.status_code == 201
        data = response.json()
        assert data["key"] == "PROJ-456"
        assert data["comment_id"] > 0

    @pytest.mark.asyncio
    async def test_create_jira_not_configured_returns_400(self, test_client):
        """Creating a Jira bug without Jira configured returns 400."""
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_foo",
                    "error": "err",
                    "analysis": {"classification": "PRODUCT BUG"},
                }
            ],
        }
        await storage.save_result(
            "job-create-jira-noconfig", "http://jenkins", "completed", result_data
        )
        response = test_client.post(
            "/results/job-create-jira-noconfig/create-jira-bug",
            json={
                "test_name": "test_foo",
                "title": "Bug",
                "body": "Details",
            },
        )
        assert response.status_code == 400


class TestOverrideClassification:
    """Tests for PUT /results/{job_id}/override-classification."""

    @pytest.mark.asyncio
    async def test_overrides_classification(self, test_client):
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_login_success",
                    "error": "err",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-override-1", "http://jenkins", "completed", result_data
        )
        with patch(
            "jenkins_job_insight.storage.override_classification"
        ) as mock_override:
            response = test_client.put(
                "/results/job-override-1/override-classification",
                json={
                    "test_name": "test_login_success",
                    "classification": "PRODUCT BUG",
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["classification"] == "PRODUCT BUG"
        mock_override.assert_called_once()

    @pytest.mark.asyncio
    async def test_override_invalid_test(self, test_client):
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_login_success",
                    "error": "err",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-override-2", "http://jenkins", "completed", result_data
        )
        response = test_client.put(
            "/results/job-override-2/override-classification",
            json={
                "test_name": "nonexistent_test",
                "classification": "CODE ISSUE",
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_override_nonexistent_job(self, test_client):
        response = test_client.put(
            "/results/nonexistent-job/override-classification",
            json={
                "test_name": "test_foo",
                "classification": "PRODUCT BUG",
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_override_invalid_classification(self, test_client):
        """Invalid classification values should be rejected by Pydantic validation."""
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_foo",
                    "error": "err",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-override-3", "http://jenkins", "completed", result_data
        )
        response = test_client.put(
            "/results/job-override-3/override-classification",
            json={
                "test_name": "test_foo",
                "classification": "UNKNOWN",
            },
        )
        assert response.status_code == 422


class TestBugCreationIntegration:
    """Integration tests for the full bug creation flow."""

    @pytest.mark.asyncio
    async def test_full_flow_github(self, test_client):
        """Test full flow: preview -> create -> verify comment."""
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_login_success",
                    "error": "err",
                    "error_signature": "sig789",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-integ-gh", "http://jenkins", "completed", result_data
        )
        with (
            patch("jenkins_job_insight.main.generate_github_issue_content") as mock_gen,
            patch("jenkins_job_insight.main.search_github_duplicates") as mock_dup,
            patch("jenkins_job_insight.main.create_github_issue") as mock_create,
        ):
            mock_gen.return_value = {"title": "Bug title", "body": "Bug body"}
            mock_dup.return_value = []
            mock_create.return_value = {
                "url": "https://github.com/org/repo/issues/1",
                "number": 1,
            }

            # Preview
            preview_resp = test_client.post(
                "/results/job-integ-gh/preview-github-issue",
                json={"test_name": "test_login_success"},
            )
            assert preview_resp.status_code == 200

            # Create (need settings with TESTS_REPO_URL and GITHUB_TOKEN)
            with patch.dict(
                os.environ,
                {
                    "TESTS_REPO_URL": "https://github.com/org/repo",
                    "GITHUB_TOKEN": "ghp_test",
                },
            ):
                from jenkins_job_insight.config import get_settings

                get_settings.cache_clear()
                create_resp = test_client.post(
                    "/results/job-integ-gh/create-github-issue",
                    json={
                        "test_name": "test_login_success",
                        "title": "Bug title",
                        "body": "Bug body",
                    },
                )
                get_settings.cache_clear()
            assert create_resp.status_code == 201
            data = create_resp.json()
            assert data["comment_id"] > 0

            # Verify comment was added
            comments_resp = test_client.get("/results/job-integ-gh/comments")
            assert comments_resp.status_code == 200
            comments = comments_resp.json()["comments"]
            assert any("github.com" in c["comment"] for c in comments)

    @pytest.mark.asyncio
    async def test_override_then_verify(self, test_client):
        """Test: override classification persists and is visible on GET."""
        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_login_success",
                    "error": "err",
                    "analysis": {"classification": "PRODUCT BUG"},
                }
            ],
        }
        await storage.save_result(
            "job-integ-override", "http://jenkins", "completed", result_data
        )
        resp = test_client.put(
            "/results/job-integ-override/override-classification",
            json={
                "test_name": "test_login_success",
                "classification": "CODE ISSUE",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["classification"] == "CODE ISSUE"

        # Verify the override persisted by fetching the result
        get_resp = test_client.get("/results/job-integ-override")
        assert get_resp.status_code == 200


class TestCreateGithubIssueApiErrors:
    """Finding 4: create-github-issue should catch external API errors and return 502."""

    @pytest.mark.asyncio
    async def test_github_api_http_error_returns_502(self, test_client):
        """HTTPStatusError from GitHub API should surface as 502."""
        import httpx

        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_foo",
                    "error": "err",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-gh-err", "http://jenkins", "completed", result_data
        )
        with patch("jenkins_job_insight.main.create_github_issue") as mock_create:
            mock_create.side_effect = httpx.HTTPStatusError(
                "Forbidden",
                request=httpx.Request("POST", "https://api.github.com"),
                response=httpx.Response(403),
            )
            with patch.dict(
                os.environ,
                {
                    "TESTS_REPO_URL": "https://github.com/org/repo",
                    "GITHUB_TOKEN": "ghp_test",
                },
            ):
                from jenkins_job_insight.config import get_settings

                get_settings.cache_clear()
                response = test_client.post(
                    "/results/job-gh-err/create-github-issue",
                    json={
                        "test_name": "test_foo",
                        "title": "Bug",
                        "body": "Details",
                    },
                )
                get_settings.cache_clear()
        assert response.status_code == 502
        assert "GitHub API error" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_github_api_request_error_returns_502(self, test_client):
        """RequestError (network unreachable) from GitHub should surface as 502."""
        import httpx

        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_foo",
                    "error": "err",
                    "analysis": {"classification": "CODE ISSUE"},
                }
            ],
        }
        await storage.save_result(
            "job-gh-net-err", "http://jenkins", "completed", result_data
        )
        with patch("jenkins_job_insight.main.create_github_issue") as mock_create:
            mock_create.side_effect = httpx.RequestError(
                "Connection refused",
                request=httpx.Request("POST", "https://api.github.com"),
            )
            with patch.dict(
                os.environ,
                {
                    "TESTS_REPO_URL": "https://github.com/org/repo",
                    "GITHUB_TOKEN": "ghp_test",
                },
            ):
                from jenkins_job_insight.config import get_settings

                get_settings.cache_clear()
                response = test_client.post(
                    "/results/job-gh-net-err/create-github-issue",
                    json={
                        "test_name": "test_foo",
                        "title": "Bug",
                        "body": "Details",
                    },
                )
                get_settings.cache_clear()
        assert response.status_code == 502
        assert "GitHub API unreachable" in response.json()["detail"]


class TestCreateJiraBugApiErrors:
    """Finding 4: create-jira-bug should catch external API errors and return 502."""

    @pytest.mark.asyncio
    async def test_jira_api_http_error_returns_502(self, test_client):
        """HTTPStatusError from Jira API should surface as 502."""
        import httpx
        from unittest.mock import PropertyMock
        from jenkins_job_insight.config import Settings

        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_foo",
                    "error": "err",
                    "analysis": {"classification": "PRODUCT BUG"},
                }
            ],
        }
        await storage.save_result(
            "job-jira-err", "http://jenkins", "completed", result_data
        )
        with patch("jenkins_job_insight.main.create_jira_bug") as mock_create:
            mock_create.side_effect = httpx.HTTPStatusError(
                "Forbidden",
                request=httpx.Request("POST", "https://jira.example.com"),
                response=httpx.Response(403),
            )
            with patch.object(
                Settings, "jira_enabled", new_callable=PropertyMock, return_value=True
            ):
                response = test_client.post(
                    "/results/job-jira-err/create-jira-bug",
                    json={
                        "test_name": "test_foo",
                        "title": "Bug",
                        "body": "Details",
                    },
                )
        assert response.status_code == 502
        assert "Jira API error" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_jira_api_request_error_returns_502(self, test_client):
        """RequestError (network unreachable) from Jira should surface as 502."""
        import httpx
        from unittest.mock import PropertyMock
        from jenkins_job_insight.config import Settings

        result_data = {
            "status": "completed",
            "summary": "",
            "failures": [
                {
                    "test_name": "test_foo",
                    "error": "err",
                    "analysis": {"classification": "PRODUCT BUG"},
                }
            ],
        }
        await storage.save_result(
            "job-jira-net-err", "http://jenkins", "completed", result_data
        )
        with patch("jenkins_job_insight.main.create_jira_bug") as mock_create:
            mock_create.side_effect = httpx.RequestError(
                "Connection refused",
                request=httpx.Request("POST", "https://jira.example.com"),
            )
            with patch.object(
                Settings, "jira_enabled", new_callable=PropertyMock, return_value=True
            ):
                response = test_client.post(
                    "/results/job-jira-net-err/create-jira-bug",
                    json={
                        "test_name": "test_foo",
                        "title": "Bug",
                        "body": "Details",
                    },
                )
        assert response.status_code == 502
        assert "Jira API unreachable" in response.json()["detail"]


class TestHistoryEndpoints:
    """Tests for the /history/* endpoints."""

    @pytest.mark.asyncio
    async def test_get_test_history(self, test_client) -> None:
        """Test that /history/test/{test_name} returns expected structure and values."""
        response = test_client.get("/history/test/some.test.name")
        assert response.status_code == 200
        data = response.json()
        assert data["test_name"] == "some.test.name"
        # Verify all expected keys are present with correct default values
        assert data["total_runs"] == 0
        assert data["failures"] == 0
        assert data["passes"] == 0
        assert data["failure_rate"] == 0.0
        assert data["consecutive_failures"] == 0
        assert isinstance(data["recent_runs"], list)
        assert isinstance(data["comments"], list)
        assert isinstance(data["classifications"], dict)

    @pytest.mark.asyncio
    async def test_search_by_signature(self, test_client) -> None:
        """Test that /history/search returns expected structure."""
        response = test_client.get("/history/search?signature=abc123")
        assert response.status_code == 200
        data = response.json()
        assert data["signature"] == "abc123"
        assert isinstance(data.get("matches", []), list)

    @pytest.mark.asyncio
    async def test_search_by_signature_requires_param(self, test_client) -> None:
        """Test that /history/search requires signature parameter."""
        response = test_client.get("/history/search")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_job_stats(self, test_client) -> None:
        """Test that /history/stats/{job_name} returns expected structure and values."""
        response = test_client.get("/history/stats/my-job")
        assert response.status_code == 200
        data = response.json()
        assert data["job_name"] == "my-job"
        # Verify default values for a job with no history
        assert data["total_builds_analyzed"] == 0
        assert data["builds_with_failures"] == 0
        assert data["overall_failure_rate"] == 0.0
        assert isinstance(data["most_common_failures"], list)
        assert data["recent_trend"] in ("stable", "improving", "worsening")

    @pytest.mark.asyncio
    async def test_get_trends(self, test_client) -> None:
        """Test that /history/trends returns expected structure and values."""
        response = test_client.get("/history/trends")
        assert response.status_code == 200
        data = response.json()
        assert data["period"] == "daily"
        assert isinstance(data["data"], list)


class TestClassifyEndpoint:
    """Regression tests for POST /history/classify."""

    def test_classify_child_job_with_zero_build_number(self, test_client):
        """Regression: job_name + child_build_number=0 must not raise."""
        resp = test_client.post(
            "/history/classify",
            json={
                "test_name": "some_test",
                "classification": "FLAKY",
                "job_name": "parent-job",
                "child_build_number": 0,
                "job_id": "job-classify-zero",
            },
        )
        assert resp.status_code == 201

    def test_classify_storage_value_error_returns_400(self, test_client, monkeypatch):
        """ValueError from storage layer surfaces as 400."""

        async def _boom(*args, **kwargs):
            raise ValueError("bad value")

        monkeypatch.setattr(
            "jenkins_job_insight.main.storage.set_test_classification", _boom
        )
        resp = test_client.post(
            "/history/classify",
            json={
                "test_name": "t",
                "classification": "FLAKY",
                "job_id": "job-classify-err",
            },
        )
        assert resp.status_code == 400
        assert "bad value" in resp.json()["detail"]
