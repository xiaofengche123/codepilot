"""RAG 检索离线评估：对比纯向量检索与 BM25 + RRF 混合检索。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from rag.retriever import SearchHit, retrieve


def _matches(hit: SearchHit, relevant: set[str]) -> bool:
    """相关项可填写精确 chunk id，也可填写文件相对路径。"""
    return hit.uid in relevant or hit.metadata.get("file") in relevant


def calculate_metrics(hits: Iterable[SearchHit], relevant: set[str]) -> tuple[float, float]:
    """返回单条查询的 Recall@K 和 reciprocal rank。"""
    ranked = list(hits)
    matched = {
        rel
        for rel in relevant
        if any(hit.uid == rel or hit.metadata.get("file") == rel for hit in ranked)
    }
    recall = len(matched) / len(relevant) if relevant else 0.0
    reciprocal_rank = next(
        (1.0 / rank for rank, hit in enumerate(ranked, start=1) if _matches(hit, relevant)),
        0.0,
    )
    return recall, reciprocal_rank


def evaluate(dataset: list[dict], project_dir: str, k: int) -> dict[str, dict[str, float]]:
    """在同一标注集上评估 vector 与 hybrid，返回平均 Recall@K/MRR。"""
    totals = {
        "vector": {"recall": 0.0, "mrr": 0.0},
        "hybrid": {"recall": 0.0, "mrr": 0.0},
    }
    valid_queries = 0

    for item in dataset:
        query = str(item.get("query", "")).strip()
        relevant = {str(value) for value in item.get("relevant", []) if str(value).strip()}
        if not query or not relevant:
            continue
        valid_queries += 1
        for mode in totals:
            hits = retrieve(query, project_dir, k, mode=mode)
            recall, reciprocal_rank = calculate_metrics(hits, relevant)
            totals[mode]["recall"] += recall
            totals[mode]["mrr"] += reciprocal_rank

    if not valid_queries:
        raise ValueError("评估集没有有效数据；每项都需要 query 和非空 relevant")

    for metrics in totals.values():
        metrics["recall"] /= valid_queries
        metrics["mrr"] /= valid_queries
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description="对比 CodePilot 纯向量与混合检索效果")
    parser.add_argument("dataset", help="JSON 评估集路径")
    parser.add_argument("--project", default=".", help="已建立索引的项目目录")
    parser.add_argument("-k", type=int, default=10, help="Recall@K 中的 K")
    args = parser.parse_args()

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    results = evaluate(dataset, args.project, args.k)
    print(f"method\tRecall@{args.k}\tMRR")
    for method, metrics in results.items():
        print(f"{method}\t{metrics['recall']:.4f}\t\t{metrics['mrr']:.4f}")


if __name__ == "__main__":
    main()
