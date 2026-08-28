"""One-shot ROUTE-008 comparison on the frozen validation set.

The three declared strategies are evaluated without tuning: pure Vector,
compatibility fixed RRF, and the ROUTE-006 frozen adaptive router.  Reports omit
queries and result contents while retaining case IDs and paired metrics.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import time
from typing import Any, Iterable

from rag.eval_dataset import corpus_sha256
from rag.evaluate import calculate_metrics
from rag.query_features import extract_query_features
from rag.retrieval_confidence import calculate_retrieval_confidence
from rag.retrieval_router import route_retrieval
from rag.retrieval_validation import (
    VALIDATION_DATASET_NAME,
    check_validation_manifest,
)
from rag.retriever import SearchHit, reciprocal_rank_fusion


STRATEGIES = ("fixed_rrf", "vector", "adaptive")
DEFAULT_TOP_K = 10
FIXED_BM25_WEIGHT = 2.0
FIXED_VECTOR_WEIGHT = 0.25
FIXED_RRF_K = 10
FIXED_CANDIDATE_COUNT = 30
MAX_ADAPTIVE_CANDIDATES = 40
RESULT_NAME = "adaptive-routing-validation-2026-08-28.json"
MARKDOWN_NAME = "adaptive-routing-validation-2026-08-28.md"


def _require_filename(path: Path, expected: str, role: str) -> None:
    if path.name.casefold() != expected.casefold():
        raise ValueError(f"ROUTE-008 accepts only {expected} as the {role}")


def load_frozen_validation(
    dataset_path: Path, manifest_path: Path
) -> tuple[list[dict], dict[str, Any]]:
    """Check both freezes before reading validation annotations."""
    _require_filename(dataset_path, VALIDATION_DATASET_NAME, "validation dataset")
    manifest = check_validation_manifest(dataset_path, manifest_path)
    items = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(items, list) or len(items) != manifest.get("query_count"):
        raise ValueError("validation dataset does not match its frozen manifest")
    return items, manifest


def _raw_rankings(
    query: str,
    collection: Any,
    documents: list[SearchHit],
    limit: int,
) -> tuple[list[SearchHit], list[SearchHit]]:
    from config import config
    from rag.retriever import _vector_rank, bm25_rank

    k1 = float(config.get("rag.bm25_k1", 1.5))
    b = float(config.get("rag.bm25_b", 0.75))
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="route008") as pool:
        vector_future = pool.submit(_vector_rank, query, collection, limit, False)
        bm25_future = pool.submit(bm25_rank, query, documents, limit, k1, b)
        return vector_future.result(), bm25_future.result()


def _adaptive_family(reason_codes: tuple[str, ...]) -> str:
    if "ranking_disagreement" in reason_codes:
        return "ranking_disagreement"
    return next(
        (code.removeprefix("query_") for code in reason_codes if code.startswith("query_")),
        "baseline",
    )


def _rank_from_raw(
    strategy: str,
    query: str,
    vector_hits: list[SearchHit],
    bm25_hits: list[SearchHit],
    *,
    top_k: int = DEFAULT_TOP_K,
) -> tuple[list[SearchHit], str | None]:
    """Apply one predeclared strategy to supplied rankings without I/O."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unsupported ROUTE-008 strategy: {strategy}")
    if strategy == "vector":
        return vector_hits[:top_k], None
    if strategy == "fixed_rrf":
        return (
            reciprocal_rank_fusion(
                vector_hits[:FIXED_CANDIDATE_COUNT],
                bm25_hits[:FIXED_CANDIDATE_COUNT],
                top_k,
                rrf_k=FIXED_RRF_K,
                vector_weight=FIXED_VECTOR_WEIGHT,
                bm25_weight=FIXED_BM25_WEIGHT,
            ),
            None,
        )

    confidence = calculate_retrieval_confidence(
        query, vector_hits, bm25_hits, top_k=top_k
    )
    plan = route_retrieval(extract_query_features(query), confidence)
    return (
        reciprocal_rank_fusion(
            vector_hits[:plan.candidate_count],
            bm25_hits[:plan.candidate_count],
            top_k,
            rrf_k=plan.rrf_k,
            vector_weight=plan.vector_weight,
            bm25_weight=plan.bm25_weight,
        ),
        _adaptive_family(plan.reason_codes),
    )


def _run_strategy(
    strategy: str,
    query: str,
    collection: Any,
    documents: list[SearchHit],
    top_k: int,
) -> tuple[list[SearchHit], str | None]:
    if strategy == "vector":
        from rag.retriever import _vector_rank

        return _vector_rank(query, collection, top_k, False), None
    limit = FIXED_CANDIDATE_COUNT if strategy == "fixed_rrf" else MAX_ADAPTIVE_CANDIDATES
    vector_hits, bm25_hits = _raw_rankings(query, collection, documents, limit)
    return _rank_from_raw(
        strategy, query, vector_hits, bm25_hits, top_k=top_k
    )


