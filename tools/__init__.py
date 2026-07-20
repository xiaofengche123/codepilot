"""
码搭 CodePilot · 工具注册表
合并所有工具模块的注册表并暴露统一接口。
"""

import inspect

from tools.core_tools import (
    CORE_TOOLS, CORE_TOOL_DEFINITIONS, CORE_DANGEROUS_TOOLS,
)
from tools.git_tools import (
    GIT_TOOLS, GIT_TOOL_DEFINITIONS, GIT_DANGEROUS_TOOLS,
)
from tools.web_tools import (
    WEB_TOOLS, WEB_TOOL_DEFINITIONS, WEB_DANGEROUS_TOOLS,
)
from tools.rag_tools import (
    RAG_TOOLS, RAG_TOOL_DEFINITIONS, RAG_DANGEROUS_TOOLS,
)

TOOLS_REGISTRY = {**CORE_TOOLS, **GIT_TOOLS, **WEB_TOOLS, **RAG_TOOLS}
TOOL_DEFINITIONS = CORE_TOOL_DEFINITIONS + GIT_TOOL_DEFINITIONS + WEB_TOOL_DEFINITIONS + RAG_TOOL_DEFINITIONS
DANGEROUS_TOOLS: set[str] = CORE_DANGEROUS_TOOLS | GIT_DANGEROUS_TOOLS | WEB_DANGEROUS_TOOLS | RAG_DANGEROUS_TOOLS


def _accepts_workdir(func) -> bool:
    """判断工具函数是否声明了 workdir 注入参数（web 类工具没有）。"""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    return "workdir" in params or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


_ACCEPTS_WORKDIR = {name: _accepts_workdir(f) for name, f in TOOLS_REGISTRY.items()}


def execute_tool(name: str, args: dict, workdir: str = None) -> str:
    """执行指定工具并返回结果。

    workdir: 工具的工作目录（由 AgentSession 注入），相对路径参数基于它解析；
             None 时退回进程当前目录（CLI/MCP 场景）。
    """
    if name not in TOOLS_REGISTRY:
        return f"[错误] 未知工具: {name}，可用工具: {', '.join(TOOLS_REGISTRY.keys())}"

    func = TOOLS_REGISTRY[name]
    try:
        # workdir 是内部注入参数，不允许 LLM 侧传入覆盖
        clean_args = {k: v for k, v in args.items() if v is not None and k != "workdir"}
        if workdir is not None and _ACCEPTS_WORKDIR[name]:
            return func(workdir=workdir, **clean_args)
        return func(**clean_args)
    except TypeError as e:
        return f"[错误] 参数错误: {e}"
    except Exception as e:
        return f"[错误] 工具执行异常: {e}"
