"""RAG 检索离线评估：对比 BM25、纯向量与 RRF 混合检索。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import statistics
import time
from typing import Iterable

from rag.retriever import SearchHit, retrieve


_CHUNK_LABEL = re.compile(r"^(.*):(\d+)-(\d+)$")


def _normal_path(value: object) -> str:
    return str(value or "").replace("\\", "/")


def _matches_label(hit: SearchHit, relevant: str) -> bool:
    """匹配文件或 chunk 标签，并容忍代码插行造成的小范围行号漂移。"""
    label = _normal_path(relevant)
    hit_file = _normal_path(hit.metadata.get("file"))
    if label == _normal_path(hit.uid) or label == hit_file:
        return True

    parsed = _CHUNK_LABEL.fullmatch(label)
    if not parsed or _normal_path(parsed.group(1)) != hit_file:
        return False
    hit_start = hit.metadata.get("start_line")
    hit_end = hit.metadata.get("end_line")
    if not isinstance(hit_start, int) or not isinstance(hit_end, int):
        return False

    label_start, label_end = int(parsed.group(2)), int(parsed.group(3))
    overlap = max(0, min(label_end, hit_end) - max(label_start, hit_start) + 1)
    shorter = min(label_end - label_start + 1, hit_end - hit_start + 1)
    return shorter > 0 and overlap / shorter >= 0.5


def _matches(hit: SearchHit, relevant: set[str]) -> bool:
    """相关项可填写 chunk 行号区间，也可填写文件相对路径。"""
    return any(_matches_label(hit, rel) for rel in relevant)


def calculate_metrics(hits: Iterable[SearchHit], relevant: set[str]) -> tuple[float, float]:
    """返回单条查询的 Recall@K 和 reciprocal rank。"""
    ranked = list(hits)
    matched = {
        rel
        for rel in relevant
        if any(_matches_label(hit, rel) for hit in ranked)
    }
    recall = len(matched) / len(relevant) if relevant else 0.0
    reciprocal_rank = next(
        (1.0 / rank for rank, hit in enumerate(ranked, start=1) if _matches(hit, relevant)),
        0.0,
    )
    return recall, reciprocal_rank


def evaluate(dataset: list[dict], project_dir: str, k: int) -> dict[str, dict[str, float]]:
    """在同一标注集上评估三种检索，返回质量与进程内查询延迟。"""
    valid_items = []
    for item in dataset:
        query = str(item.get("query", "")).strip()
        relevant = {
            str(value) for value in item.get("relevant", []) if str(value).strip()
        }
        if query and relevant:
            valid_items.append((query, relevant))
    if not valid_items:
        raise ValueError("评估集没有有效数据；每项都需要 query 和非空 relevant")

    totals = {
        mode: {
            "recall": 0.0,
            "mrr": 0.0,
            "latencies_ms": [],
            "fallback_queries": 0,
        }
        for mode in ("bm25", "vector", "hybrid", "rerank")
    }

    # 质量指标不受预热影响；延迟统计排除模型/Chroma 首次初始化成本。
    # 冷启动耗时应在独立进程级基准中单独记录。
    warmup_query = valid_items[0][0]
    for mode in totals:
        retrieve(warmup_query, project_dir, 1, mode=mode)

    for query, relevant in valid_items:
        for mode in totals:
            started = time.perf_counter()
            hits = retrieve(query, project_dir, k, mode=mode)
            totals[mode]["latencies_ms"].append(
                (time.perf_counter() - started) * 1000
            )
            recall, reciprocal_rank = calculate_metrics(hits, relevant)
            if mode == "rerank" and any(
                hit.metadata.get("rerank_fallback") for hit in hits
            ):
                totals[mode]["fallback_queries"] += 1
            totals[mode]["recall"] += recall
            totals[mode]["mrr"] += reciprocal_rank

    for metrics in totals.values():
        metrics["recall"] /= len(valid_items)
        metrics["mrr"] /= len(valid_items)
        latencies = metrics.pop("latencies_ms")
        metrics["avg_ms"] = statistics.fmean(latencies)
        ordered = sorted(latencies)
        metrics["p95_ms"] = ordered[max(0, int(len(ordered) * 0.95) - 1)]
        metrics["fallback_rate"] = metrics.pop("fallback_queries") / len(valid_items)
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description="对比 CodePilot 三种检索模式")
    parser.add_argument("dataset", help="JSON 评估集路径")
    parser.add_argument("--project", default=".", help="已建立索引的项目目录")
    parser.add_argument("-k", type=int, default=10, help="Recall@K 中的 K")
    args = parser.parse_args()

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    results = evaluate(dataset, args.project, args.k)
    print(f"method\tRecall@{args.k}\tMRR\tAvg(ms)\tP95(ms)\tFallback")
    for method, metrics in results.items():
        print(
            f"{method}\t{metrics['recall']:.4f}\t\t{metrics['mrr']:.4f}"
            f"\t{metrics['avg_ms']:.1f}\t\t{metrics['p95_ms']:.1f}"
            f"\t\t{metrics['fallback_rate']:.2%}"
        )


if __name__ == "__main__":
    main()
