"""
码搭 CodePilot · MCP 端到端测试

起真实的 mcp.server 子进程，走通 initialize → tools/list → tools/call 全流程。
这是 MCP Client 的回归测试：协议层必须能解析 Response/Error 消息，
否则任何外部 MCP Server 都连不上。
"""

import json
import sys
from pathlib import Path

import pytest

from mcp.client import MCPClientManager

PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def mgr(tmp_path):
    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(json.dumps({
        "servers": [{
            "name": "self",
            "command": sys.executable,
            "args": ["-m", "mcp.server"],
            "cwd": str(PROJECT_ROOT),
        }]
    }), encoding="utf-8")
    manager = MCPClientManager(str(cfg))
    manager.connect_all()
    yield manager
    manager.disconnect_all()


def test_tools_registered_with_prefix(mgr):
    """15 个内置工具全部以 mcp_self_ 前缀注册。"""
    assert len(mgr.tool_definitions) == 15
    names = {t["function"]["name"] for t in mgr.tool_definitions}
    assert "mcp_self_list_files" in names
    assert "mcp_self_run_shell" in names


def test_call_tool_through_stdio(mgr):
    """通过 stdio JSON-RPC 真实调用工具并拿到结果。"""
    result = mgr.call_tool("mcp_self_list_files", {"path": "."})
    assert "main.py" in result
    assert "agent.py" in result


def test_dangerous_tool_rejected_over_mcp(mgr):
    """MCP 是无交互通道，危险工具默认被拒绝。"""
    result = mgr.call_tool("mcp_self_run_shell", {"command": "echo hello"})
    assert "拒绝" in result


def test_unknown_tool(mgr):
    result = mgr.call_tool("mcp_self_nonexistent", {})
    assert "错误" in result
