"""
码搭 CodePilot · Worktree 隔离管理器

为每个任务创建独立的 Git Worktree 沙箱，执行完成后收集 diff 并清理。
创建 worktree 不会切换主仓库的当前分支（git worktree add -b 一步完成）。
非 git 项目或创建失败时返回 None，由调用方降级为在项目目录直接执行。
"""

import subprocess
from pathlib import Path
from typing import Optional


class WorktreeManager:
    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()
        self._is_git = (self.repo_root / ".git").exists()

    def create(self, task_id: str) -> Optional[str]:
        """为任务创建工作区，返回工作区路径；失败或非 git 项目返回 None。"""
        if not self._is_git:
            return None
        return self._create_worktree(task_id)

    def _branch_name(self, task_id: str) -> str:
        # task_id 形如 task-000001，必须整体使用，
        # 截断（如 [:8]）会导致不同任务得到同名分支互相冲突
        return f"codepilot/{task_id}"

    def _create_worktree(self, task_id: str) -> Optional[str]:
        branch = self._branch_name(task_id)
        worktree_path = self.repo_root.parent / ".codepilot-worktrees" / task_id

        # 基于当前 HEAD 创建新分支的 worktree，不切换主仓库分支
        ok = self._git(["worktree", "add", "-b", branch, str(worktree_path)])
        if not ok:
            # 可能残留同名分支（异常中断的任务），清理后重试一次
            self._git(["branch", "-D", branch])
            ok = self._git(["worktree", "add", "-b", branch, str(worktree_path)])
        if not ok:
            return None
        return str(worktree_path)

    def _git(self, args: list, cwd: str = None, timeout: int = 30) -> bool:
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=cwd or str(self.repo_root),
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
            return result.returncode == 0
        except Exception:
            return False

    def collect_diff(self, worktree_path: str) -> str:
        """收集 worktree 中的变更概览（含未跟踪的新文件）。"""
        if not self._is_git or not worktree_path:
            return ""
        try:
            stat = subprocess.run(
                ["git", "-c", "core.quotepath=false", "diff", "--stat"],
                cwd=worktree_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            ).stdout
            status = subprocess.run(
                ["git", "-c", "core.quotepath=false", "status", "--short"],
                cwd=worktree_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            ).stdout
            parts = []
            if stat.strip():
                parts.append(stat.strip())
            if status.strip():
                parts.append("变更文件:\n" + status.strip())
            return "\n".join(parts)[:3000] if parts else "(no changes)"
        except Exception as e:
            return f"(diff collection failed: {e})"

    def cleanup(self, worktree_path: str, task_id: str):
        """清理 worktree 和对应分支。"""
        if not self._is_git or not worktree_path:
            return
        self._git(["worktree", "remove", worktree_path, "--force"], timeout=15)
        self._git(["branch", "-D", self._branch_name(task_id)], timeout=15)

    def cleanup_all(self):
        """清理所有残留的 codepilot worktree（服务关闭时调用）。"""
        if not self._is_git:
            return
        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=str(self.repo_root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            )
            for line in result.stdout.splitlines():
                if line.startswith("worktree ") and ".codepilot-worktrees" in line:
                    self._git(["worktree", "remove", line[len("worktree "):], "--force"], timeout=15)
        except Exception:
            pass
