"""JJI CLI configuration file support.

Loads named server configurations from $XDG_CONFIG_HOME/jji/config.toml
(defaults to ~/.config/jji/config.toml when XDG_CONFIG_HOME is unset).
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

_XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG_DIR = _XDG_CONFIG_HOME / "jji"
CONFIG_FILE = CONFIG_DIR / "config.toml"


@dataclass
class ServerConfig:
    """A named server entry from the config file."""

    url: str
    username: str = ""
    no_verify_ssl: bool = False
    # Jenkins
    jenkins_url: str = ""
    jenkins_user: str = ""
    jenkins_password: str = ""
    jenkins_ssl_verify: bool = True
    # Tests
    tests_repo_url: str = ""
    # AI
    ai_provider: str = ""
    ai_model: str = ""
    ai_cli_timeout: int = 0  # 0 means use server default
    # Jira
    jira_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_pat: str = ""
    jira_project_key: str = ""
    jira_ssl_verify: bool = True
    jira_max_results: int = 0  # 0 means use server default
    enable_jira: bool = False
    # GitHub
    github_token: str = ""
    # Jenkins job monitoring
    wait_for_completion: bool | None = None
    poll_interval_minutes: int = 0  # 0 means use server default
    max_wait_minutes: int = 0  # 0 means use server default


def load_config(path: Path | None = None) -> dict:
    """Load config from TOML file.

    Args:
        path: Override config file path (used by tests). Defaults to
            ~/.config/jji/config.toml.

    Returns:
        Parsed TOML as a dict, or empty dict if file does not exist.
    """
    config_path = path or CONFIG_FILE
    if not config_path.exists():
        return {}
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def get_default_server_name(config: dict | None = None) -> str:
    """Return the default server name from config.

    Args:
        config: Pre-loaded config dict. If None, loads from disk.

    Returns:
        Default server name, or empty string if not set.
    """
    if config is None:
        config = load_config()
    return config.get("default", {}).get("server", "")


def _server_config_from_dict(data: dict) -> ServerConfig:
    """Build a ServerConfig from a TOML server dict.

    Args:
        data: The TOML dict for a single ``[servers.<name>]`` section.

    Returns:
        A fully populated ServerConfig.
    """
    return ServerConfig(
        url=data.get("url", ""),
        username=data.get("username", ""),
        no_verify_ssl=data.get("no_verify_ssl", False),
        # Jenkins
        jenkins_url=data.get("jenkins_url", ""),
        jenkins_user=data.get("jenkins_user", ""),
        jenkins_password=data.get("jenkins_password", ""),
        jenkins_ssl_verify=data.get("jenkins_ssl_verify", True),
        # Tests
        tests_repo_url=data.get("tests_repo_url", ""),
        # AI
        ai_provider=data.get("ai_provider", ""),
        ai_model=data.get("ai_model", ""),
        ai_cli_timeout=data.get("ai_cli_timeout", 0),
        # Jira
        jira_url=data.get("jira_url", ""),
        jira_email=data.get("jira_email", ""),
        jira_api_token=data.get("jira_api_token", ""),
        jira_pat=data.get("jira_pat", ""),
        jira_project_key=data.get("jira_project_key", ""),
        jira_ssl_verify=data.get("jira_ssl_verify", True),
        jira_max_results=data.get("jira_max_results", 0),
        enable_jira=data.get("enable_jira", False),
        # GitHub
        github_token=data.get("github_token", ""),
        # Jenkins job monitoring
        wait_for_completion=data.get("wait_for_completion"),
        poll_interval_minutes=data.get("poll_interval_minutes", 0),
        max_wait_minutes=data.get("max_wait_minutes", 0),
    )


def get_server_config(
    name: str | None = None,
    config: dict | None = None,
) -> ServerConfig | None:
    """Look up a named server, falling back to the default.

    Global ``[defaults]`` values are merged first, then server-specific
    values override them.

    Args:
        name: Server name to look up. If None, uses the default.
        config: Pre-loaded config dict. If None, loads from disk.

    Returns:
        ServerConfig if found, None otherwise.
    """
    if config is None:
        config = load_config()
    if not config:
        return None

    server_name = name or get_default_server_name(config)
    if not server_name:
        return None

    server_data = config.get("servers", {}).get(server_name)
    if not server_data:
        return None

    # Merge: global defaults first, server-specific values override.
    defaults = config.get("defaults", {})
    merged = {**defaults, **server_data}

    return _server_config_from_dict(merged)


def list_servers(config: dict | None = None) -> dict[str, ServerConfig]:
    """Return all configured servers.

    Global ``[defaults]`` values are merged into each server entry.

    Args:
        config: Pre-loaded config dict. If None, loads from disk.

    Returns:
        Mapping of server name to ServerConfig.
    """
    if config is None:
        config = load_config()
    defaults = config.get("defaults", {})
    result: dict[str, ServerConfig] = {}
    for name, data in config.get("servers", {}).items():
        merged = {**defaults, **data}
        result[name] = _server_config_from_dict(merged)
    return result
