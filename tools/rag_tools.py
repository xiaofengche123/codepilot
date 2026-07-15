"""
码搭 CodePilot · RAG 工具
search_semantic / index_project
"""

import os


def search_semantic(query: str, n: int = 10) -> str:
    """
    语义搜索代码库。用自然语言查找功能相关的代码，不需要精确关键词。参数 query: 自然语言查询描述（如"登录验证的逻辑"）、n: 返回结果数（默认 10）。返回相关代码片段和位置。
    """
    from rag.retriever import hybrid_search
    return hybrid_search(query, os.getcwd(), n)


def index_project(force: bool = False) -> str:
    """
    向量化索引当前项目代码，供语义搜索使用。首次使用 search_semantic 前必须调用此工具。参数 force: 是否强制重建索引（默认 false 增量更新）。返回索引摘要。
    """
    from rag.indexer import index_project as do_index
    return do_index(os.getcwd(), force=force)


RAG_TOOLS = {
    "search_semantic": search_semantic,
    "index_project": index_project,
}

RAG_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_semantic",
            "description": "语义搜索代码库。用自然语言描述查找相关代码，不需要精确关键词。适合找某个功能的实现位置。需先运行 index_project。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "自然语言查询，如'用户登录的验证逻辑'、'数据库连接池配置'"},
                    "n": {"type": "integer", "description": "返回结果数，默认 10"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "index_project",
            "description": "向量化索引当前项目代码。索引后才能使用 search_semantic 进行语义搜索。支持增量更新。",
            "parameters": {
                "type": "object",
                "properties": {
                    "force": {"type": "boolean", "description": "是否强制重建索引，默认 false（增量更新）"},
                },
                "required": [],
            },
        },
    },
]

RAG_DANGEROUS_TOOLS: set[str] = set()
