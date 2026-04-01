"""Tests for repository management."""

from unittest.mock import MagicMock, patch

import git.exc
import pytest
from git.exc import GitCommandError

from jenkins_job_insight.repository import RepositoryManager, repo_name_from_url


class TestRepositoryManager:
    """Tests for the RepositoryManager class."""

    def test_init_empty_temp_dirs(self) -> None:
        """Test that RepositoryManager initializes with empty temp_dirs list."""
        manager = RepositoryManager()
        assert manager.temp_dirs == []

    @patch("jenkins_job_insight.repository.Repo")
    def test_clone_creates_temp_directory(self, mock_repo: MagicMock) -> None:
        """Test that clone creates a temporary directory."""
        manager = RepositoryManager()
        repo_url = "https://github.com/example/repo"

        result = manager.clone(repo_url)

        assert result.exists()
        assert len(manager.temp_dirs) == 1
        assert manager.temp_dirs[0] == result
        mock_repo.clone_from.assert_called_once()

        # Cleanup
        manager.cleanup()

    @patch("jenkins_job_insight.repository.Repo")
    def test_clone_with_custom_depth(self, mock_repo: MagicMock) -> None:
        """Test that clone passes correct depth parameter."""
        manager = RepositoryManager()
        repo_url = "https://github.com/example/repo"
        depth = 100

        manager.clone(repo_url, depth=depth)

        mock_repo.clone_from.assert_called_once()
        call_kwargs = mock_repo.clone_from.call_args
        assert call_kwargs[1]["depth"] == depth

        # Cleanup
        manager.cleanup()

    @patch("jenkins_job_insight.repository.Repo")
    def test_clone_multiple_repos(self, mock_repo: MagicMock) -> None:
        """Test cloning multiple repositories."""
        manager = RepositoryManager()

        path1 = manager.clone("https://github.com/example/repo1")
        path2 = manager.clone("https://github.com/example/repo2")

        assert len(manager.temp_dirs) == 2
        assert path1 != path2
        assert path1 in manager.temp_dirs
        assert path2 in manager.temp_dirs
        assert mock_repo.clone_from.call_count == 2

        # Cleanup
        manager.cleanup()

    @patch("jenkins_job_insight.repository.Repo")
    def test_cleanup_removes_directories(self, _mock_repo: MagicMock) -> None:
        """Test that cleanup removes all temporary directories."""
        manager = RepositoryManager()

        path1 = manager.clone("https://github.com/example/repo1")
        path2 = manager.clone("https://github.com/example/repo2")

        # Directories should exist before cleanup
        assert path1.exists()
        assert path2.exists()

        manager.cleanup()

        # Directories should be removed after cleanup
        assert not path1.exists()
        assert not path2.exists()
        assert manager.temp_dirs == []

    @patch("jenkins_job_insight.repository.Repo")
    def test_cleanup_handles_missing_directory(self, _mock_repo: MagicMock) -> None:
        """Test that cleanup handles already deleted directories gracefully."""
        manager = RepositoryManager()

        path = manager.clone("https://github.com/example/repo")

        # Manually delete the directory
        path.rmdir()

        # Cleanup should not raise an error
        manager.cleanup()
        assert manager.temp_dirs == []

    @patch("jenkins_job_insight.repository.Repo")
    def test_context_manager_enter(self, _mock_repo: MagicMock) -> None:
        """Test context manager __enter__ returns self."""
        manager = RepositoryManager()

        with manager as ctx:
            assert ctx is manager

    @patch("jenkins_job_insight.repository.Repo")
    def test_context_manager_cleanup_on_exit(self, _mock_repo: MagicMock) -> None:
        """Test context manager cleans up on exit."""
        path = None
        with RepositoryManager() as manager:
            path = manager.clone("https://github.com/example/repo")
            assert path.exists()

        # Directory should be cleaned up after context manager exits
        assert not path.exists()

    @patch("jenkins_job_insight.repository.Repo")
    def test_context_manager_cleanup_on_exception(self, _mock_repo: MagicMock) -> None:
        """Test context manager cleans up even when exception occurs."""
        path = None
        try:
            with RepositoryManager() as manager:
                path = manager.clone("https://github.com/example/repo")
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Directory should be cleaned up after exception
        assert path is not None
        assert not path.exists()

    @patch("jenkins_job_insight.repository.Repo")
    def test_clone_uses_jenkins_insight_prefix(self, _mock_repo: MagicMock) -> None:
        """Test that cloned directories are under jenkins-insight base path."""
        manager = RepositoryManager()

        path = manager.clone("https://github.com/example/repo")

        # The path should be under the jenkins-insight base directory
        assert "jenkins-insight" in str(path)
        assert path.parent.name == "jenkins-insight"

        # Cleanup
        manager.cleanup()

    @patch("jenkins_job_insight.repository.Repo")
    def test_clone_rejects_file_url(self, _mock_repo: MagicMock) -> None:
        """Test that clone rejects file:// URLs."""
        manager = RepositoryManager()
        with pytest.raises(ValueError, match="Only https:// and git://"):
            manager.clone("file:///etc/passwd")

    @patch("jenkins_job_insight.repository.Repo")
    def test_clone_rejects_ssh_url(self, _mock_repo: MagicMock) -> None:
        """Test that clone rejects ssh:// URLs."""
        manager = RepositoryManager()
        with pytest.raises(ValueError, match="Only https:// and git://"):
            manager.clone("ssh://git@github.com/org/repo")

    @patch("jenkins_job_insight.repository.Repo")
    def test_clone_real_failure_handling(self, mock_repo: MagicMock) -> None:
        """Test that clone raises error for invalid repository."""
        mock_repo.clone_from.side_effect = git.exc.GitCommandError("clone", 128)
        manager = RepositoryManager()

        with pytest.raises(git.exc.GitCommandError):
            manager.clone("https://invalid-url-that-does-not-exist.example.com/repo")

        # Even on failure, cleanup should work
        manager.cleanup()


