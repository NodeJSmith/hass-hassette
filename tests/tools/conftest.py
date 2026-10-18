"""Shared fixtures for the CI gate script tests."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass
class GitRepo:
    """A throwaway git repo under ``tmp_path``, for tests of the git-diff lint helpers."""

    root: Path

    def write(self, rel_path: str, content: str) -> None:
        target = self.root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def rm(self, rel_path: str) -> None:
        (self.root / rel_path).unlink()

    def commit(self, message: str) -> str:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=self.root, check=True, capture_output=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout.strip()

    def diverge(self, rel_path: str, content: str, message: str, from_ref: str, back_to: str = "main") -> str:
        """Check out `from_ref`, commit an unrelated change, then return to `back_to`.

        Simulates the base branch moving on, unrelated to the PR, after the PR's branch point --
        the shared setup behind every test proving the "new code" gates diff against merge-base,
        not `base_ref`'s live tip.
        """
        subprocess.run(["git", "checkout", "-q", from_ref], cwd=self.root, check=True)
        self.write(rel_path, content)
        diverged_tip = self.commit(message)
        subprocess.run(["git", "checkout", "-q", back_to], cwd=self.root, check=True)
        return diverged_tip


@pytest.fixture
def git_repo(tmp_path: Path) -> GitRepo:
    """An isolated git repo with a configured committer identity, ready for real commits.

    Real git plumbing rather than mocked subprocess output: the git-diff helpers are thin
    wrappers over `git diff`/`git show`/`git merge-base`, and their exact output format (hunk
    headers, rename markers, `+++`/`---` lines) is the thing under test — mocking it would just
    re-assert whatever the mock was told to return.
    """
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)
    return GitRepo(tmp_path)