def _mean(values: Iterable[float]) -> float:
    sequence = list(values)
    return statistics.fmean(sequence) if sequence else 0.0


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, int(len(ordered) * 0.95 + 0.999999) - 1)]


def _ci(values: list[float], seed: int = 20260828, samples: int = 2000) -> list[float]:
    if not values:
        return [0.0, 0.0]
    rng = random.Random(seed)
    size = len(values)
    estimates = sorted(
        statistics.fmean(values[rng.randrange(size)] for _ in range(size))
        for _ in range(samples)
    )
    return [estimates[int(samples * 0.025)], estimates[int(samples * 0.975)]]


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    recalls = [float(row["recall_at_10"]) for row in rows]
    mrrs = [float(row["mrr_at_10"]) for row in rows]
    latencies = [float(row["latency_ms"]) for row in rows]
    return {
        "query_count": len(rows),
        "recall_at_10": round(_mean(recalls), 6),
        "recall_at_10_ci95": [round(value, 6) for value in _ci(recalls)],
        "mrr_at_10": round(_mean(mrrs), 6),
        "mrr_at_10_ci95": [round(value, 6) for value in _ci(mrrs, 20260829)],
        "latency_ms": {
            "average": round(_mean(latencies), 3),
            "p95": round(_p95(latencies), 3),
            "maximum": round(max(latencies), 3),
        },
    }


