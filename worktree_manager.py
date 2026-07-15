"""
码搭 CodePilot · Worktree 隔离管理器

为每个任务创建独立的 Git Worktree 沙箱，执行完成后收集 diff 并清理。
非 git 项目降级为临时目录。
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class WorktreeManager:
    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()
        self._is_git = (self.repo_root / ".git").exists()
        self._temp_dirs: list[str] = []

    def create(self, task_id: str) -> str:
        """为任务创建工作区，返回工作区路径。"""
        if self._is_git:
            return self._create_worktree(task_id)
        else:
            return self._create_tempdir(task_id)

    def _create_worktree(self, task_id: str) -> str:
        branch = f"codepilot/task-{task_id[:8]}"
        worktree_path = self.repo_root.parent / f".codepilot-worktrees/task-{task_id[:8]}"

        try:
            subprocess.run(
                ["git", "checkout", "-b", branch],
                cwd=str(self.repo_root), capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass

        try:
            subprocess.run(
                ["git", "worktree", "add", str(worktree_path), branch],
                cwd=str(self.repo_root), capture_output=True, text=True, timeout=30,
                check=True,
            )
        except subprocess.CalledProcessError:
            worktree_path.mkdir(parents=True, exist_ok=True)
            return str(worktree_path)

        return str(worktree_path)

    def _create_tempdir(self, task_id: str) -> str:
        tmp = tempfile.mkdtemp(prefix=f"codepilot-task-{task_id[:8]}-")
        self._temp_dirs.append(tmp)
        return tmp

    def collect_diff(self, worktree_path: str) -> str:
        """收集 worktree 中的 git diff。"""
        if not self._is_git:
            return ""
        try:
            result = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=worktree_path, capture_output=True, text=True, timeout=15,
            )
            return result.stdout[:3000] if result.stdout else "(no changes)"
        except Exception as e:
            return f"(diff collection failed: {e})"

    def cleanup(self, worktree_path: str, task_id: str):
        """清理 worktree 和对应分支。"""
        branch = f"codepilot/task-{task_id[:8]}"
        if self._is_git:
            try:
                subprocess.run(
                    ["git", "worktree", "remove", worktree_path, "--force"],
                    cwd=str(self.repo_root), capture_output=True, text=True, timeout=15,
                )
            except Exception:
                pass
            try:
                subprocess.run(
                    ["git", "branch", "-D", branch],
                    cwd=str(self.repo_root), capture_output=True, text=True, timeout=15,
                )
            except Exception:
                pass
        elif os.path.exists(worktree_path):
            shutil.rmtree(worktree_path, ignore_errors=True)

    def cleanup_all(self):
        """清理所有临时目录。"""
        for tmp in self._temp_dirs:
            shutil.rmtree(tmp, ignore_errors=True)
        self._temp_dirs.clear()
