"""Tests for jji CLI config module and config-driven server resolution."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from jenkins_job_insight.cli.config import (
    ServerConfig,
    get_default_server_name,
    get_server_config,
    list_servers,
    load_config,
)
from jenkins_job_insight.cli.main import app

runner = CliRunner()

_SAMPLE_TOML = """\
[default]
server = "dev"

[servers.dev]
url = "http://localhost:8000"
username = "dev-user"
no_verify_ssl = true
jenkins_url = "https://jenkins.dev.local"
jenkins_user = "jenkins-dev"
jenkins_password = "dev-token"  # pragma: allowlist secret
jenkins_ssl_verify = false
tests_repo_url = "https://github.com/org/tests"
ai_provider = "claude"
ai_model = "opus-4"
ai_cli_timeout = 15
jira_url = "https://jira.dev.local"
jira_email = "dev@example.com"
jira_api_token = "jira-tok-dev"
jira_pat = "jira-pat-dev"
jira_project_key = "DEV"
jira_ssl_verify = false
jira_max_results = 30
enable_jira = true
github_token = "ghp_dev123"

[servers.prod]
url = "https://jji.example.com"
username = "prod-user"
no_verify_ssl = false

[servers.staging]
url = "https://staging-jji.example.com"
username = "admin"
"""

_DEFAULTS_TOML = """\
[default]
server = "dev"

[defaults]
jenkins_url = "https://jenkins.shared.local"
jenkins_user = "shared-user"
jenkins_password = "shared-token"  # pragma: allowlist secret
ai_provider = "claude"
ai_model = "opus-4"
tests_repo_url = "https://github.com/org/tests"

[servers.dev]
url = "http://localhost:8000"
username = "dev-user"
jenkins_ssl_verify = false

[servers.prod]
url = "https://jji.example.com"
username = "prod-user"
ai_provider = "cursor"
ai_model = "gpt-5"
"""


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    """Write sample TOML config and return its path."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(_SAMPLE_TOML)
    return cfg


