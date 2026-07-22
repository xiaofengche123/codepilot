"""
码搭 CodePilot · RAG 混合检索引擎

使用 ChromaDB 向量召回和 BM25 关键词召回，并通过 RRF 融合两路排名。
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
import math
import re
from typing import Any, Iterable

from rag.indexer import _get_collection, _get_model


_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+|[\u4e00-\u9fff]+")
_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


@dataclass
class SearchHit:
    """结构化检索结果，供格式化输出和离线评估复用。"""

    uid: str
    document: str
    metadata: dict[str, Any]
    score: float = 0.0
    vector_rank: int | None = None
    bm25_rank: int | None = None


@lru_cache(maxsize=4_096)
def tokenize_code(text: str) -> tuple[str, ...]:
    """为代码和中英文注释生成 BM25 token。

    英文标识符按 snake_case/camelCase 边界切分；连续中文同时保留全文、单字和
    二元组，使自然语言查询能匹配代码注释中的局部词语。
    """
    tokens: list[str] = []
    for raw in _TOKEN_PATTERN.findall(text or ""):
        if raw[0].isascii():
            normalized = _ACRONYM_BOUNDARY.sub(r"\1 \2", raw)
            normalized = _CAMEL_BOUNDARY.sub(r"\1 \2", normalized)
            parts = normalized.lower().split()
            tokens.extend(part for part in parts if part)
            continue

        tokens.append(raw)
        if len(raw) > 1:
            tokens.extend(raw)
        if len(raw) > 2:
            tokens.extend(raw[i:i + 2] for i in range(len(raw) - 1))
    return tuple(tokens)


def bm25_rank(
    query: str,
    documents: list[SearchHit],
    limit: int,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[SearchHit]:
    """使用 Okapi BM25 对代码片段排序，不依赖外部搜索服务。"""
    if not documents or limit <= 0:
        return []

    query_tokens = tokenize_code(query)
    if not query_tokens:
        return []

    # 文件路径同样参与关键词召回，便于按模块名、类名或文件名定位代码。
    tokenized_docs = [
        tokenize_code(f"{hit.metadata.get('file', '')} {hit.document}")
        for hit in documents
    ]
    avg_doc_len = sum(map(len, tokenized_docs)) / len(tokenized_docs)
    if avg_doc_len == 0:
        return []

    doc_freq: Counter[str] = Counter()
    for tokens in tokenized_docs:
        doc_freq.update(set(tokens))

    corpus_size = len(documents)
    scores: list[tuple[float, str, SearchHit]] = []
    for hit, tokens in zip(documents, tokenized_docs):
        frequencies = Counter(tokens)
        doc_len = len(tokens)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            frequency_in_docs = doc_freq[token]
            idf = math.log(1 + (corpus_size - frequency_in_docs + 0.5) / (frequency_in_docs + 0.5))
            denominator = frequency + k1 * (1 - b + b * doc_len / avg_doc_len)
            score += idf * frequency * (k1 + 1) / denominator
        if score > 0:
            scores.append((score, hit.uid, hit))

    scores.sort(key=lambda item: (-item[0], item[1]))
    return [
        SearchHit(
            uid=hit.uid,
            document=hit.document,
            metadata=hit.metadata,
            score=score,
            bm25_rank=rank,
        )
        for rank, (score, _, hit) in enumerate(scores[:limit], start=1)
    ]


def reciprocal_rank_fusion(
    vector_hits: Iterable[SearchHit],
    bm25_hits: Iterable[SearchHit],
    limit: int,
    rrf_k: int = 60,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> list[SearchHit]:
    """使用 Reciprocal Rank Fusion 融合并去重两路召回结果。"""
    merged: dict[str, SearchHit] = {}
    scores: Counter[str] = Counter()

    for rank, hit in enumerate(vector_hits, start=1):
        current = merged.setdefault(
            hit.uid,
            SearchHit(hit.uid, hit.document, hit.metadata),
        )
        current.vector_rank = rank
        scores[hit.uid] += vector_weight / (rrf_k + rank)

    for rank, hit in enumerate(bm25_hits, start=1):
        current = merged.setdefault(
            hit.uid,
            SearchHit(hit.uid, hit.document, hit.metadata),
        )
        current.bm25_rank = rank
        scores[hit.uid] += bm25_weight / (rrf_k + rank)

    ranked = sorted(merged.values(), key=lambda hit: (-scores[hit.uid], hit.uid))
    for hit in ranked:
        hit.score = scores[hit.uid]
    return ranked[:limit]


def _collection_documents(collection) -> list[SearchHit]:
    data = collection.get(include=["documents", "metadatas"])
    ids = data.get("ids") or []
    documents = data.get("documents") or []
    metadatas = data.get("metadatas") or []
    return [
        SearchHit(
            uid=uid,
            document=documents[i] if i < len(documents) and documents[i] else "",
            metadata=metadatas[i] if i < len(metadatas) and metadatas[i] else {},
        )
        for i, uid in enumerate(ids)
    ]


def _vector_rank(query: str, collection, limit: int) -> list[SearchHit]:
    count = collection.count()
    if count == 0 or limit <= 0:
        return []

    model = _get_model()
    query_embedding = model.encode([query], show_progress_bar=False).tolist()
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=min(limit, count),
        include=["documents", "metadatas", "distances"],
    )

    ids = (results.get("ids") or [[]])[0]
    documents = (results.get("documents") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]
    hits = []
    for i, uid in enumerate(ids):
        distance = distances[i] if i < len(distances) else 0.0
        hits.append(SearchHit(
            uid=uid,
            document=documents[i] if i < len(documents) and documents[i] else "",
            metadata=metadatas[i] if i < len(metadatas) and metadatas[i] else {},
            score=-float(distance),
            vector_rank=i + 1,
        ))
    return hits


def retrieve(query: str, project_dir: str, n: int = 10, mode: str = "hybrid") -> list[SearchHit]:
    """返回结构化排名结果；mode 支持 vector、bm25、hybrid。"""
    if not query.strip() or n <= 0:
        return []

    collection = _get_collection(project_dir)
    if collection is None:
        raise LookupError("项目尚未索引，请先运行 /index 或调用 index_project")

    from config import config
    multiplier = max(1, int(config.get("rag.candidate_multiplier", 3)))
    candidate_limit = min(max(n * multiplier, n), 100)
    k1 = float(config.get("rag.bm25_k1", 1.5))
    b = float(config.get("rag.bm25_b", 0.75))

    if mode == "vector":
        return _vector_rank(query, collection, n)

    documents = _collection_documents(collection)
    if mode == "bm25":
        return bm25_rank(query, documents, n, k1=k1, b=b)
    if mode != "hybrid":
        raise ValueError(f"不支持的检索模式: {mode}")

    # 两路召回相互独立，并行执行以降低混合检索延迟。
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="rag-retrieval") as executor:
        vector_future = executor.submit(_vector_rank, query, collection, candidate_limit)
        keyword_future = executor.submit(
            bm25_rank, query, documents, candidate_limit, k1, b,
        )
        vector_hits = vector_future.result()
        keyword_hits = keyword_future.result()
    return reciprocal_rank_fusion(
        vector_hits,
        keyword_hits,
        n,
        rrf_k=max(1, int(config.get("rag.rrf_k", 60))),
        vector_weight=float(config.get("rag.vector_weight", 1.0)),
        bm25_weight=float(config.get("rag.bm25_weight", 1.0)),
    )


def _format_hits(query: str, hits: list[SearchHit], title: str) -> str:
    if not hits:
        return f"[未找到] {title}没有匹配 '{query}' 的结果"

    lines = [f"{title}: 找到 {len(hits)} 条相关结果:"]
    for hit in hits:
        file = hit.metadata.get("file", "")
        start = hit.metadata.get("start_line", 0)
        snippet = hit.document[:150].replace("\n", " ")
        ranks = []
        if hit.vector_rank is not None:
            ranks.append(f"向量#{hit.vector_rank}")
        if hit.bm25_rank is not None:
            ranks.append(f"BM25#{hit.bm25_rank}")
        details = []
        if title == "混合检索":
            details.append(f"RRF:{hit.score:.4f}")
        details.extend(ranks)
        source = ", ".join(details) or "未知"
        lines.append(f"  {file}:{start} ({source}) | {snippet}")
    return "\n".join(lines)


def semantic_search(query: str, project_dir: str, n: int = 10) -> str:
    """保留纯向量检索接口，便于对比评估和向后兼容。"""
    try:
        hits = retrieve(query, project_dir, n, mode="vector")
    except LookupError as e:
        return f"[未索引] {e}"
    except Exception as e:
        return f"[错误] 检索失败: {e}"
    return _format_hits(query, hits, "语义检索")


def hybrid_search(query: str, project_dir: str, n: int = 10) -> str:
    """BM25 + ChromaDB 向量召回，经 RRF 融合后的混合检索。"""
    try:
        hits = retrieve(query, project_dir, n, mode="hybrid")
    except LookupError as e:
        return f"[未索引] {e}"
    except Exception as e:
        return f"[错误] 检索失败: {e}"
    return _format_hits(query, hits, "混合检索")
