"""Tests for the standalone JUnit XML AI enrichment CLI."""

import importlib.util
import io
import os
import sys
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

# Import the enricher script via importlib (avoids sys.path mutation)
_ENRICHER_PATH = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "junit_xml_enricher"
    / "enrich_junit_xml.py"
)
_enricher_spec = importlib.util.spec_from_file_location(
    "enrich_junit_xml", _ENRICHER_PATH
)
if _enricher_spec is None or _enricher_spec.loader is None:
    raise ImportError(f"Cannot create module spec from {_ENRICHER_PATH}")
_enricher = importlib.util.module_from_spec(_enricher_spec)
sys.modules["enrich_junit_xml"] = _enricher
try:
    _enricher_spec.loader.exec_module(_enricher)
except Exception as exc:
    pytest.exit(f"Cannot import enrich_junit_xml: {exc}", returncode=4)

EXIT_INVALID_INPUT = _enricher.EXIT_INVALID_INPUT
EXIT_NO_FAILURES = _enricher.EXIT_NO_FAILURES
EXIT_SERVER_ERROR = _enricher.EXIT_SERVER_ERROR
EXIT_SUCCESS = _enricher.EXIT_SUCCESS
main = _enricher.main

# Import the shared utility for direct extraction tests
_UTILS_PATH = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "pytest_junitxml"
    / "conftest_junit_ai_utils.py"
)
_utils_spec = importlib.util.spec_from_file_location(
    "conftest_junit_ai_utils", _UTILS_PATH
)
if _utils_spec is None or _utils_spec.loader is None:
    raise ImportError(f"Cannot create module spec from {_UTILS_PATH}")
_utils = importlib.util.module_from_spec(_utils_spec)
try:
    _utils_spec.loader.exec_module(_utils)
except Exception as exc:
    pytest.exit(f"Cannot import conftest_junit_ai_utils: {exc}", returncode=4)

_extract_failures_from_xml = _utils._extract_failures_from_xml


@pytest.fixture(autouse=True)
def _suppress_dotenv():
    """Prevent load_dotenv from loading real .env files during tests."""
    with patch("enrich_junit_xml.load_dotenv"):
        yield


@pytest.fixture
def junit_xml_with_failures(tmp_path: Path) -> Path:
    """Create a JUnit XML file with test failures."""
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite" tests="3" failures="2" errors="0">
    <testcase classname="com.example.Tests" name="test_pass" time="0.1"/>
    <testcase classname="com.example.Tests" name="test_fail_with_message" time="0.5">
        <failure message="Expected true but got false" type="AssertionError">
            at com.example.Tests.test_fail_with_message(Tests.java:42)
        </failure>
    </testcase>
    <testcase classname="com.example.Tests" name="test_fail_no_message" time="1.2">
        <failure type="Failure">tests/storage/datavolume.go:229
Timed out after 500.055s.
Expected Running but got Scheduling</failure>
    </testcase>
</testsuite>"""
    xml_path = tmp_path / "report.xml"
    xml_path.write_text(xml_content)
    return xml_path


@pytest.fixture
def junit_xml_no_failures(tmp_path: Path) -> Path:
    """Create a JUnit XML file with no failures."""
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite" tests="2" failures="0" errors="0">
    <testcase classname="com.example.Tests" name="test_a" time="0.1"/>
    <testcase classname="com.example.Tests" name="test_b" time="0.2"/>
</testsuite>"""
    xml_path = tmp_path / "report.xml"
    xml_path.write_text(xml_content)
    return xml_path


@pytest.fixture
def invalid_xml(tmp_path: Path) -> Path:
    """Create an invalid XML file."""
    xml_path = tmp_path / "bad.xml"
    xml_path.write_text("this is not xml <<<<")
    return xml_path