@pytest.fixture()
def defaults_config_file(tmp_path: Path) -> Path:
    """Write TOML config with a [defaults] section and return its path."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(_DEFAULTS_TOML)
    return cfg


@pytest.fixture()
def empty_config(tmp_path: Path) -> Path:
    """Return a path to a non-existent config file."""
    return tmp_path / "missing.toml"


# -- load_config --------------------------------------------------------------


class TestLoadConfig:
    def test_loads_valid_toml(self, config_file: Path):
        config = load_config(config_file)
        assert config["default"]["server"] == "dev"
        assert "dev" in config["servers"]
        assert "prod" in config["servers"]

    def test_returns_empty_when_missing(self, empty_config: Path):
        assert load_config(empty_config) == {}


# -- get_default_server_name --------------------------------------------------


class TestGetDefaultServerName:
    def test_returns_default(self, config_file: Path):
        config = load_config(config_file)
        assert get_default_server_name(config) == "dev"

    def test_returns_empty_when_no_default(self):
        assert get_default_server_name({}) == ""

    def test_returns_empty_when_default_section_missing(self):
        assert get_default_server_name({"servers": {"x": {}}}) == ""


# -- get_server_config --------------------------------------------------------


class TestGetServerConfig:
    def test_lookup_by_name(self, config_file: Path):
        config = load_config(config_file)
        cfg = get_server_config("prod", config)
        assert cfg is not None
        assert cfg.url == "https://jji.example.com"
        assert cfg.username == "prod-user"
        assert cfg.no_verify_ssl is False

    def test_falls_back_to_default(self, config_file: Path):
        config = load_config(config_file)
        cfg = get_server_config(None, config)
        assert cfg is not None
        assert cfg.url == "http://localhost:8000"
        assert cfg.username == "dev-user"
        assert cfg.no_verify_ssl is True

    def test_returns_none_for_unknown_name(self, config_file: Path):
        config = load_config(config_file)
        assert get_server_config("nonexistent", config) is None

    def test_returns_none_when_config_empty(self):
        assert get_server_config("anything", {}) is None

    def test_returns_none_when_no_default_and_no_name(self):
        config = {"servers": {"a": {"url": "http://a"}}}
        assert get_server_config(None, config) is None

    def test_reads_all_analyze_fields(self, config_file: Path):
        """All analyze-related fields are populated from TOML."""
        config = load_config(config_file)
        cfg = get_server_config("dev", config)
        assert cfg is not None
        assert cfg.jenkins_url == "https://jenkins.dev.local"
        assert cfg.jenkins_user == "jenkins-dev"
        assert cfg.jenkins_password == "dev-token"  # pragma: allowlist secret
        assert cfg.jenkins_ssl_verify is False
        assert cfg.tests_repo_url == "https://github.com/org/tests"
        assert cfg.ai_provider == "claude"
        assert cfg.ai_model == "opus-4"
        assert cfg.ai_cli_timeout == 15
        assert cfg.jira_url == "https://jira.dev.local"
        assert cfg.jira_email == "dev@example.com"
        assert cfg.jira_api_token == "jira-tok-dev"
        assert cfg.jira_pat == "jira-pat-dev"
        assert cfg.jira_project_key == "DEV"
        assert cfg.jira_ssl_verify is False
        assert cfg.jira_max_results == 30
        assert cfg.enable_jira is True
        assert cfg.github_token == "ghp_dev123"

    def test_defaults_for_missing_analyze_fields(self, config_file: Path):
        """Servers without analyze fields get dataclass defaults."""
        config = load_config(config_file)
        cfg = get_server_config("prod", config)
        assert cfg is not None
        assert cfg.jenkins_url == ""
        assert cfg.jenkins_ssl_verify is True
        assert cfg.ai_provider == ""
        assert cfg.ai_cli_timeout == 0
        assert cfg.jira_ssl_verify is True
        assert cfg.jira_max_results == 0
        assert cfg.enable_jira is False
        assert cfg.github_token == ""


# -- list_servers --------------------------------------------------------------


class TestListServers:
    def test_lists_all(self, config_file: Path):
        config = load_config(config_file)
        servers = list_servers(config)
        assert set(servers.keys()) == {"dev", "prod", "staging"}
        assert isinstance(servers["dev"], ServerConfig)

    def test_empty_when_no_servers(self):
        assert list_servers({}) == {}

    def test_server_fields(self, config_file: Path):
        config = load_config(config_file)
        staging = list_servers(config)["staging"]
        assert staging.url == "https://staging-jji.example.com"
        assert staging.username == "admin"
        assert staging.no_verify_ssl is False

    def test_list_servers_includes_analyze_fields(self, config_file: Path):
        config = load_config(config_file)
        dev = list_servers(config)["dev"]
        assert dev.jenkins_url == "https://jenkins.dev.local"
        assert dev.ai_provider == "claude"
        assert dev.enable_jira is True


# -- CLI integration: --server with config name ----------------------------------


class TestServerNameResolution:
    """--server accepts a config server name and resolves it."""

    def test_server_name_resolves_from_config(self, config_file: Path):
        with (
            patch("jenkins_job_insight.cli.main.load_config") as mock_load,
            patch("jenkins_job_insight.cli.main.get_server_config") as mock_get,
            patch("jenkins_job_insight.cli.main.list_servers") as mock_list,
            patch("jenkins_job_insight.cli.main._get_client") as mock_client_fn,
            patch.dict(os.environ, {}, clear=True),
        ):
            config = load_config(config_file)
            mock_load.return_value = config
            mock_get.return_value = ServerConfig(
                url="https://jji.example.com",
                username="prod-user",
                no_verify_ssl=False,
            )
            mock_list.return_value = {}
            client = MagicMock()
            client.health.return_value = {"status": "healthy"}
            mock_client_fn.return_value = client

            result = runner.invoke(app, ["--server", "prod", "health"])
            assert result.exit_code == 0
            assert "healthy" in result.output

    def test_unknown_server_name_errors(self, config_file: Path):
        with (
            patch("jenkins_job_insight.cli.main.get_server_config", return_value=None),
            patch(
                "jenkins_job_insight.cli.main.list_servers",
                return_value={"dev": ServerConfig(url="http://x")},
            ),
            patch.dict(os.environ, {}, clear=True),
        ):
            result = runner.invoke(app, ["--server", "nonexistent", "health"])
            assert result.exit_code == 1
            assert "nonexistent" in result.output
            assert "dev" in result.output  # lists available servers


class TestDefaultServerFromConfig:
    """Default server from config is used when no --server given."""

    def test_default_server_used(self, config_file: Path):
        with (
            patch("jenkins_job_insight.cli.main.get_server_config") as mock_get,
            patch("jenkins_job_insight.cli.main._get_client") as mock_client_fn,
            patch.dict(os.environ, {}, clear=True),
        ):
            mock_get.return_value = ServerConfig(
                url="http://localhost:8000",
                username="dev-user",
                no_verify_ssl=True,
            )
            client = MagicMock()
            client.health.return_value = {"status": "healthy"}
            mock_client_fn.return_value = client

            result = runner.invoke(app, ["health"])
            assert result.exit_code == 0
            assert "healthy" in result.output

    def test_no_server_no_config_errors(self):
        with (
            patch("jenkins_job_insight.cli.main.get_server_config", return_value=None),
            patch.dict(os.environ, {}, clear=True),
        ):
            result = runner.invoke(app, ["health"])
            assert result.exit_code == 1
            assert "No server specified" in result.output
            assert "config.toml" in result.output


class TestCLIOverridesConfig:
    """CLI flags take precedence over config values."""

    def test_cli_user_overrides_config(self, config_file: Path):
        with (
            patch("jenkins_job_insight.cli.main.get_server_config") as mock_get,
            patch("jenkins_job_insight.cli.main._get_client") as mock_client_fn,
            patch("jenkins_job_insight.cli.main._state", {}) as mock_state,
            patch.dict(os.environ, {}, clear=True),
        ):
            mock_get.return_value = ServerConfig(
                url="http://localhost:8000",
                username="config-user",
                no_verify_ssl=False,
            )
            client = MagicMock()
            client.health.return_value = {"status": "healthy"}
            mock_client_fn.return_value = client

            result = runner.invoke(
                app,
                ["--server", "http://localhost:8000", "--user", "cli-user", "health"],
            )
            assert result.exit_code == 0
            assert mock_state["username"] == "cli-user"

    def test_cli_no_verify_ssl_overrides_config(self, config_file: Path):
        with (
            patch("jenkins_job_insight.cli.main.get_server_config") as mock_get,
            patch("jenkins_job_insight.cli.main._get_client") as mock_client_fn,
            patch("jenkins_job_insight.cli.main._state", {}) as mock_state,
            patch.dict(os.environ, {}, clear=True),
        ):
            mock_get.return_value = ServerConfig(
                url="http://localhost:8000",
                username="",
                no_verify_ssl=False,
            )
            client = MagicMock()
            client.health.return_value = {"status": "healthy"}
            mock_client_fn.return_value = client

            result = runner.invoke(
                app,
                ["--server", "http://localhost:8000", "--no-verify-ssl", "health"],
            )
            assert result.exit_code == 0
            assert mock_state["no_verify_ssl"] is True

    def test_url_uses_config_fallback_for_username(self):
        """When --server is a URL, config username is used as fallback."""
        with (
            patch("jenkins_job_insight.cli.main.get_server_config") as mock_get,
            patch("jenkins_job_insight.cli.main._get_client") as mock_client_fn,
            patch("jenkins_job_insight.cli.main._state", {}) as mock_state,
            patch.dict(os.environ, {}, clear=True),
        ):
            mock_get.return_value = ServerConfig(
                url="http://default:8000",
                username="default-user",
                no_verify_ssl=False,
            )
            client = MagicMock()
            client.health.return_value = {"status": "healthy"}
            mock_client_fn.return_value = client

            result = runner.invoke(app, ["--server", "http://custom:9000", "health"])
            assert result.exit_code == 0
            assert mock_state["server_url"] == "http://custom:9000"
            assert mock_state["username"] == "default-user"


# -- Config subcommand tests ---------------------------------------------------


class TestConfigShow:
    def test_show_no_config(self):
        with patch("jenkins_job_insight.cli.main.load_config", return_value={}):
            result = runner.invoke(app, ["config", "show"])
            assert result.exit_code == 0
            assert "No config file" in result.output

    def test_show_with_config(self, config_file: Path):
        config = load_config(config_file)
        with (
            patch("jenkins_job_insight.cli.main.load_config", return_value=config),
            patch(
                "jenkins_job_insight.cli.main.get_default_server_name",
                return_value="dev",
            ),
            patch(
                "jenkins_job_insight.cli.main.list_servers",
                return_value={
                    "dev": ServerConfig(
                        url="http://localhost:8000",
                        username="dev-user",
                        no_verify_ssl=True,
                    ),
                    "prod": ServerConfig(
                        url="https://jji.example.com",
                        username="prod-user",
                        no_verify_ssl=False,
                    ),
                },
            ),
        ):
            result = runner.invoke(app, ["config", "show"])
            assert result.exit_code == 0
            assert "dev" in result.output
            assert "prod" in result.output
            assert "Default server: dev" in result.output


class TestConfigServers:
    def test_servers_table(self, config_file: Path):
        config = load_config(config_file)
        with (
            patch("jenkins_job_insight.cli.main.load_config", return_value=config),
            patch(
                "jenkins_job_insight.cli.main.get_default_server_name",
                return_value="dev",
            ),
            patch(
                "jenkins_job_insight.cli.main.list_servers",
                return_value={
                    "dev": ServerConfig(
                        url="http://localhost:8000",
                        username="dev-user",
                        no_verify_ssl=True,
                    ),
                },
            ),
        ):
            result = runner.invoke(app, ["config", "servers"])
            assert result.exit_code == 0
            assert "dev" in result.output
            assert "localhost" in result.output

    def test_servers_empty(self):
        with (
            patch("jenkins_job_insight.cli.main.load_config", return_value={}),
            patch(
                "jenkins_job_insight.cli.main.get_default_server_name",
                return_value="",
            ),
            patch("jenkins_job_insight.cli.main.list_servers", return_value={}),
        ):
            result = runner.invoke(app, ["config", "servers"])
            assert result.exit_code == 0
            assert "No servers configured" in result.output

    def test_servers_json(self, config_file: Path):
        import json

        config = load_config(config_file)
        with (
            patch("jenkins_job_insight.cli.main.load_config", return_value=config),
            patch(
                "jenkins_job_insight.cli.main.get_default_server_name",
                return_value="dev",
            ),
            patch(
                "jenkins_job_insight.cli.main.list_servers",
                return_value={
                    "dev": ServerConfig(
                        url="http://localhost:8000",
                        username="dev-user",
                        no_verify_ssl=True,
                    ),
                },
            ),
        ):
            result = runner.invoke(app, ["config", "servers", "--json"])
            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert "dev" in parsed
            assert parsed["dev"]["url"] == "http://localhost:8000"
            assert parsed["dev"]["default"] is True


class TestConfigSubcommandNoServer:
    """Config subcommands must work without any server configured."""

    def test_config_show_without_server(self):
        with (
            patch("jenkins_job_insight.cli.main.load_config", return_value={}),
            patch.dict(os.environ, {}, clear=True),
        ):
            result = runner.invoke(app, ["config", "show"])
            assert result.exit_code == 0

    def test_config_servers_without_server(self):
        with (
            patch("jenkins_job_insight.cli.main.load_config", return_value={}),
            patch(
                "jenkins_job_insight.cli.main.get_default_server_name",
                return_value="",
            ),
            patch("jenkins_job_insight.cli.main.list_servers", return_value={}),
            patch.dict(os.environ, {}, clear=True),
        ):
            result = runner.invoke(app, ["config", "servers"])
            assert result.exit_code == 0


# -- XDG_CONFIG_HOME ----------------------------------------------------------


class TestXDGConfigHome:
    """CONFIG_DIR and CONFIG_FILE respect XDG_CONFIG_HOME."""

    def test_default_config_dir_uses_home_dot_config(self):
        """When XDG_CONFIG_HOME is unset, falls back to ~/.config."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_CONFIG_HOME", None)
            # Re-import to pick up the env change.
            import importlib

            import jenkins_job_insight.cli.config as cfg_mod

            importlib.reload(cfg_mod)
            assert cfg_mod.CONFIG_DIR == Path.home() / ".config" / "jji"
            assert (
                cfg_mod.CONFIG_FILE == Path.home() / ".config" / "jji" / "config.toml"
            )

    def test_xdg_config_home_override(self, tmp_path: Path):
        """When XDG_CONFIG_HOME is set, CONFIG_DIR uses it."""
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}):
            import importlib

            import jenkins_job_insight.cli.config as cfg_mod

            importlib.reload(cfg_mod)
            assert cfg_mod.CONFIG_DIR == tmp_path / "jji"
            assert cfg_mod.CONFIG_FILE == tmp_path / "jji" / "config.toml"