class TestCloneInto:
    """Tests for RepositoryManager.clone_into."""

    @patch("jenkins_job_insight.repository.Repo")
    def test_clone_into_creates_directory(self, mock_repo: MagicMock, tmp_path) -> None:
        """Test that clone_into creates the target directory and clones."""
        manager = RepositoryManager()
        target = tmp_path / "my-repo"
        result = manager.clone_into("https://github.com/org/repo", target, depth=1)
        assert result == target
        assert target.exists()
        mock_repo.clone_from.assert_called_once_with(
            "https://github.com/org/repo", target, depth=1
        )

    @patch("jenkins_job_insight.repository.Repo")
    def test_clone_into_not_tracked_for_cleanup(
        self, _mock_repo: MagicMock, tmp_path
    ) -> None:
        """Test that clone_into does NOT add directory to temp_dirs."""
        manager = RepositoryManager()
        target = tmp_path / "my-repo"
        manager.clone_into("https://github.com/org/repo", target)
        assert target not in manager.temp_dirs

    def test_clone_into_rejects_file_url(self) -> None:
        """Test that clone_into rejects file:// URLs."""
        from pathlib import Path

        manager = RepositoryManager()
        with pytest.raises(ValueError, match="Only https:// and git://"):
            manager.clone_into("file:///etc/passwd", Path("/tmp/test"))

    def test_clone_into_rejects_ssh_url(self) -> None:
        """Test that clone_into rejects ssh:// URLs."""
        from pathlib import Path

        manager = RepositoryManager()
        with pytest.raises(ValueError, match="Only https:// and git://"):
            manager.clone_into("ssh://git@github.com/org/repo", Path("/tmp/test"))


class TestCloneWithSslRetry:
    """Tests for SSL certificate retry logic."""

    def test_clone_into_retries_on_ssl_error(self, tmp_path) -> None:
        """clone_into retries with GIT_SSL_NO_VERIFY on cert failure."""
        manager = RepositoryManager()
        with patch("jenkins_job_insight.repository.Repo.clone_from") as mock_clone:
            mock_clone.side_effect = [
                GitCommandError(
                    "git clone",
                    128,
                    stderr="server verification failed: certificate signer not trusted",
                ),
                MagicMock(),
            ]
            target = tmp_path / "repo"
            target.mkdir()
            manager.clone_into("https://example.com/repo", target)
            assert mock_clone.call_count == 2
            _, kwargs = mock_clone.call_args
            assert kwargs.get("env", {}).get("GIT_SSL_NO_VERIFY") == "1"

    def test_clone_into_does_not_retry_on_other_errors(self, tmp_path) -> None:
        """Non-SSL errors are not retried."""
        manager = RepositoryManager()
        with patch("jenkins_job_insight.repository.Repo.clone_from") as mock_clone:
            mock_clone.side_effect = GitCommandError(
                "git clone", 128, stderr="repository not found"
            )
            with pytest.raises(GitCommandError):
                manager.clone_into("https://example.com/repo", tmp_path / "repo")
            assert mock_clone.call_count == 1

    def test_clone_into_succeeds_without_retry(self, tmp_path) -> None:
        """Successful clone doesn't trigger retry."""
        manager = RepositoryManager()
        with patch("jenkins_job_insight.repository.Repo.clone_from") as mock_clone:
            mock_clone.return_value = MagicMock()
            target = tmp_path / "repo"
            target.mkdir()
            manager.clone_into("https://example.com/repo", target)
            assert mock_clone.call_count == 1

    def test_clone_retries_on_ssl_error(self, tmp_path) -> None:
        """clone() retries with GIT_SSL_NO_VERIFY on SSL cert failure."""
        manager = RepositoryManager()
        with patch("jenkins_job_insight.repository.Repo.clone_from") as mock_clone:
            mock_clone.side_effect = [
                GitCommandError(
                    "git clone",
                    128,
                    stderr="SSL certificate problem: unable to get local issuer certificate",
                ),
                MagicMock(),
            ]
            manager.clone("https://example.com/repo")
            assert mock_clone.call_count == 2
            _, kwargs = mock_clone.call_args
            assert kwargs.get("env", {}).get("GIT_SSL_NO_VERIFY") == "1"
        manager.cleanup()

    def test_clone_does_not_retry_on_other_errors(self) -> None:
        """clone() non-SSL errors are not retried."""
        manager = RepositoryManager()
        with patch("jenkins_job_insight.repository.Repo.clone_from") as mock_clone:
            mock_clone.side_effect = GitCommandError(
                "git clone", 128, stderr="repository not found"
            )
            with pytest.raises(GitCommandError):
                manager.clone("https://example.com/repo")
            assert mock_clone.call_count == 1
        manager.cleanup()

    def test_clone_retries_on_certificate_keyword(self) -> None:
        """clone() retries when stderr contains 'certificate'."""
        manager = RepositoryManager()
        with patch("jenkins_job_insight.repository.Repo.clone_from") as mock_clone:
            mock_clone.side_effect = [
                GitCommandError(
                    "git clone",
                    128,
                    stderr="fatal: unable to access: certificate verify failed",
                ),
                MagicMock(),
            ]
            manager.clone("https://example.com/repo")
            assert mock_clone.call_count == 2
        manager.cleanup()