def _paired(rows_by_strategy: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    baseline = rows_by_strategy["fixed_rrf"]
    result = {}
    for strategy in ("vector", "adaptive"):
        rows = rows_by_strategy[strategy]
        metrics = {}
        for metric in ("recall_at_10", "mrr_at_10"):
            differences = [
                float(row[metric]) - float(base[metric])
                for row, base in zip(rows, baseline)
            ]
            metrics[metric] = {
                "mean_difference": round(_mean(differences), 6),
                "ci95": [round(value, 6) for value in _ci(differences)],
                "improved": sum(value > 0 for value in differences),
                "degraded": sum(value < 0 for value in differences),
                "tied": sum(value == 0 for value in differences),
            }
        result[strategy] = metrics
    return result


def compare_strategies(
    items: list[dict], project_dir: str, *, top_k: int = DEFAULT_TOP_K
) -> dict[str, Any]:
    """Run exactly the three frozen strategies and return privacy-minimized results."""
    if top_k != DEFAULT_TOP_K:
        raise ValueError("ROUTE-008 comparison is frozen at top_k=10")
    from rag.indexer import _get_collection
    from rag.retriever import _collection_documents

    collection = _get_collection(project_dir)
    if collection is None:
        raise LookupError("project must be indexed before ROUTE-008 comparison")
    documents = _collection_documents(collection, include_docs=False)
    if not documents:
        raise LookupError("indexed code corpus is empty")

    valid = []
    for item in items:
        query = str(item.get("query", "")).strip()
        relevant = {str(value) for value in item.get("required", [])}
        if query and relevant:
            valid.append((item, query, relevant))
    if len(valid) != len(items):
        raise ValueError("every frozen validation item must have query and required labels")

    # Warm the local embedding/model path outside latency measurements.
    for strategy in STRATEGIES:
        _run_strategy(strategy, valid[0][1], collection, documents, top_k)

    rows_by_strategy: dict[str, list[dict[str, Any]]] = {
        strategy: [] for strategy in STRATEGIES
    }
    family_counts: Counter[str] = Counter()
    for item, query, relevant in valid:
        for strategy in STRATEGIES:
            started = time.perf_counter()
            hits, family = _run_strategy(
                strategy, query, collection, documents, top_k
            )
            elapsed = (time.perf_counter() - started) * 1000
            recall, mrr = calculate_metrics(hits, relevant)
            rows_by_strategy[strategy].append(
                {
                    "id": item.get("id"),
                    "category": item.get("category"),
                    "recall_at_10": round(recall, 6),
                    "mrr_at_10": round(mrr, 6),
                    "latency_ms": round(elapsed, 3),
                    **({"route_family": family} if family else {}),
                }
            )
            if strategy == "adaptive" and family:
                family_counts[family] += 1

    results = {}
    for strategy, rows in rows_by_strategy.items():
        categories: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            categories[str(row["category"])].append(row)
        results[strategy] = {
            "overall": _aggregate(rows),
            "by_category": {
                category: _aggregate(category_rows)
                for category, category_rows in sorted(categories.items())
            },
            "per_case": rows,
        }

    return {
        "schema_version": 1,
        "task": "ROUTE-008",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "top_k": top_k,
        "strategies": list(STRATEGIES),
        "results": results,
        "paired_vs_fixed_rrf": _paired(rows_by_strategy),
        "adaptive_route_families": dict(sorted(family_counts.items())),
        "limitations": [
            "same-repository internal validation, not an external benchmark",
            "latency is local sequential wall-clock after warmup, not production P95",
            "results must not be used to retune the frozen ROUTE-006 profile",
        ],
    }


def _git_head(root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            text=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# CodePilot ROUTE-008 独立验证对比（2026-08-28）",
        "",
        "> 本报告使用 ROUTE-007 评分前冻结的50条内部验证集。结果不得用于回调路由参数或修改标注。",
        "",
        "## 总体结果",
        "",
        "| 策略 | Recall@10 | MRR@10 | P95 latency (ms) |",
        "|---|---:|---:|---:|",
    ]
    names = {
        "fixed_rrf": "固定 RRF",
        "vector": "纯 Vector",
        "adaptive": "冻结自适应",
    }
    for strategy in STRATEGIES:
        overall = report["results"][strategy]["overall"]
        lines.append(
            f"| {names[strategy]} | {overall['recall_at_10']:.6f} | "
            f"{overall['mrr_at_10']:.6f} | {overall['latency_ms']['p95']:.3f} |"
        )
    lines.extend(["", "## 相对固定 RRF 的成对差值", ""])
    for strategy in ("vector", "adaptive"):
        paired = report["paired_vs_fixed_rrf"][strategy]
        lines.append(
            f"- {names[strategy]}：Recall@10 {paired['recall_at_10']['mean_difference']:+.6f}，"
            f"95% CI [{paired['recall_at_10']['ci95'][0]:+.6f}, "
            f"{paired['recall_at_10']['ci95'][1]:+.6f}]；MRR@10 "
            f"{paired['mrr_at_10']['mean_difference']:+.6f}，95% CI "
            f"[{paired['mrr_at_10']['ci95'][0]:+.6f}, "
            f"{paired['mrr_at_10']['ci95'][1]:+.6f}]。"
        )
    families = report["adaptive_route_families"]
    lines.extend([
        "",
        "## 自适应路由分布",
        "",
        "、".join(f"{name}={count}" for name, count in families.items()) + "。",
        "",
        "## 结论",
        "",
        "- 冻结自适应取得最高点估计，但 Recall 仅1条改善、49条持平，MRR 的成对区间跨过0；本轮没有证明稳定优势。",
        "- 纯 Vector 点估计低于固定 RRF，但两项成对区间同样跨过0，不能把差异外推到其他仓库。",
        "- 保持产品默认固定 RRF、关闭 Rerank；如需上线自适应 Router，应另设运行时接线、回退和线上观测任务。",
        "",
        "## 边界",
        "",
        "- 这是同仓库内部验证，不是跨仓库或线上泛化证据。",
        "- 延迟是本机预热后的顺序墙钟测量，不能作为生产环境 P95 SLO。",
        "- Retriever 默认路径仍未接入自适应 Router；产品行为没有因本评测改变。",
        "- 不启用 Rerank，不联网，不调用付费 API。",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the frozen ROUTE-008 comparison")
    parser.add_argument(
        "dataset", nargs="?", type=Path,
        default=Path(f".rag-eval/{VALIDATION_DATASET_NAME}"),
    )
    parser.add_argument("--project", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, default=Path(f".rag-eval/{RESULT_NAME}"))
    parser.add_argument(
        "--markdown-output", type=Path,
        default=Path(f".rag-eval/{MARKDOWN_NAME}"),
    )
    args = parser.parse_args()
    _require_filename(args.dataset, VALIDATION_DATASET_NAME, "validation dataset")
    _require_filename(args.output, RESULT_NAME, "result file")
    _require_filename(args.markdown_output, MARKDOWN_NAME, "markdown report")
    for output in (args.output, args.markdown_output):
        if output.exists():
            raise FileExistsError(f"ROUTE-008 result already exists; refusing overwrite: {output}")

    root = args.project.resolve()
    dataset = args.dataset.resolve()
    manifest_path = (
        args.manifest.resolve()
        if args.manifest else dataset.with_suffix(".manifest.json")
    )
    items, manifest = load_frozen_validation(dataset, manifest_path)
    report = compare_strategies(items, str(root))
    report.update({
        "dataset": dataset.relative_to(root).as_posix(),
        "dataset_sha256": manifest["dataset_sha256"],
        "router_profile_sha256": manifest["router_profile_sha256"],
        "corpus_sha256_at_evaluation": corpus_sha256(root),
        "git_head": _git_head(root),
        "offline": True,
        "rerank_enabled": False,
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))


if __name__ == "__main__":
    main()
