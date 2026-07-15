"""
码搭 CodePilot · Agent 核心循环

ReAct 模式（推理-行动-观察循环）：
  用户输入 → LLM 思考 → 决定调工具或回答
  调工具 → 执行 → 结果反馈 → LLM 再思考 → ... → 最终回答
"""

import os
from typing import Optional

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from rich.console import Console

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

MAX_ITERATIONS = 10


class AgentSession:
    """有状态的 Agent 会话，维护跨轮对话连续性。"""

    def __init__(self, working_dir: str, max_context_tokens: int = 8000):
        self.working_dir = os.path.abspath(working_dir)
        self.context_mgr = ContextManager(max_tokens=max_context_tokens)

    def run(self, user_input: str, on_tool_call=None) -> str:
        os.chdir(self.working_dir)

        llm = router_get_llm()
        llm_with_tools = llm.bind_tools(TOOL_DEFINITIONS)

        # 构建消息：System + 历史 + 当前输入
        messages = [SystemMessage(content=SYSTEM_PROMPT)]
        messages.extend(load_history(self.working_dir))
        messages.append(HumanMessage(content=user_input))

        # Token 预算控制
        messages = self.context_mgr.trim(messages)

        for _iteration in range(MAX_ITERATIONS):
            response = llm_with_tools.invoke(messages)
            messages.append(response)

            tool_calls = getattr(response, "tool_calls", None) or []

            if not tool_calls:
                answer = response.content or ""
                save_turn(self.working_dir, user_input, answer)
                return answer

            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = tc.get("args", {})

                if tool_name in DANGEROUS_TOOLS:
                    confirmed = _confirm_dangerous(tool_name, tool_args)
                    if not confirmed:
                        result = f"[用户取消] 已拒绝执行 {tool_name}"
                    else:
                        result = execute_tool(tool_name, tool_args)
                else:
                    result = execute_tool(tool_name, tool_args)

                if on_tool_call:
                    on_tool_call(tool_name, tool_args, result)

                messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

        return "已达到最大执行步数（10步），任务可能未完成。请拆分任务后重试。"


def run(user_input: str, working_dir: Optional[str] = None, on_tool_call=None) -> str:
    """无状态单次调用（兼容旧接口）。"""
    wd = working_dir or os.getcwd()
    session = AgentSession(working_dir=wd)
    return session.run(user_input, on_tool_call)


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