class TestCreateWorkspace:
    """Tests for RepositoryManager.create_workspace."""

    def test_create_workspace_returns_path(self) -> None:
        """create_workspace returns a Path under base_path."""
        manager = RepositoryManager()
        workspace = manager.create_workspace()
        assert workspace.exists()
        assert workspace.parent == manager.base_path
        assert "workspace-" in workspace.name
        manager.cleanup()

    def test_create_workspace_tracked_for_cleanup(self) -> None:
        """create_workspace adds path to temp_dirs."""
        manager = RepositoryManager()
        workspace = manager.create_workspace()
        assert workspace in manager.temp_dirs
        manager.cleanup()
        assert not workspace.exists()


class TestRepoNameFromUrl:
    """Tests for the repo_name_from_url utility function."""

    def test_https_url_with_git_suffix(self) -> None:
        """Extract repo name from HTTPS URL ending in .git."""
        assert repo_name_from_url("https://github.com/org/my-repo.git") == "my-repo"

    def test_https_url_without_git_suffix(self) -> None:
        """Extract repo name from HTTPS URL without .git."""
        assert repo_name_from_url("https://github.com/org/my-repo") == "my-repo"

    def test_url_with_trailing_slash(self) -> None:
        """Strip trailing slash before extracting."""
        assert repo_name_from_url("https://github.com/org/my-repo/") == "my-repo"

    def test_url_with_trailing_slash_and_git(self) -> None:
        """Strip trailing slash and .git."""
        assert repo_name_from_url("https://github.com/org/my-repo.git/") == "my-repo"

    def test_git_protocol_url(self) -> None:
        """Extract from git:// URL."""
        assert repo_name_from_url("git://github.com/org/my-repo.git") == "my-repo"

    def test_pydantic_httpurl(self) -> None:
        """Works with pydantic HttpUrl objects."""
        from pydantic import HttpUrl

        url = HttpUrl("https://github.com/org/my-repo.git")
        assert repo_name_from_url(url) == "my-repo"


class TestCloneWithSslRetryCleanup:
    """Tests for Finding 2: partial clone cleanup before SSL retry."""

    def test_partial_clone_cleaned_before_retry(self, tmp_path) -> None:
        """After a failed SSL clone, the target dir is cleaned before retry."""
        manager = RepositoryManager()
        target = tmp_path / "repo"
        target.mkdir()
        # Create a partial file in the target to simulate partial clone
        partial_file = target / "partial-object"
        partial_file.write_text("partial data")

        with patch("jenkins_job_insight.repository.Repo.clone_from") as mock_clone:
            mock_clone.side_effect = [
                GitCommandError(
                    "git clone",
                    128,
                    stderr="server verification failed: certificate signer not trusted",
                ),
                MagicMock(),
            ]
            manager.clone_into("https://example.com/repo", target)

        # The partial file should have been removed before the retry
        assert not partial_file.exists()
        # Target dir should still exist (recreated for the retry)
        assert target.exists()