class TestCLIInputValidation:
    """Tests for CLI input validation and exit codes."""

    def test_file_not_found(self) -> None:
        with patch("sys.argv", ["prog", "/nonexistent/file.xml", "--dry-run"]):
            assert main() == EXIT_INVALID_INPUT

    def test_invalid_xml_parse_error(self, invalid_xml: Path) -> None:
        with patch("sys.argv", ["prog", str(invalid_xml), "--dry-run"]):
            assert main() == EXIT_INVALID_INPUT

    def test_no_failures_returns_exit_1(self, junit_xml_no_failures: Path) -> None:
        with patch("sys.argv", ["prog", str(junit_xml_no_failures), "--dry-run"]):
            assert main() == EXIT_NO_FAILURES

    def test_missing_server_url_returns_exit_3(
        self, junit_xml_with_failures: Path
    ) -> None:
        env_without_server = {
            k: v for k, v in os.environ.items() if k != "JJI_SERVER_URL"
        }
        with patch("sys.argv", ["prog", str(junit_xml_with_failures)]):
            with patch.dict(os.environ, env_without_server, clear=True):
                assert main() == EXIT_INVALID_INPUT


class TestDryRun:
    """Tests for dry-run mode."""

    def test_dry_run_shows_failures_and_exits_success(
        self, junit_xml_with_failures: Path
    ) -> None:
        # simple_logger writes to stderr via its own StreamHandler;
        # capture by temporarily redirecting the handler's stream.
        enricher_logger = _enricher.logger
        buf = io.StringIO()
        original_streams = []
        for handler in enricher_logger.handlers:
            if hasattr(handler, "stream"):
                original_streams.append((handler, handler.stream))
                handler.stream = buf

        try:
            with patch("sys.argv", ["prog", str(junit_xml_with_failures), "--dry-run"]):
                result = main()
        finally:
            for handler, stream in original_streams:
                handler.stream = stream

        assert result == EXIT_SUCCESS
        log_output = buf.getvalue()
        assert "2 failure(s)" in log_output
        assert "test_fail_with_message" in log_output
        assert "test_fail_no_message" in log_output

    def test_dry_run_does_not_modify_xml(self, junit_xml_with_failures: Path) -> None:
        original = junit_xml_with_failures.read_text()
        with patch("sys.argv", ["prog", str(junit_xml_with_failures), "--dry-run"]):
            main()
        assert junit_xml_with_failures.read_text() == original


class TestErrorMessageExtraction:
    """Tests for error_message extraction from different XML formats."""

    def test_message_attribute_extracted(self, junit_xml_with_failures: Path) -> None:
        """Test that message attribute is used when present (pytest/Java style)."""
        failures = _extract_failures_from_xml(junit_xml_with_failures)
        msg_failure = next(
            f for f in failures if "test_fail_with_message" in f["test_name"]
        )
        assert msg_failure["error_message"] == "Expected true but got false"

    def test_first_line_fallback_when_no_message(
        self, junit_xml_with_failures: Path
    ) -> None:
        """Test that first line of element text is used when message attribute is missing."""
        failures = _extract_failures_from_xml(junit_xml_with_failures)
        no_msg_failure = next(
            f for f in failures if "test_fail_no_message" in f["test_name"]
        )
        assert no_msg_failure["error_message"] == "tests/storage/datavolume.go:229"
        # Stack trace should have the full text
        assert "Timed out after 500.055s" in no_msg_failure["stack_trace"]


