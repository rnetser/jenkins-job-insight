"""Base protocol for AI clients."""

from typing import Protocol


class AIClient(Protocol):
    """Protocol defining the interface for AI clients."""

    def analyze(self, prompt: str, system_prompt: str | None = None) -> str:
        """Analyze a prompt and return the AI response.

        Args:
            prompt: The prompt to send to the AI model.
            system_prompt: Optional system prompt to guide the AI's behavior.

        Returns:
            The AI model's response as a string.
        """
        ...
