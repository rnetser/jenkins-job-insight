"""Gemini AI client implementation."""

from google import genai

from jenkins_job_insight.ai.base import AIClient


class GeminiClient(AIClient):
    """AI client using Google Gemini API."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-pro") -> None:
        """Initialize Gemini client with API key.

        Args:
            api_key: The Gemini API key.
            model: The Gemini model to use.
        """
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def analyze(self, prompt: str, system_prompt: str | None = None) -> str:
        """Analyze a prompt using Gemini.

        Args:
            prompt: The prompt to send to Gemini.
            system_prompt: Optional system prompt to guide Gemini's behavior.

        Returns:
            The Gemini response as a string.
        """
        config = {
            "temperature": 0.7,
            "max_output_tokens": 4096,
            "response_mime_type": "application/json",
        }
        if system_prompt:
            config["system_instruction"] = system_prompt

        response = self.client.models.generate_content(
            model=self.model,
            contents=[{"parts": [{"text": prompt}]}],
            config=config,
        )
        return response.text
