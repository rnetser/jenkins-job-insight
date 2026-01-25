"""AI client factory and implementations."""

from jenkins_job_insight.ai.base import AIClient
from jenkins_job_insight.ai.claude import ClaudeClient
from jenkins_job_insight.ai.gemini import GeminiClient
from jenkins_job_insight.config import Settings


def get_ai_client(config: Settings) -> AIClient:
    """Return first configured AI client (Gemini first, then Claude).

    Args:
        config: The application settings.

    Returns:
        An AI client instance.

    Raises:
        ValueError: If no AI provider is configured.
    """
    if config.gemini_api_key:
        return GeminiClient(api_key=config.gemini_api_key, model=config.gemini_model)
    if config.google_project_id and config.google_credentials_json:
        return ClaudeClient(
            project_id=config.google_project_id,
            region=config.google_region,
            credentials_json=config.google_credentials_json,
            model=config.claude_model,
        )
    raise ValueError(
        "No AI provider configured. Set GEMINI_API_KEY or "
        "GOOGLE_PROJECT_ID + GOOGLE_CREDENTIALS_JSON"
    )


__all__ = ["AIClient", "ClaudeClient", "GeminiClient", "get_ai_client"]