class TestServerInteraction:
    """Tests for server communication and XML enrichment."""

    def test_successful_enrichment(self, junit_xml_with_failures: Path) -> None:
        """Test full enrichment flow with mocked server."""
        mock_analysis_map = {
            ("com.example.Tests", "test_fail_with_message"): {
                "classification": "CODE ISSUE",
                "details": "Assertion failed",
                "affected_tests": ["test_fail_with_message"],
            },
            ("com.example.Tests", "test_fail_no_message"): {
                "classification": "PRODUCT BUG",
                "details": "Timeout during VM scheduling",
                "affected_tests": ["test_fail_no_message"],
            },
        }

        with patch(
            "sys.argv",
            [
                "prog",
                str(junit_xml_with_failures),
                "--server-url",
                "http://test-server:8000",
                "--ai-provider",
                "claude",
                "--ai-model",
                "test-model",
            ],
        ):
            with patch(
                "enrich_junit_xml._fetch_analysis_from_server",
                return_value=(
                    mock_analysis_map,
                    "http://test-server:8000/results/job-123.html",
                ),
            ):
                result = main()

        assert result == EXIT_SUCCESS

        # Verify XML was enriched
        tree = ET.parse(junit_xml_with_failures)
        for testcase in tree.iter("testcase"):
            name = testcase.get("name", "")
            if name == "test_fail_with_message":
                props = testcase.find("properties")
                assert props is not None
                prop_names = [p.get("name") for p in props]
                assert "ai_classification" in prop_names
            elif name == "test_fail_no_message":
                props = testcase.find("properties")
                assert props is not None
                prop_names = [p.get("name") for p in props]
                assert "ai_classification" in prop_names

        # Verify testsuite html_report_url property
        for testsuite in tree.iter("testsuite"):
            ts_props = testsuite.find("properties")
            assert ts_props is not None
            html_url_props = [p for p in ts_props if p.get("name") == "html_report_url"]
            assert len(html_url_props) == 1
            assert (
                html_url_props[0].get("value")
                == "http://test-server:8000/results/job-123.html"
            )

    def test_server_error_returns_exit_2(self, junit_xml_with_failures: Path) -> None:
        """Test that server failure returns EXIT_SERVER_ERROR."""
        with patch(
            "sys.argv",
            [
                "prog",
                str(junit_xml_with_failures),
                "--server-url",
                "http://test-server:8000",
                "--ai-provider",
                "claude",
                "--ai-model",
                "test-model",
            ],
        ):
            with patch(
                "enrich_junit_xml._fetch_analysis_from_server",
                return_value=({}, ""),
            ):
                result = main()

        assert result == EXIT_SERVER_ERROR

    def test_xml_not_modified_on_server_error(
        self, junit_xml_with_failures: Path
    ) -> None:
        """Test that XML is not modified when server returns no results."""
        original = junit_xml_with_failures.read_text()
        with patch(
            "sys.argv",
            [
                "prog",
                str(junit_xml_with_failures),
                "--server-url",
                "http://test-server:8000",
                "--ai-provider",
                "claude",
                "--ai-model",
                "test-model",
            ],
        ):
            with patch(
                "enrich_junit_xml._fetch_analysis_from_server",
                return_value=({}, ""),
            ):
                main()

        assert junit_xml_with_failures.read_text() == original


class TestConfigResolution:
    """Tests for CLI args vs env var resolution."""

    def test_cli_args_override_env_vars(self, junit_xml_with_failures: Path) -> None:
        """Test that CLI arguments take precedence over environment variables."""
        with patch.dict(
            os.environ,
            {
                "JJI_SERVER_URL": "http://env-server:8000",
                "JJI_AI_PROVIDER": "gemini",
                "JJI_AI_MODEL": "gemini-model",
            },
        ):
            with patch(
                "sys.argv",
                [
                    "prog",
                    str(junit_xml_with_failures),
                    "--server-url",
                    "http://cli-server:9000",
                    "--ai-provider",
                    "claude",
                    "--ai-model",
                    "claude-model",
                ],
            ):
                with patch(
                    "enrich_junit_xml._fetch_analysis_from_server",
                    return_value=({}, ""),
                ) as mock_fetch:
                    assert main() == EXIT_SERVER_ERROR

                # Verify the CLI values were used in the payload
                assert mock_fetch.call_args.kwargs["payload"]["ai_provider"] == "claude"
                assert (
                    mock_fetch.call_args.kwargs["payload"]["ai_model"] == "claude-model"
                )
                assert (
                    mock_fetch.call_args.kwargs["server_url"]
                    == "http://cli-server:9000"
                )

    def test_env_vars_used_when_no_cli_args(
        self, junit_xml_with_failures: Path
    ) -> None:
        """Test that env vars are used when CLI args are not provided."""
        with patch.dict(
            os.environ,
            {
                "JJI_SERVER_URL": "http://env-server:8000",
                "JJI_AI_PROVIDER": "gemini",
                "JJI_AI_MODEL": "gemini-model",
            },
        ):
            with patch("sys.argv", ["prog", str(junit_xml_with_failures)]):
                with patch(
                    "enrich_junit_xml._fetch_analysis_from_server",
                    return_value=({}, ""),
                ) as mock_fetch:
                    assert main() == EXIT_SERVER_ERROR

                assert mock_fetch.call_args.kwargs["payload"]["ai_provider"] == "gemini"
                assert (
                    mock_fetch.call_args.kwargs["payload"]["ai_model"] == "gemini-model"
                )
                assert (
                    mock_fetch.call_args.kwargs["server_url"]
                    == "http://env-server:8000"
                )
