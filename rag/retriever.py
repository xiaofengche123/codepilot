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
import warnings
from typing import Any, Iterable

from rag.indexer import _get_collection, _get_model
from rag.query_features import QueryFeatures, extract_query_features
from rag.retrieval_confidence import calculate_retrieval_confidence
from rag.retrieval_plan import RetrievalPlan
from rag.retrieval_router import route_retrieval


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
    rrf_rank: int | None = None
    rerank_score: float | None = None


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
    for rank, hit in enumerate(ranked, start=1):
        hit.score = scores[hit.uid]
        hit.rrf_rank = rank
    return ranked[:limit]


def _content_filter(include_docs: bool) -> dict | None:
    """代码搜索默认排除说明文档，避免 README 等文字压过真实实现。"""
    return None if include_docs else {"content_type": "code"}


def _collection_documents(collection, include_docs: bool = False) -> list[SearchHit]:
    kwargs = {"include": ["documents", "metadatas"]}
    where = _content_filter(include_docs)
    if where:
        kwargs["where"] = where
    data = collection.get(**kwargs)
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


def _vector_rank(
    query: str,
    collection,
    limit: int,
    include_docs: bool = False,
) -> list[SearchHit]:
    count = collection.count()
    if count == 0 or limit <= 0:
        return []

    model = _get_model()
    query_embedding = model.encode([query], show_progress_bar=False).tolist()
    query_kwargs = dict(
        query_embeddings=query_embedding,
        n_results=min(limit, count),
        include=["documents", "metadatas", "distances"],
    )
    where = _content_filter(include_docs)
    if where:
        query_kwargs["where"] = where
    results = collection.query(**query_kwargs)

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


def _dual_rankings(
    query: str,
    collection,
    documents: list[SearchHit],
    recall_limit: int,
    include_docs: bool,
    k1: float,
    b: float,
) -> tuple[list[SearchHit], list[SearchHit]]:
    """并行执行两路召回，供固定与自适应融合复用。"""
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="rag-retrieval") as executor:
        vector_future = executor.submit(
            _vector_rank, query, collection, recall_limit, include_docs,
        )
        keyword_future = executor.submit(
            bm25_rank, query, documents, recall_limit, k1, b,
        )
        vector_hits = vector_future.result()
        keyword_hits = keyword_future.result()
    return vector_hits, keyword_hits


def _fixed_fusion(
    vector_hits: list[SearchHit],
    keyword_hits: list[SearchHit],
    limit: int,
) -> list[SearchHit]:
    from config import config

    return reciprocal_rank_fusion(
        vector_hits,
        keyword_hits,
        limit,
        rrf_k=max(1, int(config.get("rag.rrf_k", 60))),
        vector_weight=float(config.get("rag.vector_weight", 1.0)),
        bm25_weight=float(config.get("rag.bm25_weight", 1.0)),
    )


def _route_family(plan: RetrievalPlan) -> str:
    if "ranking_disagreement" in plan.reason_codes:
        return "ranking_disagreement"
    return next(
        (
            code.removeprefix("query_")
            for code in plan.reason_codes
            if code.startswith("query_")
        ),
        "baseline",
    )


def _annotate_adaptive_hits(
    hits: list[SearchHit], plan: RetrievalPlan
) -> list[SearchHit]:
    family = _route_family(plan)
    for hit in hits:
        hit.metadata = dict(hit.metadata)
        hit.metadata.update({
            "adaptive_routing": True,
            "retrieval_router_version": plan.reason_codes[0],
            "retrieval_route_family": family,
            "retrieval_reason_codes": ",".join(plan.reason_codes),
            "retrieval_bm25_weight": plan.bm25_weight,
            "retrieval_vector_weight": plan.vector_weight,
            "retrieval_rrf_k": plan.rrf_k,
            "retrieval_candidate_count": plan.candidate_count,
        })
    return hits


