"""
码搭 CodePilot · Agent 核心循环

ReAct 模式（推理-行动-观察循环）：
  用户输入 → LLM 思考 → 决定调工具或回答
  调工具 → 执行 → 结果反馈 → LLM 再思考 → ... → 最终回答
"""

import json
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)

from tools import TOOL_DEFINITIONS, execute_tool

# ============================================================
# 系统提示词 — 定义 Agent 的行为边界和风格
# ============================================================

SYSTEM_PROMPT = """你是「码搭」，一个智能编程助手 Agent。

你有以下能力：
- 读写文件、列出目录、搜索代码
- 执行终端命令（危险命令会被自动拦截）
- 回答编程问题、解释代码、调试错误

工作原则：
1. 做任何操作前，先观察（list_files、search_code、read_file）
2. 代码修改要精准，用 search_code 找到目标位置再改
3. 执行 shell 命令前，先读一下当前环境确认安全
4. 回答要简洁、直接、给代码示例
5. 如果用户的问题和你的工具无关，直接文字回答即可"""


# ============================================================
# 模型工厂 — 多模型路由的底座
# ============================================================

def _create_default_llm():
    """根据 .env 配置创建 LLM 客户端，优先级：Claude > DeepSeek > OpenAI"""
    import os
    from dotenv import load_dotenv
    load_dotenv()

    claude_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    if claude_key and "xxx" not in claude_key:
        base = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        return ChatAnthropic(
            model=os.getenv("DEFAULT_MODEL", "claude-sonnet-4-6"),
            api_key=claude_key,
            base_url=base,
            temperature=0.3,
            max_tokens=4096,
        )

    if deepseek_key and "xxx" not in deepseek_key:
        return ChatOpenAI(
            model="deepseek-chat",
            api_key=deepseek_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            temperature=0.3,
            max_tokens=4096,
        )

    if openai_key and "xxx" not in openai_key:
        return ChatOpenAI(
            model="gpt-4o",
            api_key=openai_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com"),
            temperature=0.3,
            max_tokens=4096,
        )

    raise RuntimeError(
        "未找到有效的 API Key，请在 .env 中配置 ANTHROPIC_API_KEY、"
        "DEEPSEEK_API_KEY 或 OPENAI_API_KEY"
    )


# 全局 LLM 实例（单例，避免重复初始化）
_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        _llm = _create_default_llm()
    return _llm


# ============================================================
# Agent 主循环
# ============================================================

MAX_ITERATIONS = 10  # 防止无限循环


def run(user_input: str, working_dir: Optional[str] = None, on_tool_call=None) -> str:
    """
    执行一次 Agent 对话。

    参数:
        user_input: 用户输入的自然语言指令
        working_dir: 工作目录（Agent 操作文件的根目录）
        on_tool_call: 回调函数，每执行一个工具后调用。签名: (tool_name, args, result) -> None
                      用于 CLI 层实时打印工具调用过程。

    返回:
        Agent 的最终文字回复
    """
    import os
    if working_dir:
        os.chdir(working_dir)

    llm = _get_llm()
    # 绑定工具，LLM 会自动在需要时返回 tool_call
    llm_with_tools = llm.bind_tools(TOOL_DEFINITIONS)

    # 消息历史：system prompt + 用户输入 + 对话过程
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_input),
    ]

    for iteration in range(MAX_ITERATIONS):
        # 调用 LLM
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        # 检查是否有工具调用请求
        tool_calls = getattr(response, "tool_calls", None) or []

        if not tool_calls:
            # 没有工具调用 → LLM 给出最终回答
            return response.content or ""

        # 执行工具调用
        for tc in tool_calls:
            tool_name = tc["name"]
            tool_args = tc.get("args", {})

            # 安全确认：run_shell 需要用户确认
            if tool_name == "run_shell":
                confirmed = _confirm_dangerous(tool_args.get("command", ""))
                if not confirmed:
                    result = "[用户取消] 已拒绝执行此命令"
                else:
                    result = execute_tool(tool_name, tool_args)
            else:
                result = execute_tool(tool_name, tool_args)

            # 通知 CLI 层
            if on_tool_call:
                on_tool_call(tool_name, tool_args, result)

            # 把工具执行结果追加到消息历史
            messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

    return "已达到最大执行步数（10步），任务可能未完成。请拆分任务后重试。"


def _confirm_dangerous(command: str) -> bool:
    """在 CLI 中请求用户确认 shell 命令"""
    print(f"\n  ⚠ Agent 想执行命令: \033[93m{command}\033[0m")
    answer = input("  允许执行吗？[y/N] ").strip().lower()
    return answer in ("y", "yes")
