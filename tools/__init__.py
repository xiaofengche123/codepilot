"""
码搭 CodePilot · 工具注册表
合并所有工具模块的注册表并暴露统一接口。
"""

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


def execute_tool(name: str, args: dict) -> str:
    """执行指定工具并返回结果"""
    if name not in TOOLS_REGISTRY:
        return f"[错误] 未知工具: {name}，可用工具: {', '.join(TOOLS_REGISTRY.keys())}"

    func = TOOLS_REGISTRY[name]
    try:
        clean_args = {k: v for k, v in args.items() if v is not None}
        return func(**clean_args)
    except TypeError as e:
        return f"[错误] 参数错误: {e}"
    except Exception as e:
        return f"[错误] 工具执行异常: {e}"
