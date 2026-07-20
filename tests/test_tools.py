"""
码搭 CodePilot · 工具函数测试
"""

import os
import tempfile
from pathlib import Path

import pytest

from tools.core_tools import search_code, list_files, write_file, read_file, run_shell, _resolve
from tools import execute_tool, TOOLS_REGISTRY, DANGEROUS_TOOLS


class TestCoreTools:
    def test_search_code_finds_match(self):
        result = search_code("def run", str(Path(__file__).parent.parent))
        assert "agent.py" in result
        assert "找到" in result

    def test_search_code_invalid_regex(self):
        result = search_code("[invalid", str(Path(__file__).parent.parent))
        assert "正则表达式无效" in result

    def test_list_files_current_dir(self):
        result = list_files(str(Path(__file__).parent.parent))
        assert "main.py" in result
        assert "agent.py" in result

    def test_read_file(self):
        content = read_file(str(Path(__file__).parent.parent / "requirements.txt"))
        assert "rich" in content

    def test_read_file_not_found(self):
        result = read_file("nonexistent_file_xyz.txt")
        assert "文件不存在" in result

    def test_write_and_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.txt")
            write_file(path, "hello world")
            content = read_file(path)
            assert "hello world" in content

    def test_run_shell_dangerous_blocked(self):
        result = run_shell("rm -rf /")
        assert "拦截" in result

    def test_resolve_relative_path(self):
        result = _resolve("setup.py")
        assert result.is_absolute()

    def test_search_code_no_match_in_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = search_code("xyznonexistent789", tmp)
            assert "未找到" in result


class TestToolDispatcher:
    def test_execute_known_tool(self):
        result = execute_tool("list_files", {"path": str(Path(__file__).parent.parent)})
        assert "main.py" in result

    def test_execute_unknown_tool(self):
        result = execute_tool("nonexistent_tool", {})
        assert "未知工具" in result

    def test_execute_with_none_args(self):
        result = execute_tool("list_files", {"path": None})
        assert "main.py" in result

    def test_all_tools_registered(self):
        assert len(TOOLS_REGISTRY) == 15
        for name in ["read_file", "write_file", "list_files", "search_code", "run_shell",
                      "git_status", "git_diff", "git_log", "git_branch", "git_add", "git_commit",
                      "web_search", "web_fetch", "search_semantic", "index_project"]:
            assert name in TOOLS_REGISTRY, f"Missing tool: {name}"

    def test_dangerous_tools(self):
        assert "run_shell" in DANGEROUS_TOOLS
        assert "git_add" in DANGEROUS_TOOLS
        assert "git_commit" in DANGEROUS_TOOLS
        assert "read_file" not in DANGEROUS_TOOLS


class TestGitTools:
    def test_git_status_non_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            from tools.git_tools import git_status
            result = git_status(tmp)
            assert "不是 git 仓库" in result

    def test_git_diff_non_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            from tools.git_tools import git_diff
            result = git_diff(path=tmp)
            assert "不是 git 仓库" in result

    def test_git_commit_empty_message(self):
        from tools.git_tools import git_commit
        result = git_commit("", path=str(Path(__file__).parent.parent))
        assert "不能为空" in result or "不是 git" in result


class TestWorkdirInjection:
    """execute_tool 的 workdir 注入：相对路径基于 workdir 解析，且不改变进程 cwd。"""

    def test_execute_tool_injects_workdir(self, tmp_path):
        cwd_before = os.getcwd()
        result = execute_tool(
            "write_file", {"path": "sub/a.txt", "content": "hi"},
            workdir=str(tmp_path),
        )
        assert "成功" in result
        assert (tmp_path / "sub" / "a.txt").read_text(encoding="utf-8") == "hi"
        assert os.getcwd() == cwd_before  # 无 chdir 副作用

    def test_llm_cannot_override_workdir(self, tmp_path):
        # LLM 侧传入的 workdir 参数会被剥离，以注入值为准
        execute_tool(
            "write_file",
            {"path": "b.txt", "content": "x", "workdir": "/nonexistent-xyz"},
            workdir=str(tmp_path),
        )
        assert (tmp_path / "b.txt").exists()

    def test_workdir_none_falls_back_to_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = execute_tool("write_file", {"path": "c.txt", "content": "y"})
        assert "成功" in result
        assert (tmp_path / "c.txt").exists()

    def test_run_shell_uses_workdir(self, tmp_path):
        result = execute_tool(
            "run_shell",
            {"command": "python -c \"import os; print(os.getcwd())\""},
            workdir=str(tmp_path),
        )
        assert str(tmp_path).replace("\\", "/").lower() in result.replace("\\", "/").lower()
