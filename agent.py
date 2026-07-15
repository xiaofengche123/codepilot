"""
码搭 CodePilot · Agent 核心循环

ReAct 模式（推理-行动-观察循环）：
  用户输入 → LLM 思考 → 决定调工具或回答
  调工具 → 执行 → 结果反馈 → LLM 再思考 → ... → 最终回答
"""

import os
import json
from typing import Optional

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from rich.console import Console

from config import config
from tools import TOOL_DEFINITIONS, execute_tool, DANGEROUS_TOOLS
from model_router import get_llm as router_get_llm
from memory import load_history, save_turn
from context_mgr import ContextManager

console = Console()

# ============================================================
# 系统提示词 — 定义 Agent 的行为边界和风格
# ============================================================

SYSTEM_PROMPT = """你是「码搭」，一个智能编程助手 Agent。

你有以下能力：
- 读写文件、列出目录、搜索代码
- 执行终端命令（危险命令会被自动拦截）
- Git 操作：查看状态(git_status)、差异(git_diff)、日志(git_log)、分支(git_branch)、暂存(git_add)、提交(git_commit)
- 网页搜索(web_search)：在互联网上搜索最新信息
- 抓取网页(web_fetch)：获取指定 URL 的内容并转为纯文本
- 语义搜索(search_semantic)：用自然语言查找代码功能，需要先 index_project 索引项目
- 项目索引(index_project)：对当前项目代码建立向量索引，支持增量更新
- 回答编程问题、解释代码、调试错误

工作原则：
1. 做任何操作前，先观察（list_files、search_code、read_file）
2. 代码修改要精准，用 search_code 找到目标位置再改
3. 执行 shell 命令前，先读一下当前环境确认安全
4. git commit 和 git add 需要用户确认，不要自动执行
5. 回答要简洁、直接、给代码示例
6. 如果用户的问题和你的工具无关，直接文字回答即可"""

# ============================================================
# Agent 主循环
# ============================================================

MAX_ITERATIONS = config.get("agent.max_iterations", 10)


class AgentSession:
    """有状态的 Agent 会话，维护跨轮对话连续性。"""

    def __init__(self, working_dir: str, max_context_tokens: int = None):
        if max_context_tokens is None:
            max_context_tokens = config.get("agent.max_context_tokens", 8000)
        self.working_dir = os.path.abspath(working_dir)
        self.context_mgr = ContextManager(max_tokens=max_context_tokens)
        self.mcp_client = None
        self._init_mcp()

    def _init_mcp(self):
        """加载 MCP 服务器配置并连接。"""
        import sys
        config_path = os.path.join(os.path.dirname(__file__), "mcp_servers.json")
        if not os.path.exists(config_path):
            return
        try:
            from mcp.client import MCPClientManager
            self.mcp_client = MCPClientManager(config_path)
            self.mcp_client.connect_all()
        except Exception as e:
            print(f"  [MCP] 初始化失败: {e}", file=sys.stderr)

    def _get_all_tools(self) -> list:
        """合并内置工具和 MCP 工具定义。"""
        all_tools = list(TOOL_DEFINITIONS)
        if self.mcp_client and self.mcp_client.tool_definitions:
            all_tools = all_tools + self.mcp_client.tool_definitions
        return all_tools

    def _execute_tool(self, tool_name: str, tool_args: dict) -> str:
        """执行工具：优先 MCP，其次内置。"""
        if self.mcp_client and tool_name in self.mcp_client.tools:
            return self.mcp_client.call_tool(tool_name, tool_args)
        if tool_name in DANGEROUS_TOOLS:
            confirmed = _confirm_dangerous(tool_name, tool_args)
            if not confirmed:
                return f"[用户取消] 已拒绝执行 {tool_name}"
        return execute_tool(tool_name, tool_args)

    def run(self, user_input: str, on_tool_call=None, on_stream=None) -> str:
        """执行一次对话。

        on_tool_call(tool_name, args, result) — 工具调用时回调
        on_stream(chunk_text) — 流式输出每段文本时回调
        """
        os.chdir(self.working_dir)

        llm = router_get_llm()
        llm_with_tools = llm.bind_tools(self._get_all_tools())

        messages = [SystemMessage(content=SYSTEM_PROMPT)]
        messages.extend(load_history(self.working_dir))
        messages.append(HumanMessage(content=user_input))

        messages = self.context_mgr.trim(messages)

        for _iteration in range(MAX_ITERATIONS):
            response, tool_calls = self._stream_llm(
                llm_with_tools, messages, on_stream,
            )

            messages.append(response)

            if not tool_calls:
                answer = response.content or ""
                if answer:
                    save_turn(self.working_dir, user_input, answer)
                return answer

            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args", {})
                tc_id = tc.get("id", "")

                result = self._execute_tool(tool_name, tool_args)

                if on_tool_call:
                    on_tool_call(tool_name, tool_args, result)

                messages.append(ToolMessage(content=result, tool_call_id=tc_id))

        return "已达到最大执行步数（10步），任务可能未完成。请拆分任务后重试。"

    def _stream_llm(self, llm_with_tools, messages, on_stream):
        """流式调用 LLM，返回 (完整 AIMessage, 解析好的 tool_calls 列表)。

        优先使用 LangChain 内置的 AIMessageChunk 累加后的 tool_calls；
        若失败则回退到 invoke() 非流式调用。
        """
        try:
            full_response = None
            for chunk in llm_with_tools.stream(messages):
                if full_response is None:
                    full_response = chunk
                else:
                    full_response += chunk
                chunk_content = getattr(chunk, "content", "")
                if chunk_content and on_stream:
                    on_stream(chunk_content)

            if full_response is None:
                return AIMessage(content=""), []

            # 优先用 LangChain 累加后的 tool_calls（最可靠）
            tool_calls = getattr(full_response, "tool_calls", None) or []
            if tool_calls:
                return full_response, tool_calls

            return full_response, []

        except Exception:
            # 流式失败时回退到非流式
            response = llm_with_tools.invoke(messages)
            tool_calls = getattr(response, "tool_calls", None) or []
            return response, tool_calls


def run(user_input: str, working_dir: Optional[str] = None, on_tool_call=None, on_stream=None) -> str:
    """无状态单次调用（兼容旧接口）。"""
    wd = working_dir or os.getcwd()
    session = AgentSession(working_dir=wd)
    return session.run(user_input, on_tool_call=on_tool_call, on_stream=on_stream)


def _confirm_dangerous(tool_name: str, args: dict) -> bool:
    """在 CLI 中请求用户确认危险操作"""
    if tool_name == "run_shell":
        desc = f"执行命令: {args.get('command', '')}"
    elif tool_name == "git_commit":
        desc = f"git commit，消息: {args.get('message', '')}"
    elif tool_name == "git_add":
        desc = f"git add 文件: {args.get('files', '')}"
    else:
        desc = str(args)
    console.print(f"\n  [yellow]⚠ Agent 想 {desc}[/yellow]")
    answer = console.input("  允许执行吗？[y/N] ").strip().lower()
    return answer in ("y", "yes")