def _hybrid_candidates(
    query: str,
    collection,
    documents: list[SearchHit],
    limit: int,
    include_docs: bool,
    k1: float,
    b: float,
    features: QueryFeatures | None = None,
) -> list[SearchHit]:
    """返回固定 RRF，或在显式启用时执行冻结自适应计划。"""
    from config import config

    multiplier = max(1, int(config.get("rag.candidate_multiplier", 3)))
    recall_limit = min(max(limit * multiplier, limit), 100)
    if features is not None:
        from rag.retrieval_router import (
            CROSS_MODULE_CANDIDATE_COUNT,
            DEFAULT_CANDIDATE_COUNT,
            DISAGREEMENT_CANDIDATE_COUNT,
            MIXED_CANDIDATE_COUNT,
        )

        recall_limit = max(
            recall_limit,
            DEFAULT_CANDIDATE_COUNT,
            MIXED_CANDIDATE_COUNT,
            CROSS_MODULE_CANDIDATE_COUNT,
            DISAGREEMENT_CANDIDATE_COUNT,
        )
    vector_hits, keyword_hits = _dual_rankings(
        query, collection, documents, recall_limit, include_docs, k1, b
    )
    if features is None:
        return _fixed_fusion(vector_hits, keyword_hits, limit)

    try:
        confidence = calculate_retrieval_confidence(
            query, vector_hits, keyword_hits, top_k=10
        )
        plan = route_retrieval(features, confidence)
        hits = reciprocal_rank_fusion(
            vector_hits[:plan.candidate_count],
            keyword_hits[:plan.candidate_count],
            limit,
            rrf_k=plan.rrf_k,
            vector_weight=plan.vector_weight,
            bm25_weight=plan.bm25_weight,
        )
        return _annotate_adaptive_hits(hits, plan)
    except Exception as exc:
        if not bool(config.get("rag.adaptive_routing.fallback_on_error", True)):
            raise
        warnings.warn(
            f"Adaptive retrieval routing failed; falling back to fixed RRF: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        fallback = _fixed_fusion(vector_hits, keyword_hits, limit)
        for hit in fallback:
            hit.metadata = dict(hit.metadata)
            hit.metadata["adaptive_routing_fallback"] = True
        return fallback


def retrieve(query: str, project_dir: str, n: int = 10, mode: str = "hybrid") -> list[SearchHit]:
    """返回结构化排名结果；支持 vector、bm25、hybrid、rerank。"""
    if not query.strip() or n <= 0:
        return []

    collection = _get_collection(project_dir)
    if collection is None:
        raise LookupError("项目尚未索引，请先运行 /index 或调用 index_project")

    from config import config
    k1 = float(config.get("rag.bm25_k1", 1.5))
    b = float(config.get("rag.bm25_b", 0.75))
    include_docs = bool(config.get("rag.include_docs", False))

    if mode == "vector":
        return _vector_rank(query, collection, n, include_docs)

    if mode == "bm25":
        documents = _collection_documents(collection, include_docs)
        return bm25_rank(query, documents, n, k1=k1, b=b)
    if mode not in {"hybrid", "rerank"}:
        raise ValueError(f"不支持的检索模式: {mode}")

    adaptive_features = None
    if bool(config.get("rag.adaptive_routing.enabled", False)):
        try:
            adaptive_features = extract_query_features(query)
            # include_docs is decided solely from features, not ranking results.
            adaptive_include_docs = route_retrieval(
                adaptive_features, None
            ).include_docs
            if adaptive_include_docs and not include_docs:
                include_docs = True
        except Exception as exc:
            if not bool(config.get("rag.adaptive_routing.fallback_on_error", True)):
                raise
            warnings.warn(
                "Adaptive retrieval feature routing failed; using fixed RRF: "
                f"{exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            adaptive_features = None

    documents = _collection_documents(collection, include_docs)

    if mode == "hybrid":
        return _hybrid_candidates(
            query, collection, documents, n, include_docs, k1, b,
            adaptive_features,
        )

    candidate_count = min(
        100,
        max(n, int(config.get("rag.reranker.candidate_count", 30))),
    )
    candidates = _hybrid_candidates(
        query, collection, documents, candidate_count, include_docs, k1, b,
        adaptive_features,
    )
    try:
        from rag.reranker import rerank

        return rerank(query, candidates, n)
    except Exception as exc:
        if not bool(config.get("rag.reranker.fallback_on_error", True)):
            raise
        warnings.warn(
            f"Cross-Encoder rerank failed; falling back to RRF: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        fallback = candidates[:n]
        for hit in fallback:
            hit.metadata = dict(hit.metadata)
            hit.metadata["rerank_fallback"] = True
        return fallback


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
        if hit.rerank_score is not None:
            details.append(f"Rerank:{hit.rerank_score:.4f}")
            details.append(f"RRF#{hit.rrf_rank}")
        elif title in {"混合检索", "精排检索"}:
            details.append(f"RRF:{hit.score:.4f}")
            if hit.metadata.get("rerank_fallback"):
                details.append("Rerank回退")
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
    """BM25 + 向量 + RRF，并按配置使用 Cross-Encoder 精排。"""
    try:
        from config import config

        rerank_enabled = bool(config.get("rag.reranker.enabled", True))
        mode = "rerank" if rerank_enabled else "hybrid"
        hits = retrieve(query, project_dir, n, mode=mode)
    except LookupError as e:
        return f"[未索引] {e}"
    except Exception as e:
        return f"[错误] 检索失败: {e}"
    return _format_hits(query, hits, "精排检索" if rerank_enabled else "混合检索")