# -- Global defaults merging --------------------------------------------------


class TestGlobalDefaults:
    """[defaults] section values are inherited by all servers."""

    def test_server_inherits_defaults(self, defaults_config_file: Path):
        """dev server inherits jenkins_url etc. from [defaults]."""
        config = load_config(defaults_config_file)
        cfg = get_server_config("dev", config)
        assert cfg is not None
        # Inherited from [defaults]
        assert cfg.jenkins_url == "https://jenkins.shared.local"
        assert cfg.jenkins_user == "shared-user"
        assert cfg.jenkins_password == "shared-token"  # pragma: allowlist secret
        assert cfg.ai_provider == "claude"
        assert cfg.ai_model == "opus-4"
        assert cfg.tests_repo_url == "https://github.com/org/tests"
        # From [servers.dev]
        assert cfg.url == "http://localhost:8000"
        assert cfg.username == "dev-user"
        assert cfg.jenkins_ssl_verify is False

    def test_server_overrides_defaults(self, defaults_config_file: Path):
        """prod server overrides ai_provider and ai_model from [defaults]."""
        config = load_config(defaults_config_file)
        cfg = get_server_config("prod", config)
        assert cfg is not None
        # Overridden by [servers.prod]
        assert cfg.ai_provider == "cursor"
        assert cfg.ai_model == "gpt-5"
        # Still inherited from [defaults]
        assert cfg.jenkins_url == "https://jenkins.shared.local"
        assert cfg.jenkins_user == "shared-user"
        assert cfg.tests_repo_url == "https://github.com/org/tests"
        # From [servers.prod]
        assert cfg.url == "https://jji.example.com"
        assert cfg.username == "prod-user"

    def test_list_servers_merges_defaults(self, defaults_config_file: Path):
        """list_servers also applies [defaults] to every server."""
        config = load_config(defaults_config_file)
        servers = list_servers(config)
        assert servers["dev"].jenkins_url == "https://jenkins.shared.local"
        assert servers["dev"].ai_provider == "claude"
        assert servers["prod"].jenkins_url == "https://jenkins.shared.local"
        assert servers["prod"].ai_provider == "cursor"

    def test_no_defaults_section_still_works(self, config_file: Path):
        """Config without [defaults] works exactly as before."""
        config = load_config(config_file)
        cfg = get_server_config("dev", config)
        assert cfg is not None
        assert cfg.jenkins_url == "https://jenkins.dev.local"
        assert cfg.ai_provider == "claude"

    def test_empty_defaults_section(self, tmp_path: Path):
        """An empty [defaults] section has no effect."""
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            '[default]\nserver = "a"\n\n[defaults]\n\n[servers.a]\nurl = "http://a"\n'
        )
        config = load_config(cfg_path)
        cfg = get_server_config("a", config)
        assert cfg is not None
        assert cfg.url == "http://a"
        assert cfg.jenkins_url == ""
        assert cfg.ai_provider == ""
