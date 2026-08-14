"""可复现的 RAG 离线评测：分层相关性、分类指标、延迟与置信区间。"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import time
from typing import Iterable

from rag.retriever import SearchHit, retrieve


_CHUNK_LABEL = __import__("re").compile(r"^(.*):(\d+)-(\d+)$")
DEFAULT_MODES = ("bm25", "vector", "hybrid", "rerank")


def _normal_path(value: object) -> str:
    return str(value or "").replace("\\", "/")


def _matches_label(hit: SearchHit, relevant: str) -> bool:
    """匹配文件或 chunk 标签，并容忍插行导致的主要区间重叠。"""
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
    return any(_matches_label(hit, rel) for rel in relevant)


def calculate_metrics(
    hits: Iterable[SearchHit], relevant: set[str]
) -> tuple[float, float]:
    """兼容旧数据格式，返回单条查询的 Recall@K 和 reciprocal rank。"""
    ranked = list(hits)
    matched = {
        rel for rel in relevant if any(_matches_label(hit, rel) for hit in ranked)
    }
    recall = len(matched) / len(relevant) if relevant else 0.0
    reciprocal_rank = next(
        (1.0 / rank for rank, hit in enumerate(ranked, 1) if _matches(hit, relevant)),
        0.0,
    )
    return recall, reciprocal_rank


def _labels(item: dict) -> tuple[set[str], set[str]]:
    """读取 required/supporting；旧 relevant 数据等价于 required。"""
    required_values = item.get("required", item.get("relevant", []))
    required = {str(value) for value in required_values if str(value).strip()}
    supporting = {
        str(value) for value in item.get("supporting", []) if str(value).strip()
    }
    return required, supporting - required


def _query_metrics(
    hits: list[SearchHit], required: set[str], supporting: set[str]
) -> dict[str, float | list[str]]:
    matched_required = {
        label for label in required if any(_matches_label(hit, label) for hit in hits)
    }
    required_recall = len(matched_required) / len(required)
    reciprocal_rank = next(
        (1.0 / rank for rank, hit in enumerate(hits, 1) if _matches(hit, required)),
        0.0,
    )

    # required=2、supporting=1；每个标注最多贡献一次，避免重叠 chunk 重复计分。
    remaining = {**{label: 2 for label in required}, **{label: 1 for label in supporting}}
    gains = []
    for hit in hits:
        matching = [label for label in remaining if _matches_label(hit, label)]
        grade = max((remaining[label] for label in matching), default=0)
        gains.append(grade)
        for label in matching:
            remaining.pop(label, None)
    dcg = sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(gains, 1))
    ideal = sorted([2] * len(required) + [1] * len(supporting), reverse=True)[:len(hits)]
    idcg = sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal, 1))
    return {
        "required_recall": required_recall,
        "mrr": reciprocal_rank,
        "graded_ndcg": dcg / idcg if idcg else 0.0,
        "missing_required": sorted(required - matched_required),
    }


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _bootstrap_ci(
    values: list[float], *, seed: int = 20260814, samples: int = 2000
) -> list[float]:
    """固定随机种子的 percentile bootstrap 95% CI。"""
    if not values:
        return [0.0, 0.0]
    rng = random.Random(seed)
    n = len(values)
    estimates = sorted(
        statistics.fmean(values[rng.randrange(n)] for _ in range(n))
        for _ in range(samples)
    )
    return [estimates[int(samples * 0.025)], estimates[int(samples * 0.975)]]


def _aggregate(rows: list[dict], ks: list[int]) -> dict:
    result: dict[str, object] = {"queries": len(rows)}
    for k in ks:
        for metric in ("required_recall", "mrr", "graded_ndcg"):
            values = [float(row["at_k"][str(k)][metric]) for row in rows]
            result[f"{metric}@{k}"] = _mean(values)
            result[f"{metric}@{k}_ci95"] = _bootstrap_ci(values, seed=20260814 + k)
    return result


def _git_head(project_dir: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_dir, capture_output=True,
            text=True, timeout=10, check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def evaluate(
    dataset: list[dict],
    project_dir: str,
    k: int | None = None,
    *,
    ks: Iterable[int] | None = None,
    modes: Iterable[str] = DEFAULT_MODES,
) -> dict:
    """一次检索覆盖多个 K，返回逐题、分类和总体结果。"""
    selected_ks = sorted({int(value) for value in (ks or ([k] if k else [5, 10]))})
    if not selected_ks or selected_ks[0] <= 0:
        raise ValueError("K 必须为正整数")
    selected_modes = tuple(modes)
    valid_items = []
    for item in dataset:
        query = str(item.get("query", "")).strip()
        required, supporting = _labels(item)
        if query and required:
            valid_items.append((item, query, required, supporting))
    if not valid_items:
        raise ValueError("评估集没有有效数据；每项需要 query 和非空 required/relevant")

    max_k = max(selected_ks)
    warmup_query = valid_items[0][1]
    for mode in selected_modes:
        retrieve(warmup_query, project_dir, 1, mode=mode)

    mode_rows: dict[str, list[dict]] = {mode: [] for mode in selected_modes}
    latencies: dict[str, list[float]] = {mode: [] for mode in selected_modes}
    fallback_counts: Counter[str] = Counter()
    for item, query, required, supporting in valid_items:
        for mode in selected_modes:
            started = time.perf_counter()
            hits = retrieve(query, project_dir, max_k, mode=mode)
            latency_ms = (time.perf_counter() - started) * 1000
            latencies[mode].append(latency_ms)
            fallback = any(hit.metadata.get("rerank_fallback") for hit in hits)
            fallback_counts[mode] += int(fallback)
            at_k = {
                str(value): _query_metrics(hits[:value], required, supporting)
                for value in selected_ks
            }
            mode_rows[mode].append({
                "id": item.get("id"),
                "category": item.get("category", "uncategorized"),
                "query": query,
                "latency_ms": latency_ms,
                "fallback": fallback,
                "at_k": at_k,
                "top_results": [hit.uid for hit in hits],
            })

    results = {}
    for mode in selected_modes:
        rows = mode_rows[mode]
        by_category: defaultdict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_category[str(row["category"])].append(row)
        latency_values = latencies[mode]
        results[mode] = {
            "overall": _aggregate(rows, selected_ks),
            "by_category": {
                category: _aggregate(category_rows, selected_ks)
                for category, category_rows in sorted(by_category.items())
            },
            "latency_ms": {
                "avg": _mean(latency_values),
                "p50": _percentile(latency_values, 0.50),
                "p95": _percentile(latency_values, 0.95),
                "max": max(latency_values),
            },
            "fallback_rate": fallback_counts[mode] / len(rows),
            "failures": [
                row for row in rows
                if row["at_k"][str(max_k)]["required_recall"] < 1.0
            ],
            "per_query": rows,
        }

    paired_differences = {}
    if "hybrid" in mode_rows:
        baseline = mode_rows["hybrid"]
        for mode, rows in mode_rows.items():
            if mode == "hybrid":
                continue
            paired_differences[mode] = {}
            for value in selected_ks:
                for metric in ("required_recall", "mrr", "graded_ndcg"):
                    differences = [
                        float(row["at_k"][str(value)][metric])
                        - float(base["at_k"][str(value)][metric])
                        for row, base in zip(rows, baseline)
                    ]
                    key = f"{metric}@{value}"
                    paired_differences[mode][key] = {
                        "mean": _mean(differences),
                        "ci95": _bootstrap_ci(differences, seed=20260814 + value),
                        "improved": sum(diff > 0 for diff in differences),
                        "degraded": sum(diff < 0 for diff in differences),
                        "tied": sum(diff == 0 for diff in differences),
                    }

    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(project_dir),
        "query_count": len(valid_items),
        "ks": selected_ks,
        "results": results,
        "paired_vs_hybrid": paired_differences,
    }


def _print_summary(report: dict) -> None:
    ks = report["ks"]
    columns = ["method"]
    for k in ks:
        columns.extend([f"ReqR@{k}", f"MRR@{k}", f"nDCG@{k}"])
    columns.extend(["Avg(ms)", "P95(ms)", "Max(ms)", "Fallback"])
    print("\t".join(columns))
    for mode, result in report["results"].items():
        overall = result["overall"]
        values = [mode]
        for k in ks:
            values.extend([
                f"{overall[f'required_recall@{k}']:.4f}",
                f"{overall[f'mrr@{k}']:.4f}",
                f"{overall[f'graded_ndcg@{k}']:.4f}",
            ])
        latency = result["latency_ms"]
        values.extend([
            f"{latency['avg']:.1f}", f"{latency['p95']:.1f}",
            f"{latency['max']:.1f}", f"{result['fallback_rate']:.2%}",
        ])
        print("\t".join(values))


def main() -> None:
    parser = argparse.ArgumentParser(description="评估 CodePilot 四种检索模式")
    parser.add_argument("dataset", help="JSON 评估集路径")
    parser.add_argument("--project", default=".", help="已建立索引的项目目录")
    parser.add_argument("-k", type=int, help="兼容单个 K；推荐使用 --ks")
    parser.add_argument("--ks", type=int, nargs="+", default=[5, 10])
    parser.add_argument("--output", help="保存完整 JSON 报告")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    raw = dataset_path.read_bytes()
    dataset = json.loads(raw.decode("utf-8"))
    selected_ks = [args.k] if args.k else args.ks
    report = evaluate(dataset, args.project, ks=selected_ks)
    report["dataset"] = str(dataset_path)
    report["dataset_sha256"] = hashlib.sha256(raw).hexdigest()
    _print_summary(report)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"完整报告: {output}")


if __name__ == "__main__":
    main()
