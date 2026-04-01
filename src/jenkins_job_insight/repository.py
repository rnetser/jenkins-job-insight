"""Git repository management for cloning and cleanup."""

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Self

from git import Repo
from git.exc import GitCommandError
from pydantic import HttpUrl
from simple_logger.logger import get_logger

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))


def _validate_repo_url(repo_url: str | HttpUrl) -> None:
    """Validate repository URL scheme to prevent SSRF."""
    url_str = str(repo_url).lower()
    if not url_str.startswith(("https://", "git://")):
        raise ValueError(
            f"Invalid repository URL scheme. Only https:// and git:// are allowed, got: {repo_url}"
        )


def _clone_with_ssl_retry(repo_url: str, clone_dir: Path, depth: int) -> None:
    """Clone a repo, retrying without SSL verification on cert errors."""
    try:
        Repo.clone_from(repo_url, clone_dir, depth=depth)
    except GitCommandError as exc:
        stderr = str(exc)
        if (
            "SSL" in stderr
            or "certificate" in stderr
            or "server verification failed" in stderr
        ):
            logger.warning(
                f"SSL certificate verification failed for {repo_url}, retrying without verification"
            )
            Repo.clone_from(
                repo_url,
                clone_dir,
                depth=depth,
                env={"GIT_SSL_NO_VERIFY": "1"},
            )
        else:
            raise


class RepositoryManager:
    """Manages temporary git repository clones."""

    def __init__(self) -> None:
        """Initialize repository manager."""
        self.base_path = Path(tempfile.gettempdir()) / "jenkins-insight"
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.temp_dirs: list[Path] = []

    def clone(self, repo_url: str | HttpUrl, depth: int = 50) -> Path:
        """Clone repository to a unique temporary directory.

        Each clone gets a UUID-based directory to support parallel webhook calls.

        Args:
            repo_url: URL of the git repository to clone (string or HttpUrl).
            depth: Number of commits to fetch for git history context.

        Returns:
            Path to the cloned repository.

        Raises:
            ValueError: If repo_url uses an unsafe scheme (not https:// or git://).
        """
        _validate_repo_url(repo_url)
        clone_id = str(uuid.uuid4())[:8]
        repo_name = str(repo_url).rstrip("/").split("/")[-1].replace(".git", "")
        clone_dir = self.base_path / f"{repo_name}-{clone_id}"
        clone_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dirs.append(clone_dir)
        logger.info(f"Cloning repository to {clone_dir}")
        _clone_with_ssl_retry(str(repo_url), clone_dir, depth)
        return clone_dir

    def clone_into(
        self, repo_url: str | HttpUrl, target_dir: Path, depth: int = 1
    ) -> Path:
        """Clone repository into a specific target directory.

        Unlike clone(), this places the repo at an explicit path instead
        of auto-generating one.  The target directory is NOT tracked for
        cleanup -- its parent is expected to manage lifecycle.

        Args:
            repo_url: URL of the git repository to clone.
            target_dir: Exact directory to clone into.
            depth: Number of commits to fetch (default 1 for shallow).

        Returns:
            Path to the cloned repository (same as target_dir).

        Raises:
            ValueError: If repo_url uses an unsafe scheme.
        """
        _validate_repo_url(repo_url)
        target_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Cloning repository into {target_dir}")
        _clone_with_ssl_retry(str(repo_url), target_dir, depth)
        return target_dir

    def create_workspace(self) -> Path:
        """Create an empty workspace directory for additional repos.

        Returns:
            Path to the created workspace directory.
        """
        workspace_id = str(uuid.uuid4())[:8]
        workspace_dir = self.base_path / f"workspace-{workspace_id}"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dirs.append(workspace_dir)
        logger.info(f"Created workspace directory: {workspace_dir}")
        return workspace_dir

    def cleanup(self) -> None:
        """Remove all cloned repositories."""
        logger.debug("Cleaning up temporary directories")
        for temp_dir in self.temp_dirs:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
        self.temp_dirs.clear()

    def __enter__(self) -> Self:
        """Enter context manager."""
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object,
    ) -> None:
        """Exit context manager and cleanup."""
        self.cleanup()
