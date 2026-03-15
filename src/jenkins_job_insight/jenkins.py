"""Jenkins API client wrapper."""

import io
import os
from urllib.parse import urlparse

import jenkins
import urllib3
from pydantic import HttpUrl
from simple_logger.logger import get_logger

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))


class JenkinsClient(jenkins.Jenkins):
    """Extended Jenkins client with helper methods."""

    def __init__(
        self, url: str, username: str, password: str, ssl_verify: bool = True
    ) -> None:
        """Initialize Jenkins client.

        Args:
            url: Jenkins server URL.
            username: Jenkins username.
            password: Jenkins password or API token.
            ssl_verify: Whether to verify SSL certificates. Set to False for self-signed certs.
        """
        super().__init__(url=url, username=username, password=password)
        logger.info(f"Connecting to Jenkins: {url}")
        if not ssl_verify:
            self._session.verify = False
            # Suppress InsecureRequestWarning
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def get_build_console(self, job_name: str, build_number: int) -> str:
        """Get console output for a build.

        Args:
            job_name: Name of the Jenkins job.
            build_number: Build number to retrieve.

        Returns:
            Console output as a string.
        """
        logger.debug(f"Fetching console output: {job_name} #{build_number}")
        return self.get_build_console_output(job_name, build_number)

    def get_build_info_safe(self, job_name: str, build_number: int) -> dict:
        """Get build information safely.

        Args:
            job_name: Name of the Jenkins job.
            build_number: Build number to retrieve.

        Returns:
            Build information dictionary.
        """
        logger.debug(f"Fetching build info: {job_name} #{build_number}")
        return super().get_build_info(job_name, build_number)

    def get_test_report(self, job_name: str, build_number: int) -> dict | None:
        """Get test report for a build.

        Uses the Jenkins /testReport/api/json endpoint which provides structured
        test results with all failures in a parseable format.

        Args:
            job_name: Name of the Jenkins job.
            build_number: Build number to retrieve.

        Returns:
            Test report dictionary if available, None if no test report exists.

        Raises:
            jenkins.JenkinsException: If there's an error other than 404 (not found).
        """
        logger.debug(f"Fetching test report: {job_name} #{build_number}")
        try:
            return self.get_build_test_report(job_name, build_number)
        except jenkins.NotFoundException:
            # No test report available (404)
            return None
        except jenkins.JenkinsException as err:
            logger.warning(
                "Failed to fetch test report: %s #%s - %s",
                job_name,
                build_number,
                err,
            )
            raise

    def list_build_artifacts(self, job_name: str, build_number: int) -> list[dict]:
        """List all artifacts for a build.

        Args:
            job_name: Name of the Jenkins job.
            build_number: Build number to retrieve.

        Returns:
            List of artifact dicts with 'relativePath' and 'fileName' keys.
            Returns empty list if no artifacts or on error.
        """
        try:
            build_info = self.get_build_info_safe(job_name, build_number)
            return build_info.get("artifacts", [])
        except Exception as exc:
            logger.warning(
                f"Failed to list artifacts for {job_name} #{build_number}: {exc}"
            )
            return []

    def download_artifact(
        self,
        job_name: str,
        build_number: int,
        relative_path: str,
        max_size_mb: int = 500,
    ) -> bytes | None:
        """Download a single build artifact by relative path.

        Uses the build URL from the Jenkins API to construct the download URL,
        and the client's existing authenticated session for the request.

        Args:
            job_name: Name of the Jenkins job.
            build_number: Build number containing the artifact.
            relative_path: Relative path of the artifact (from Jenkins API).
            max_size_mb: Maximum allowed artifact size in megabytes.

        Returns:
            Raw bytes of the artifact, or None if download fails.
        """
        try:
            build_info = self.get_build_info_safe(job_name, build_number)
            build_url = build_info.get("url", "").rstrip("/")
            if not build_url:
                logger.warning(f"No build URL found for {job_name} #{build_number}")
                return None

            url = f"{build_url}/artifact/{relative_path}"
            logger.info(f"Downloading artifact: {url}")

            response = self._session.get(url, stream=True, timeout=60)
            if response.status_code != 200:
                logger.warning(
                    f"Failed to download artifact '{relative_path}' from "
                    f"{job_name} #{build_number}: HTTP {response.status_code}"
                )
                return None

            buffer = io.BytesIO()
            downloaded = 0
            max_bytes = max_size_mb * 1024 * 1024

            for chunk in response.iter_content(chunk_size=8192):
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    logger.warning(
                        f"Artifact download exceeded maximum size ({max_size_mb} MB)"
                    )
                    return None
                buffer.write(chunk)

            return buffer.getvalue()

        except Exception as exc:
            logger.warning(f"Failed to download artifact '{relative_path}': {exc}")
            return None

    @staticmethod
    def parse_jenkins_url(url: str | HttpUrl) -> tuple[str, int]:
        """Parse Jenkins URL to extract job name and build number.

        Handles various Jenkins URL formats including nested folders.

        Args:
            url: Full Jenkins build URL (string or HttpUrl).

        Returns:
            Tuple of (job_name, build_number).

        Raises:
            ValueError: If URL format is invalid.

        Examples:
            >>> JenkinsClient.parse_jenkins_url("https://jenkins.example.com/job/my-job/123/")
            ('my-job', 123)
            >>> JenkinsClient.parse_jenkins_url("https://jenkins.example.com/job/folder/job/my-job/456")
            ('folder/job/my-job', 456)
        """
        path = urlparse(str(url)).path.rstrip("/")
        parts = path.split("/")

        if len(parts) < 2:
            raise ValueError(f"Invalid Jenkins URL format: {url}")

        try:
            build_number = int(parts[-1])
        except ValueError as err:
            raise ValueError(f"Could not parse build number from URL: {url}") from err

        # Find the job path by looking for /job/ segments
        job_parts = []
        i = 0
        while i < len(parts) - 1:
            if parts[i] == "job" and i + 1 < len(parts):
                job_parts.append(parts[i + 1])
                i += 2
            else:
                i += 1

        if not job_parts:
            # Fallback: assume the part before build number is the job name
            job_name = parts[-2]
        else:
            job_name = "/job/".join(job_parts)

        return job_name, build_number
