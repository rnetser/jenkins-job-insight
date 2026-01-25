"""Configuration settings from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Gemini API configuration
    gemini_api_key: str | None = None

    # Google Cloud configuration
    google_project_id: str | None = None
    google_region: str = "us-east5"
    google_credentials_json: str | None = None

    # AI model selection
    gemini_model: str = "gemini-2.5-pro"
    claude_model: str = "claude-sonnet-4-5"

    # Jenkins configuration
    jenkins_url: str
    jenkins_user: str
    jenkins_password: str
    jenkins_ssl_verify: bool = True

    # Slack notification configuration
    slack_webhook_url: str | None = None

    # Custom prompt file path
    prompt_file: str = "/app/PROMPT.md"

    # Optional defaults (can be overridden per-request in webhook)
    tests_repo_url: str | None = None
    callback_url: str | None = None
    callback_headers: dict[str, str] | None = None


@lru_cache
def get_settings() -> Settings:
    """Get application settings instance."""
    return Settings()
