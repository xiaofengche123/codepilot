"""
码搭 CodePilot · RAG 混合检索引擎

语义向量检索 + 关键词检索，结果去重合并。
"""

from rag.indexer import _get_collection, _get_model


def semantic_search(query: str, project_dir: str, n: int = 10) -> str:
    """向量语义检索 — 找到与 query 意思相近的代码片段。

    参数:
        query: 自然语言查询
        project_dir: 项目根目录
        n: 返回条数（默认 10）

    返回: 格式化搜索结果。
    """
    collection = _get_collection(project_dir)
    if collection is None:
        return "[未索引] 项目尚未索引，请先运行 /index 或调用 index_project"

    model = _get_model()
    query_embedding = model.encode([query], show_progress_bar=False).tolist()

    try:
        results = collection.query(query_embeddings=query_embedding, n_results=min(n, 20))
    except Exception as e:
        return f"[错误] 检索失败: {e}"

    ids = results.get("ids", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    documents = results.get("documents", [[]])[0]
    distances = results.get("distances", [[]])[0]

    if not ids:
        return f"[未找到] 语义检索没有匹配 '{query}' 的结果"

    lines = [f"语义检索: 找到 {len(ids)} 条相关结果:"]
    for i, uid in enumerate(ids):
        meta = metadatas[i] if i < len(metadatas) else {}
        dist = distances[i] if i < len(distances) else 0
        doc = documents[i] if i < len(documents) else ""
        snippet = doc[:150].replace("\n", " ")

        file = meta.get("file", "")
        start = meta.get("start_line", 0)
        relevance = f"{1 - dist:.2f}" if dist else "?"

        lines.append(f"  {file}:{start} (相关度:{relevance}) | {snippet}")

    return "\n".join(lines)


def hybrid_search(query: str, project_dir: str, n: int = 10) -> str:
    """混合检索：语义检索 + 关键词检索合并去重。

    参数:
        query: 自然语言查询
        project_dir: 项目根目录
        n: 每路返回条数（默认 10）

    返回: 格式化搜索结果。
    """
    from tools.core_tools import search_code

    # 并行获取两路结果
    semantic_result = semantic_search(query, project_dir, n)
    keyword_result = search_code(query, project_dir)

    # 如果语义检索失败或未索引，只返回关键词结果
    if "[未索引]" in semantic_result:
        keyword_result = keyword_result.replace("关键词", "")
        return "[注意] 项目尚未索引，仅展示关键词匹配结果。语义检索需先运行 /index。\n\n" + keyword_result

    # 合并：先语义、后关键词
    parts = [semantic_result]
    if "[未找到]" not in keyword_result:
        parts.append("\n--- 关键词匹配 ---")
        parts.append(keyword_result)

    return "\n".join(parts)
