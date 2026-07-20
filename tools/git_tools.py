"""
码搭 CodePilot · Git 工具
git_status / git_diff / git_log / git_branch / git_add / git_commit
"""

import os
import subprocess
from pathlib import Path
from typing import Optional


def _resolve_git(path: str = ".", workdir: Optional[str] = None) -> str:
    """返回绝对路径并验证是否为 git 仓库"""
    p = Path(path)
    if not p.is_absolute():
        p = Path(workdir or os.getcwd()) / p
    p = p.resolve()
    if not (p / ".git").exists():
        raise ValueError(f"目录不是 git 仓库: {p}")
    return str(p)


def _run_git(args: list, cwd: str) -> str:
    """执行 git 命令并返回规范化输出"""
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false"] + args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, cwd=cwd,
        )
        output = result.stdout + result.stderr
        if not output.strip():
            return f"[完成] git {' '.join(args)} 执行成功（无输出）"
        from config import config
        _max_chars = config.get("tools.diff_max_chars", 3000)
        return output[:_max_chars]
    except subprocess.TimeoutExpired:
        return "[超时] 命令执行超时"
    except FileNotFoundError:
        return "[错误] 未找到 git，请确认 git 已安装"


def git_status(path: str = ".", workdir: Optional[str] = None) -> str:
    """
    查看 git 仓库工作区状态。参数 path: 仓库路径（默认当前目录）。返回 git status --short 的简略输出。
    """
    try:
        repo = _resolve_git(path, workdir)
    except ValueError as e:
        return f"[错误] {e}"
    return _run_git(["status", "--short"], repo)


def git_diff(staged: bool = False, path: str = ".", workdir: Optional[str] = None) -> str:
    """
    查看 git 差异。参数 staged: 是否查看暂存区差异（默认 false 查看未暂存的部分）、path: 仓库路径。返回 diff 内容。
    """
    try:
        repo = _resolve_git(path, workdir)
    except ValueError as e:
        return f"[错误] {e}"
    args = ["diff"]
    if staged:
        args.append("--cached")
    return _run_git(args, repo)


def git_log(n: int = 10, path: str = ".", workdir: Optional[str] = None) -> str:
    """
    查看 git 提交日志。参数 n: 显示最近 N 条记录（默认 10）、path: 仓库路径。
    """
    try:
        repo = _resolve_git(path, workdir)
    except ValueError as e:
        return f"[错误] {e}"
    return _run_git(["log", "--oneline", f"-n{n}"], repo)


def git_branch(path: str = ".", workdir: Optional[str] = None) -> str:
    """
    列出所有分支。参数 path: 仓库路径。当前分支会以 * 标记。返回分支列表。
    """
    try:
        repo = _resolve_git(path, workdir)
    except ValueError as e:
        return f"[错误] {e}"
    return _run_git(["branch", "-a"], repo)


def git_add(files: str, path: str = ".", workdir: Optional[str] = None) -> str:
    """
    暂存文件到 git 暂存区（需用户确认）。参数 files: 要暂存的文件，多个文件空格分隔，"." 暂存全部、path: 仓库路径。
    """
    try:
        repo = _resolve_git(path, workdir)
    except ValueError as e:
        return f"[错误] {e}"
    if not files.strip():
        return "[错误] 请指定要暂存的文件"
    if files.strip() == ".":
        return "[警告] 即将暂存所有文件，请确认后再操作。\n" + _run_git(["add", "."], repo)
    return _run_git(["add"] + files.split(), repo)


def git_commit(message: str, path: str = ".", workdir: Optional[str] = None) -> str:
    """
    创建 git commit（需用户确认，禁止 --no-verify 和 --amend）。参数 message: commit 消息、path: 仓库路径。
    """
    try:
        repo = _resolve_git(path, workdir)
    except ValueError as e:
        return f"[错误] {e}"
    if not message.strip():
        return "[错误] commit 消息不能为空"
    return _run_git(["commit", "-m", message], repo)


GIT_TOOLS = {
    "git_status": git_status,
    "git_diff": git_diff,
    "git_log": git_log,
    "git_branch": git_branch,
    "git_add": git_add,
    "git_commit": git_commit,
}

GIT_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "查看 git 仓库工作区状态（已修改/未跟踪文件）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "仓库路径，默认当前目录"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "查看 git 差异。默认显示未暂存的更改，设置 staged=true 查看已暂存的。",
            "parameters": {
                "type": "object",
                "properties": {
                    "staged": {"type": "boolean", "description": "true 查看暂存区差异(git diff --cached)，默认 false"},
                    "path": {"type": "string", "description": "仓库路径，默认当前目录"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "查看最近的 git 提交日志",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "显示最近 N 条记录，默认 10"},
                    "path": {"type": "string", "description": "仓库路径，默认当前目录"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_branch",
            "description": "列出所有本地和远程分支，当前分支以 * 标记",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "仓库路径，默认当前目录"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_add",
            "description": "暂存文件到 git 暂存区。此操作需用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {"type": "string", "description": "要暂存的文件路径，多个以空格分隔，'.' 暂存所有"},
                    "path": {"type": "string", "description": "仓库路径，默认当前目录"},
                },
                "required": ["files"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "创建 git commit。此操作需用户确认。不会跳过 hooks 或 amend。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "commit 消息"},
                    "path": {"type": "string", "description": "仓库路径，默认当前目录"},
                },
                "required": ["message"],
            },
        },
    },
]

GIT_DANGEROUS_TOOLS = {"git_add", "git_commit"}
