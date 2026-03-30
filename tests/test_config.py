"""Tests for configuration settings."""

import os
from unittest.mock import patch

import pytest

from jenkins_job_insight.config import Settings, _resolve_jira_auth, get_settings


def _build_env(**overrides: str) -> dict[str, str]:
    """Return baseline Jenkins env with per-test overrides applied."""
    base = {
        "JENKINS_URL": "https://jenkins.example.com",
        "JENKINS_USER": "testuser",
        "JENKINS_PASSWORD": "testpassword",  # pragma: allowlist secret
    }
    base.update(overrides)
    return base


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
        with patch.dict(os.environ, _build_env(), clear=True):
            settings = Settings(_env_file=None)
            assert settings.tests_repo_url is None

    @pytest.mark.parametrize(
        "field",
        ["jenkins_url", "jenkins_user", "jenkins_password"],
    )
    def test_jenkins_fields_default_to_empty_string(self, field: str) -> None:
        """Test that Jenkins fields default to empty string when env vars are missing."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)
            assert getattr(settings, field) == ""

    def test_settings_extra_fields_ignored(self, mock_env_vars: dict[str, str]) -> None:
        """Test that extra environment variables are ignored."""
        with patch.dict(os.environ, {"UNKNOWN_FIELD": "value"}, clear=False):
            settings = Settings()
            assert not hasattr(settings, "unknown_field")

    def test_settings_loads_tests_repo_url(self) -> None:
        """Test that tests_repo_url is loaded from environment variable."""
        with patch.dict(
            os.environ,
            _build_env(TESTS_REPO_URL="https://github.com/org/test-repo"),
            clear=True,
        ):
            settings = Settings(_env_file=None)
            assert settings.tests_repo_url == "https://github.com/org/test-repo"


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
        with patch.dict(os.environ, _build_env(), clear=True):
            settings = Settings(_env_file=None)
            assert not settings.jira_enabled
            assert settings.jira_url is None

    def test_jira_enabled_with_cloud_auth(self) -> None:
        """Jira is enabled with URL + email + API token + project key."""
        with patch.dict(
            os.environ,
            _build_env(
                JIRA_URL="https://jira.example.com",
                JIRA_EMAIL="user@example.com",
                JIRA_API_TOKEN="token-123",  # noqa: S106  # pragma: allowlist secret
                JIRA_PROJECT_KEY="TEST",
            ),
            clear=True,
        ):
            settings = Settings(_env_file=None)
            assert settings.jira_enabled

    def test_jira_enabled_with_server_pat(self) -> None:
        """Jira is enabled with URL + PAT + project key."""
        with patch.dict(
            os.environ,
            _build_env(
                JIRA_URL="https://jira-server.example.com",
                JIRA_PAT="pat-token-456",
                JIRA_PROJECT_KEY="TEST",
            ),
            clear=True,
        ):
            settings = Settings(_env_file=None)
            assert settings.jira_enabled

    def test_jira_disabled_without_url(self) -> None:
        """Jira is disabled when URL is missing even with credentials."""
        with patch.dict(
            os.environ,
            _build_env(
                JIRA_EMAIL="user@example.com",
                JIRA_API_TOKEN="token-123",  # noqa: S106  # pragma: allowlist secret
                JIRA_PROJECT_KEY="TEST",
            ),
            clear=True,
        ):
            settings = Settings(_env_file=None)
            assert not settings.jira_enabled

    def test_jira_disabled_without_project_key(self) -> None:
        """Jira is disabled when credentials are present but project key is missing."""
        with patch.dict(
            os.environ,
            _build_env(
                JIRA_URL="https://jira.example.com",
                JIRA_PAT="pat-token-456",
            ),
            clear=True,
        ):
            settings = Settings(_env_file=None)
            assert not settings.jira_enabled

    def test_jira_disabled_without_credentials(self) -> None:
        """Jira is disabled when URL is set but no credentials."""
        with patch.dict(
            os.environ,
            _build_env(
                JIRA_URL="https://jira.example.com",
                JIRA_PROJECT_KEY="TEST",
            ),
            clear=True,
        ):
            settings = Settings(_env_file=None)
            assert not settings.jira_enabled

    def test_jira_default_values(self) -> None:
        """Test default values for optional Jira fields."""
        with patch.dict(os.environ, _build_env(), clear=True):
            settings = Settings(_env_file=None)
            assert settings.jira_ssl_verify is True
            assert settings.jira_max_results == 5
            assert settings.jira_project_key is None

    def test_enable_jira_explicit_false_disables(self) -> None:
        """Jira is disabled when ENABLE_JIRA is explicitly False."""
        with patch.dict(
            os.environ,
            _build_env(
                JIRA_URL="https://jira.example.com",
                JIRA_PAT="pat-token-456",
                JIRA_PROJECT_KEY="TEST",
                ENABLE_JIRA="false",
            ),
            clear=True,
        ):
            settings = Settings(_env_file=None)
            assert not settings.jira_enabled

    def test_jira_custom_values(self) -> None:
        """Test custom values for Jira fields."""
        with patch.dict(
            os.environ,
            _build_env(
                JIRA_URL="https://jira.example.com",
                JIRA_EMAIL="user@example.com",
                JIRA_API_TOKEN="token",  # noqa: S106  # pragma: allowlist secret
                JIRA_PROJECT_KEY="MYPROJ",
                JIRA_SSL_VERIFY="false",
                JIRA_MAX_RESULTS="10",
            ),
            clear=True,
        ):
            settings = Settings(_env_file=None)
            assert settings.jira_project_key == "MYPROJ"
            assert settings.jira_ssl_verify is False
            assert settings.jira_max_results == 10


class TestResolveJiraAuth:
    """Tests for _resolve_jira_auth helper."""

    def test_email_plus_pat_is_server_not_cloud(self) -> None:
        """JIRA_EMAIL + JIRA_PAT must resolve to Server/DC mode, not Cloud.

        Regression: a PAT sent via Cloud Basic-auth path would fail.
        """
        env = _build_env(
            JIRA_URL="https://jira-server.example.com",
            JIRA_EMAIL="user@example.com",
            JIRA_PAT="pat-token-456",
            JIRA_PROJECT_KEY="TEST",
        )
        with patch.dict(os.environ, env, clear=True):
            settings = Settings(_env_file=None)
            is_cloud, token = _resolve_jira_auth(settings)
            assert not is_cloud, "email + PAT must NOT activate Cloud mode"
            assert token == env["JIRA_PAT"]


class TestGitHubIssuesSettings:
    """Tests for GitHub issue creation configuration."""

    def test_github_issues_disabled_by_default(self) -> None:
        """GitHub issues disabled when no GitHub env vars are set."""
        with patch.dict(os.environ, _build_env(), clear=True):
            settings = Settings(_env_file=None)
            assert not settings.github_issues_enabled

    def test_github_issues_enabled_with_token_and_repo(self) -> None:
        """GitHub issues enabled when both GITHUB_TOKEN and TESTS_REPO_URL are set."""
        env = _build_env(
            GITHUB_TOKEN="ghp_test123",  # noqa: S106  # pragma: allowlist secret
            TESTS_REPO_URL="https://github.com/org/repo",
        )
        with patch.dict(os.environ, env, clear=True):
            settings = Settings(_env_file=None)
            assert settings.github_issues_enabled

    def test_github_issues_disabled_without_token(self) -> None:
        """GitHub issues disabled when GITHUB_TOKEN is missing."""
        env = _build_env(TESTS_REPO_URL="https://github.com/org/repo")
        with patch.dict(os.environ, env, clear=True):
            settings = Settings(_env_file=None)
            assert not settings.github_issues_enabled

    def test_github_issues_disabled_without_repo_url(self) -> None:
        """GitHub issues disabled when TESTS_REPO_URL is missing."""
        env = _build_env(
            GITHUB_TOKEN="ghp_test123",  # noqa: S106  # pragma: allowlist secret
        )
        with patch.dict(os.environ, env, clear=True):
            settings = Settings(_env_file=None)
            assert not settings.github_issues_enabled

    def test_github_issues_explicit_false_disables(self) -> None:
        """GitHub issues disabled when ENABLE_GITHUB_ISSUES is explicitly False."""
        env = _build_env(
            GITHUB_TOKEN="ghp_test123",  # noqa: S106  # pragma: allowlist secret
            TESTS_REPO_URL="https://github.com/org/repo",
            ENABLE_GITHUB_ISSUES="false",
        )
        with patch.dict(os.environ, env, clear=True):
            settings = Settings(_env_file=None)
            assert not settings.github_issues_enabled

    def test_github_issues_explicit_true_without_config(self) -> None:
        """GitHub issues disabled when ENABLE_GITHUB_ISSUES is True but config missing."""
        env = _build_env(ENABLE_GITHUB_ISSUES="true")
        with patch.dict(os.environ, env, clear=True):
            settings = Settings(_env_file=None)
            assert not settings.github_issues_enabled
