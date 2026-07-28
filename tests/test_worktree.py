import subprocess
from pathlib import Path

from worktree_manager import WorktreeManager


def _git(repo: Path, *args: str):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def test_worktree_isolates_task_and_preserves_main_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "CodePilot Tests")
    (repo / "tracked.txt").write_text("main", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    original_branch = _git(repo, "branch", "--show-current").stdout.strip()

    manager = WorktreeManager(str(repo))
    worktree = manager.create("task-isolation")
    assert worktree is not None
    worktree_path = Path(worktree)
    try:
        (worktree_path / "tracked.txt").write_text("task", encoding="utf-8")
        assert (repo / "tracked.txt").read_text(encoding="utf-8") == "main"
        assert _git(repo, "branch", "--show-current").stdout.strip() == original_branch
        assert "tracked.txt" in manager.collect_diff(worktree)
    finally:
        manager.cleanup(worktree, "task-isolation")

    assert not worktree_path.exists()


def test_non_git_directory_does_not_create_worktree(tmp_path):
    manager = WorktreeManager(str(tmp_path))
    assert manager.is_git_repository is False
    assert manager.create("task-no-git") is None
