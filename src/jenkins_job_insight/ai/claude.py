"""Claude Vertex AI client implementation."""

import json
import os
import tempfile
from pathlib import Path

from anthropic import AnthropicVertex

from jenkins_job_insight.ai.base import AIClient


class ClaudeClient(AIClient):
    """Claude AI client using Vertex AI."""

    def __init__(
        self,
        project_id: str,
        region: str,
        credentials_json: str,
        model: str = "claude-sonnet-4-5",
    ) -> None:
        """Initialize Claude Vertex AI client.

        Args:
            project_id: Google Cloud project ID.
            region: Vertex AI region.
            credentials_json: Either a file path to credentials JSON or raw JSON content.
            model: Model name to use.
        """
        # Check if it's a file path or raw JSON
        if (
            credentials_json.startswith("/")
            or credentials_json.startswith("./")
            or Path(credentials_json).exists()
        ):
            # It's a file path - use it directly
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_json
        else:
            # It's raw JSON content - write to temp file
            creds = json.loads(credentials_json)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                json.dump(creds, f)
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name

        self.client = AnthropicVertex(project_id=project_id, region=region)
        self.model = model

    def analyze(self, prompt: str, system_prompt: str | None = None) -> str:
        """Analyze content using Claude.

        Args:
            prompt: The prompt to send to Claude.
            system_prompt: Optional system prompt to guide Claude's behavior.

        Returns:
            The model's response text.
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt if system_prompt else "",
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
