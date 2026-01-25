"""Tests for AI client factory."""

import json
from unittest.mock import MagicMock, patch

import pytest

from jenkins_job_insight.ai import get_ai_client
from jenkins_job_insight.ai.claude import ClaudeClient
from jenkins_job_insight.ai.gemini import GeminiClient
from jenkins_job_insight.config import Settings


class TestGetAiClient:
    """Tests for the get_ai_client factory function."""

    def test_get_ai_client_returns_gemini_when_api_key_set(self) -> None:
        """Test that Gemini client is returned when GEMINI_API_KEY is set."""
        settings = MagicMock(spec=Settings)
        settings.gemini_api_key = "test-gemini-key"  # pragma: allowlist secret
        settings.gemini_model = "gemini-2.5-pro"
        settings.google_project_id = None

        with patch("jenkins_job_insight.ai.gemini.genai"):
            client = get_ai_client(settings)
            assert isinstance(client, GeminiClient)

    def test_get_ai_client_returns_claude_when_google_configured(self) -> None:
        """Test that Claude client is returned when Google Cloud is configured."""
        settings = MagicMock(spec=Settings)
        settings.gemini_api_key = None
        settings.google_project_id = "test-project"
        settings.google_region = "us-central1"
        settings.google_credentials_json = json.dumps(
            {
                "type": "service_account",
                "project_id": "test-project",
                "private_key_id": "key-id",  # pragma: allowlist secret
                "private_key": "FAKE_TEST_PRIVATE_KEY_FOR_UNIT_TESTS",  # pragma: allowlist secret  # pragma: allowlist secret
                "client_email": "test@test.iam.gserviceaccount.com",
                "client_id": "123",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
        settings.claude_model = "claude-sonnet-4-5"

        with patch("jenkins_job_insight.ai.claude.AnthropicVertex"):
            client = get_ai_client(settings)
            assert isinstance(client, ClaudeClient)

    def test_get_ai_client_gemini_takes_precedence(self) -> None:
        """Test that Gemini is used when both Gemini and Claude are configured."""
        settings = MagicMock(spec=Settings)
        settings.gemini_api_key = "test-gemini-key"  # pragma: allowlist secret
        settings.gemini_model = "gemini-2.5-pro"
        settings.google_project_id = "test-project"
        settings.google_credentials_json = "{}"

        with patch("jenkins_job_insight.ai.gemini.genai"):
            client = get_ai_client(settings)
            assert isinstance(client, GeminiClient)

    def test_get_ai_client_raises_when_no_provider_configured(self) -> None:
        """Test that ValueError is raised when no AI provider is configured."""
        settings = MagicMock(spec=Settings)
        settings.gemini_api_key = None
        settings.google_project_id = None
        settings.google_credentials_json = None

        with pytest.raises(ValueError) as exc_info:
            get_ai_client(settings)

        assert "No AI provider configured" in str(exc_info.value)
        assert "GEMINI_API_KEY" in str(exc_info.value)
        assert "GOOGLE_PROJECT_ID" in str(exc_info.value)

    def test_get_ai_client_raises_when_only_project_id_set(self) -> None:
        """Test that ValueError is raised when only project ID is set."""
        settings = MagicMock(spec=Settings)
        settings.gemini_api_key = None
        settings.google_project_id = "test-project"
        settings.google_credentials_json = None

        with pytest.raises(ValueError):
            get_ai_client(settings)

    def test_get_ai_client_raises_when_only_credentials_set(self) -> None:
        """Test that ValueError is raised when only credentials are set."""
        settings = MagicMock(spec=Settings)
        settings.gemini_api_key = None
        settings.google_project_id = None
        settings.google_credentials_json = "{}"

        with pytest.raises(ValueError):
            get_ai_client(settings)


class TestGeminiClient:
    """Tests for the GeminiClient class."""

    def test_gemini_client_initialization(self) -> None:
        """Test GeminiClient initialization."""
        with patch("jenkins_job_insight.ai.gemini.genai") as mock_genai:
            GeminiClient(api_key="test-key")  # pragma: allowlist secret
            mock_genai.Client.assert_called_once_with(
                api_key="test-key"  # pragma: allowlist secret
            )

    def test_gemini_client_analyze(self) -> None:
        """Test GeminiClient analyze method."""
        with patch("jenkins_job_insight.ai.gemini.genai") as mock_genai:
            mock_response = MagicMock()
            mock_response.text = '{"summary": "test"}'
            mock_genai.Client.return_value.models.generate_content.return_value = (
                mock_response
            )

            client = GeminiClient(api_key="test-key")
            result = client.analyze("test prompt")

            assert result == '{"summary": "test"}'
            mock_genai.Client.return_value.models.generate_content.assert_called_once()

    def test_gemini_client_analyze_uses_default_model(self) -> None:
        """Test that GeminiClient uses the default model when not specified."""
        with patch("jenkins_job_insight.ai.gemini.genai") as mock_genai:
            mock_response = MagicMock()
            mock_response.text = "{}"
            mock_genai.Client.return_value.models.generate_content.return_value = (
                mock_response
            )

            client = GeminiClient(api_key="test-key")
            client.analyze("prompt")

            call_args = mock_genai.Client.return_value.models.generate_content.call_args
            assert call_args[1]["model"] == "gemini-2.5-pro"

    def test_gemini_client_analyze_uses_custom_model(self) -> None:
        """Test that GeminiClient uses a custom model when specified."""
        with patch("jenkins_job_insight.ai.gemini.genai") as mock_genai:
            mock_response = MagicMock()
            mock_response.text = "{}"
            mock_genai.Client.return_value.models.generate_content.return_value = (
                mock_response
            )

            client = GeminiClient(api_key="test-key", model="gemini-2.0-flash")
            client.analyze("prompt")

            call_args = mock_genai.Client.return_value.models.generate_content.call_args
            assert call_args[1]["model"] == "gemini-2.0-flash"

    def test_gemini_client_analyze_config(self) -> None:
        """Test that GeminiClient uses correct config."""
        with patch("jenkins_job_insight.ai.gemini.genai") as mock_genai:
            mock_response = MagicMock()
            mock_response.text = "{}"
            mock_genai.Client.return_value.models.generate_content.return_value = (
                mock_response
            )

            client = GeminiClient(api_key="test-key")
            client.analyze("prompt")

            call_args = mock_genai.Client.return_value.models.generate_content.call_args
            config = call_args[1]["config"]
            assert config["temperature"] == 0.7
            assert config["max_output_tokens"] == 4096
            assert config["response_mime_type"] == "application/json"

    def test_gemini_client_analyze_with_system_prompt(self) -> None:
        """Test that GeminiClient includes system_instruction when system_prompt is provided."""
        with patch("jenkins_job_insight.ai.gemini.genai") as mock_genai:
            mock_response = MagicMock()
            mock_response.text = "{}"
            mock_genai.Client.return_value.models.generate_content.return_value = (
                mock_response
            )

            client = GeminiClient(api_key="test-key")
            client.analyze("prompt", system_prompt="You are a helpful assistant")

            call_args = mock_genai.Client.return_value.models.generate_content.call_args
            config = call_args[1]["config"]
            assert config["system_instruction"] == "You are a helpful assistant"

    def test_gemini_client_analyze_without_system_prompt(self) -> None:
        """Test that GeminiClient does not include system_instruction when no system_prompt."""
        with patch("jenkins_job_insight.ai.gemini.genai") as mock_genai:
            mock_response = MagicMock()
            mock_response.text = "{}"
            mock_genai.Client.return_value.models.generate_content.return_value = (
                mock_response
            )

            client = GeminiClient(api_key="test-key")
            client.analyze("prompt")

            call_args = mock_genai.Client.return_value.models.generate_content.call_args
            config = call_args[1]["config"]
            assert "system_instruction" not in config


class TestClaudeClient:
    """Tests for the ClaudeClient class."""

    def test_claude_client_initialization(self) -> None:
        """Test ClaudeClient initialization."""
        credentials_json = json.dumps(
            {
                "type": "service_account",
                "project_id": "test",
                "private_key_id": "key",  # pragma: allowlist secret
                "private_key": "FAKE_TEST_PRIVATE_KEY_FOR_UNIT_TESTS",  # pragma: allowlist secret
                "client_email": "test@test.iam.gserviceaccount.com",
                "client_id": "123",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )

        with patch("jenkins_job_insight.ai.claude.AnthropicVertex") as mock_vertex:
            ClaudeClient(
                project_id="test-project",
                region="us-central1",
                credentials_json=credentials_json,
            )
            mock_vertex.assert_called_once()

    def test_claude_client_analyze(self) -> None:
        """Test ClaudeClient analyze method."""
        credentials_json = json.dumps(
            {
                "type": "service_account",
                "project_id": "test",
                "private_key_id": "key",  # pragma: allowlist secret
                "private_key": "FAKE_TEST_PRIVATE_KEY_FOR_UNIT_TESTS",  # pragma: allowlist secret
                "client_email": "test@test.iam.gserviceaccount.com",
                "client_id": "123",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )

        with patch("jenkins_job_insight.ai.claude.AnthropicVertex") as mock_vertex:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text='{"summary": "test"}')]
            mock_vertex.return_value.messages.create.return_value = mock_response

            client = ClaudeClient(
                project_id="test-project",
                region="us-central1",
                credentials_json=credentials_json,
            )
            result = client.analyze("test prompt")

            assert result == '{"summary": "test"}'

    def test_claude_client_uses_default_model(self) -> None:
        """Test that ClaudeClient uses the default model when not specified."""
        credentials_json = json.dumps(
            {
                "type": "service_account",
                "project_id": "test",
                "private_key_id": "key",  # pragma: allowlist secret
                "private_key": "FAKE_TEST_PRIVATE_KEY_FOR_UNIT_TESTS",  # pragma: allowlist secret
                "client_email": "test@test.iam.gserviceaccount.com",
                "client_id": "123",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )

        with patch("jenkins_job_insight.ai.claude.AnthropicVertex") as mock_vertex:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="{}")]
            mock_vertex.return_value.messages.create.return_value = mock_response

            client = ClaudeClient(
                project_id="test-project",
                region="us-central1",
                credentials_json=credentials_json,
            )
            client.analyze("prompt")

            call_args = mock_vertex.return_value.messages.create.call_args
            assert call_args[1]["model"] == "claude-sonnet-4-5"
            assert call_args[1]["max_tokens"] == 4096

    def test_claude_client_uses_custom_model(self) -> None:
        """Test that ClaudeClient uses a custom model when specified."""
        credentials_json = json.dumps(
            {
                "type": "service_account",
                "project_id": "test",
                "private_key_id": "key",  # pragma: allowlist secret
                "private_key": "FAKE_TEST_PRIVATE_KEY_FOR_UNIT_TESTS",  # pragma: allowlist secret
                "client_email": "test@test.iam.gserviceaccount.com",
                "client_id": "123",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )

        with patch("jenkins_job_insight.ai.claude.AnthropicVertex") as mock_vertex:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="{}")]
            mock_vertex.return_value.messages.create.return_value = mock_response

            client = ClaudeClient(
                project_id="test-project",
                region="us-central1",
                credentials_json=credentials_json,
                model="claude-opus-4",
            )
            client.analyze("prompt")

            call_args = mock_vertex.return_value.messages.create.call_args
            assert call_args[1]["model"] == "claude-opus-4"

    def test_claude_client_analyze_with_system_prompt(self) -> None:
        """Test ClaudeClient analyze with system prompt."""
        credentials_json = json.dumps(
            {
                "type": "service_account",
                "project_id": "test",
                "private_key_id": "key",  # pragma: allowlist secret
                "private_key": "FAKE_TEST_PRIVATE_KEY_FOR_UNIT_TESTS",  # pragma: allowlist secret
                "client_email": "test@test.iam.gserviceaccount.com",
                "client_id": "123",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )

        with patch("jenkins_job_insight.ai.claude.AnthropicVertex") as mock_vertex:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text='{"summary": "test"}')]
            mock_vertex.return_value.messages.create.return_value = mock_response

            client = ClaudeClient(
                project_id="test-project",
                region="us-central1",
                credentials_json=credentials_json,
            )
            client.analyze("test prompt", system_prompt="You are a helpful assistant")

            call_args = mock_vertex.return_value.messages.create.call_args
            assert call_args[1]["system"] == "You are a helpful assistant"

    def test_claude_client_analyze_without_system_prompt(self) -> None:
        """Test ClaudeClient analyze without system prompt uses empty string."""
        credentials_json = json.dumps(
            {
                "type": "service_account",
                "project_id": "test",
                "private_key_id": "key",  # pragma: allowlist secret
                "private_key": "FAKE_TEST_PRIVATE_KEY_FOR_UNIT_TESTS",  # pragma: allowlist secret
                "client_email": "test@test.iam.gserviceaccount.com",
                "client_id": "123",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )

        with patch("jenkins_job_insight.ai.claude.AnthropicVertex") as mock_vertex:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text='{"summary": "test"}')]
            mock_vertex.return_value.messages.create.return_value = mock_response

            client = ClaudeClient(
                project_id="test-project",
                region="us-central1",
                credentials_json=credentials_json,
            )
            client.analyze("test prompt")

            call_args = mock_vertex.return_value.messages.create.call_args
            assert call_args[1]["system"] == ""
