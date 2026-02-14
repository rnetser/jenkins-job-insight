"""Tests for configuration settings."""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from jenkins_job_insight.config import Settings, get_settings


class TestSettings:
    """Tests for the Settings class."""

    def test_settings_loads_from_env_vars(self, mock_env_vars: dict[str, str]) -> None:
        """Test that Settings loads required fields from environment variables."""
        settings = Settings()
        assert settings.jenkins_url == mock_env_vars["JENKINS_URL"]
        assert settings.jenkins_user == mock_env_vars["JENKINS_USER"]
        assert settings.jenkins_password == mock_env_vars["JENKINS_PASSWORD"]

    def test_settings_optional_fields_default_to_none(self) -> None:
        """Test that optional fields default to None when not set."""
        # Use clear=True to ensure no existing env vars pollute the test
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert settings.tests_repo_url is None
            assert settings.callback_url is None
            assert settings.callback_headers is None

    def test_settings_default_prompt_file(self, mock_env_vars: dict[str, str]) -> None:
        """Test that prompt_file has a default value."""
        settings = Settings()
        assert settings.prompt_file == "/app/PROMPT.md"

    def test_settings_custom_prompt_file(self) -> None:
        """Test that prompt_file can be overridden."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
            "PROMPT_FILE": "/custom/path/PROMPT.md",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert settings.prompt_file == "/custom/path/PROMPT.md"

    def test_settings_validation_error_missing_jenkins_url(self) -> None:
        """Test that ValidationError is raised when JENKINS_URL is missing."""
        env = {
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                Settings()
            errors = exc_info.value.errors()
            assert any(e["loc"] == ("jenkins_url",) for e in errors)

    def test_settings_validation_error_missing_jenkins_user(self) -> None:
        """Test that ValidationError is raised when JENKINS_USER is missing."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                Settings()
            errors = exc_info.value.errors()
            assert any(e["loc"] == ("jenkins_user",) for e in errors)

    def test_settings_validation_error_missing_jenkins_password(self) -> None:
        """Test that ValidationError is raised when JENKINS_PASSWORD is missing."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                Settings()
            errors = exc_info.value.errors()
            assert any(e["loc"] == ("jenkins_password",) for e in errors)

    def test_settings_extra_fields_ignored(self, mock_env_vars: dict[str, str]) -> None:
        """Test that extra environment variables are ignored."""
        with patch.dict(os.environ, {"UNKNOWN_FIELD": "value"}, clear=False):
            settings = Settings()
            assert not hasattr(settings, "unknown_field")

    def test_settings_loads_tests_repo_url(self) -> None:
        """Test that tests_repo_url is loaded from environment variable."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
            "TESTS_REPO_URL": "https://github.com/org/test-repo",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert settings.tests_repo_url == "https://github.com/org/test-repo"

    def test_settings_loads_callback_url(self) -> None:
        """Test that callback_url is loaded from environment variable."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
            "CALLBACK_URL": "https://my-service.example.com/webhook",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert settings.callback_url == "https://my-service.example.com/webhook"

    def test_settings_loads_callback_headers(self) -> None:
        """Test that callback_headers is loaded from environment variable as JSON."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
            "CALLBACK_HEADERS": '{"Authorization": "Bearer token123"}',
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert settings.callback_headers == {"Authorization": "Bearer token123"}


class TestGetSettings:
    """Tests for the get_settings function."""

    def test_get_settings_returns_settings_instance(
        self, mock_env_vars: dict[str, str]
    ) -> None:
        """Test that get_settings returns a Settings instance."""
        # Clear the lru_cache before testing
        get_settings.cache_clear()
        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_get_settings_cached(self, mock_env_vars: dict[str, str]) -> None:
        """Test that get_settings returns cached instance."""
        get_settings.cache_clear()
        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2


class TestJiraSettings:
    """Tests for Jira configuration fields."""

    def test_jira_disabled_by_default(self) -> None:
        """Jira is disabled when no Jira env vars are set."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert not settings.jira_enabled
            assert settings.jira_url is None

    def test_jira_enabled_with_cloud_auth(self) -> None:
        """Jira is enabled with URL + email + API token."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
            "JIRA_URL": "https://jira.example.com",
            "JIRA_EMAIL": "user@example.com",
            "JIRA_API_TOKEN": "token-123",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert settings.jira_enabled

    def test_jira_enabled_with_server_pat(self) -> None:
        """Jira is enabled with URL + PAT."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
            "JIRA_URL": "https://jira-server.example.com",
            "JIRA_PAT": "pat-token-456",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert settings.jira_enabled

    def test_jira_disabled_without_url(self) -> None:
        """Jira is disabled when URL is missing even with credentials."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
            "JIRA_EMAIL": "user@example.com",
            "JIRA_API_TOKEN": "token-123",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert not settings.jira_enabled

    def test_jira_disabled_without_credentials(self) -> None:
        """Jira is disabled when URL is set but no credentials."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
            "JIRA_URL": "https://jira.example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert not settings.jira_enabled

    def test_jira_default_values(self) -> None:
        """Test default values for optional Jira fields."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert settings.jira_ssl_verify is True
            assert settings.jira_max_results == 5
            assert settings.jira_project_key is None

    def test_jira_custom_values(self) -> None:
        """Test custom values for Jira fields."""
        env = {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "testuser",
            "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
            "JIRA_URL": "https://jira.example.com",
            "JIRA_EMAIL": "user@example.com",
            "JIRA_API_TOKEN": "token",
            "JIRA_PROJECT_KEY": "MYPROJ",
            "JIRA_SSL_VERIFY": "false",
            "JIRA_MAX_RESULTS": "10",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings()
            assert settings.jira_project_key == "MYPROJ"
            assert settings.jira_ssl_verify is False
            assert settings.jira_max_results == 10
