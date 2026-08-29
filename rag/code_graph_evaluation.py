"""Frozen one-shot GRAPH-007 cross-module retrieval evaluation.

The benchmark compares the repository's fixed Hybrid top-10 with a predeclared
5+5 layout: five baseline hits, up to five outgoing CALLS graph additions, then
baseline backfill.  Dataset annotations and the complete strategy profile are
sealed before retrieval.  Reports omit queries and result identities.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics
import subprocess
import time
from typing import Any, Iterable, Mapping

from rag.code_graph import GraphEdgeKind
from rag.code_graph_builder import CodeGraphBuildResult, build_python_code_graph
from rag.code_graph_context import (
    DEFAULT_GRAPH_SCORING_POLICY,
    GraphContextResult,
    select_graph_context,
)
from rag.code_graph_expansion import (
    GraphChunkRef,
    GraphTraversalDirection,
    expand_graph_one_hop,
)
from rag.eval_dataset import corpus_sha256, sha256_file
from rag.evaluate import calculate_metrics
from rag.indexer import _split_python
from rag.retriever import SearchHit


DATASET_NAME = "codepilot-graph-cross-module-v1.json"
MANIFEST_NAME = "codepilot-graph-cross-module-v1.manifest.json"
RESULT_NAME = "graph-cross-module-validation-2026-08-29.json"
MARKDOWN_NAME = "graph-cross-module-validation-2026-08-29.md"
DEVELOPMENT_DATASET_NAME = "codepilot-dev.json"
EXPECTED_QUERY_COUNT = 20
TOP_K = 10
BASELINE_PREFIX_COUNT = 5
GRAPH_SEED_COUNT = 5
GRAPH_MAX_CHUNKS = 5
GRAPH_TOKEN_BUDGET = 2_048
GRAPH_EDGE_KINDS = (GraphEdgeKind.CALLS,)
GRAPH_DIRECTION = GraphTraversalDirection.OUTGOING
MIN_RECALL_DELTA = 0.05
MAX_GRAPH_OVERHEAD_P95_MS = 10.0
MAX_IRRELEVANT_ADDITIONS_P95 = 3
_ID = re.compile(r"^CGV1-C(?:0[1-9]|1\d|20)$")
_LABEL = re.compile(r"^(.+):(\d+)-(\d+)$")


def _require_name(path: Path, expected: str, role: str) -> None:
    if path.name.casefold() != expected.casefold():
        raise ValueError(f"GRAPH-007 accepts only {expected} as the {role}")


def _normalized_query(value: object) -> str:
    return " ".join(str(value).casefold().split())


def evaluation_profile() -> dict[str, Any]:
    """Return every ranking, graph, budget, and acceptance choice."""
    return {
        "baseline": {
            "mode": "hybrid",
            "top_k": TOP_K,
            "adaptive_routing": False,
            "rerank": False,
            "include_docs": False,
        },
        "graph": {
            "baseline_prefix_count": BASELINE_PREFIX_COUNT,
            "seed_count": GRAPH_SEED_COUNT,
            "max_chunks": GRAPH_MAX_CHUNKS,
            "token_budget": GRAPH_TOKEN_BUDGET,
            "edge_kinds": [kind.value for kind in GRAPH_EDGE_KINDS],
            "direction": GRAPH_DIRECTION.value,
            "exclude_test_seeds_and_targets": True,
            "exclude_existing_baseline_top_k": True,
            "scoring_policy": DEFAULT_GRAPH_SCORING_POLICY.to_dict(),
        },
        "acceptance": {
            "minimum_recall_at_10_delta": MIN_RECALL_DELTA,
            "maximum_graph_overhead_p95_ms": MAX_GRAPH_OVERHEAD_P95_MS,
            "maximum_test_additions": 0,
            "maximum_document_additions": 0,
            "maximum_irrelevant_additions_p95": MAX_IRRELEVANT_ADDITIONS_P95,
            "ordinary_runtime_must_remain_unintegrated": True,
        },
    }


def profile_sha256(profile: Mapping[str, Any] | None = None) -> str:
    payload = json.dumps(
        dict(profile or evaluation_profile()),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _git_head(root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _git_dirty(root: Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def _tracked_python_sources(root: Path) -> dict[str, str]:
    """Load only tracked Python, never untracked/protected workspace files."""
    try:
        output = subprocess.run(
            ["git", "ls-files", "--", "*.py"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("GRAPH-007 project must be a readable Git repository") from exc
    sources: dict[str, str] = {}
    for raw in output.splitlines():
        relative = raw.replace("\\", "/")
        if not relative or relative.startswith(".rag-eval/"):
            continue
        path = root / relative
        sources[relative] = path.read_text(encoding="utf-8", errors="ignore")
    if not sources:
        raise ValueError("GRAPH-007 found no tracked Python sources")
    return sources


def _source_chunks(
    root: Path, sources: Mapping[str, str]
) -> tuple[GraphChunkRef, ...]:
    chunks: list[GraphChunkRef] = []
    for file, source in sorted(sources.items()):
        for chunk in _split_python(root / file, source, file):
            chunks.append(
                GraphChunkRef(
                    uid=f"{file}:{chunk['start_line']}-{chunk['end_line']}",
                    file=file,
                    start_line=chunk["start_line"],
                    end_line=chunk["end_line"],
                )
            )
    return tuple(chunks)


def _is_test_file(file: object) -> bool:
    normalized = str(file or "").replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return (
        normalized.startswith(("test/", "tests/"))
        or "/test/" in normalized
        or "/tests/" in normalized
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _parse_label(label: object) -> tuple[str, int, int]:
    if not isinstance(label, str):
        raise TypeError("chunk labels must be strings")
    match = _LABEL.fullmatch(label.replace("\\", "/"))
    if match is None:
        raise ValueError(f"invalid chunk label: {label}")
    return match.group(1), int(match.group(2)), int(match.group(3))


def validate_cross_module_items(
    items: list[dict[str, Any]],
    root: Path,
    development_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate annotations and prove each target is a real one-hop CALLS neighbor."""
    if not isinstance(items, list) or len(items) != EXPECTED_QUERY_COUNT:
        raise ValueError(f"GRAPH-007 dataset must contain {EXPECTED_QUERY_COUNT} items")
    queries = [_normalized_query(item.get("query")) for item in items]
    if any(not query for query in queries) or len(set(queries)) != len(queries):
        raise ValueError("GRAPH-007 queries must be non-empty and unique after normalization")
    dev_queries = {_normalized_query(item.get("query")) for item in development_items}
    if (set(queries) & dev_queries) - {""}:
        raise ValueError("GRAPH-007 queries overlap the development dataset")

    sources = _tracked_python_sources(root)
    graph = build_python_code_graph(sources)
    chunks = _source_chunks(root, sources)
    chunks_by_uid = {chunk.uid: chunk for chunk in chunks}
    required_count = 0
    supporting_count = 0
    reachable_target_count = 0
    seen_ids: set[str] = set()
    for item in items:
        item_id = item.get("id")
        if not isinstance(item_id, str) or _ID.fullmatch(item_id) is None:
            raise ValueError(f"invalid GRAPH-007 item id: {item_id}")
        if item_id in seen_ids:
            raise ValueError(f"duplicate GRAPH-007 item id: {item_id}")
        seen_ids.add(item_id)
        if item.get("category") != "cross_module":
            raise ValueError(f"{item_id}: category must be cross_module")
        seed = item.get("seed")
        required = item.get("required")
        supporting = item.get("supporting")
        if not isinstance(seed, str):
            raise ValueError(f"{item_id}: seed must be a chunk label")
        if not isinstance(required, list) or len(required) < 2:
            raise ValueError(f"{item_id}: required must contain seed and target")
        if not isinstance(supporting, list):
            raise ValueError(f"{item_id}: supporting must be an array")
        if seed not in required:
            raise ValueError(f"{item_id}: seed must also be required")
        labels = [*required, *supporting]
        if len(set(labels)) != len(labels):
            raise ValueError(f"{item_id}: labels must be unique")
        for label in labels:
            file, _, _ = _parse_label(label)
            if label not in chunks_by_uid:
                raise ValueError(f"{item_id}: label is not an exact current chunk: {label}")
            if _is_test_file(file) or not file.endswith(".py"):
                raise ValueError(f"{item_id}: labels must be production Python chunks")

        seed_chunk = chunks_by_uid[seed]
        targets = set(required) - {seed}
        if any(chunks_by_uid[target].file == seed_chunk.file for target in targets):
            raise ValueError(f"{item_id}: targets must be cross-file")
        expanded = expand_graph_one_hop(
            [seed_chunk],
            chunks,
            graph.nodes,
            graph.edges,
            edge_kinds=GRAPH_EDGE_KINDS,
            direction=GRAPH_DIRECTION,
        )
        reachable = {candidate.chunk.uid for candidate in expanded.candidates}
        missing = sorted(targets - reachable)
        if missing:
            raise ValueError(
                f"{item_id}: required targets are not one-hop CALLS neighbors: {missing}"
            )
        required_count += len(required)
        supporting_count += len(supporting)
        reachable_target_count += len(targets)

    return {
        "query_count": len(items),
        "categories": {"cross_module": len(items)},
        "required_labels": required_count,
        "supporting_labels": supporting_count,
        "reachable_target_labels": reachable_target_count,
        "development_query_overlap": 0,
        "graph_build_issue_count": len(graph.issues),
    }


def freeze_cross_module_dataset(
    dataset_path: Path,
    manifest_path: Path,
    root: Path,
    development_path: Path,
) -> dict[str, Any]:
    """Seal annotations and the complete strategy before any retrieval."""
    _require_name(dataset_path, DATASET_NAME, "dataset")
    _require_name(manifest_path, MANIFEST_NAME, "manifest")
    _require_name(development_path, DEVELOPMENT_DATASET_NAME, "development dataset")
    raw = dataset_path.read_bytes()
    items = json.loads(raw.decode("utf-8"))
    development_items = json.loads(development_path.read_text(encoding="utf-8"))
    summary = validate_cross_module_items(items, root, development_items)
    profile = evaluation_profile()
    manifest = {
        "schema_version": 1,
        "task": "GRAPH-007",
        "dataset_role": "cross_module_internal_validation",
        "status": "frozen_unscored",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset_path.relative_to(root).as_posix(),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "development_dataset_sha256": sha256_file(development_path),
        "corpus_sha256_at_freeze": corpus_sha256(root),
        "git_head_at_freeze": _git_head(root),
        "git_dirty_at_freeze": _git_dirty(root),
        **summary,
        "evaluation_profile": profile,
        "evaluation_profile_sha256": profile_sha256(profile),
        "policy": {
            "retrieval_not_run_before_freeze": True,
            "results_must_not_change_queries_labels_or_profile": True,
            "new_annotations_require_a_new_dataset_version": True,
            "same_repository_internal_validation": True,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def check_cross_module_manifest(
    dataset_path: Path, manifest_path: Path
) -> dict[str, Any]:
    _require_name(dataset_path, DATASET_NAME, "dataset")
    _require_name(manifest_path, MANIFEST_NAME, "manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset_role") != "cross_module_internal_validation":
        raise ValueError("GRAPH-007 manifest has an invalid dataset role")
    if sha256_file(dataset_path) != manifest.get("dataset_sha256"):
        raise ValueError("GRAPH-007 dataset changed after freeze; create a new version")
    if profile_sha256() != manifest.get("evaluation_profile_sha256"):
        raise ValueError("GRAPH-007 evaluation profile changed after freeze")
    return manifest


def _chunk_ref(hit: SearchHit) -> GraphChunkRef | None:
    try:
        return GraphChunkRef.from_metadata(hit.uid, hit.metadata)
    except (TypeError, ValueError):
        return None


def _production_python(hit: SearchHit) -> bool:
    file = str(hit.metadata.get("file", "")).replace("\\", "/")
    return file.endswith(".py") and not _is_test_file(file)


def _copy_graph_hit(selected: Any, indexed: SearchHit) -> SearchHit:
    metadata = dict(indexed.metadata)
    metadata.update(
        {
            "graph_expanded": True,
            "graph_score": selected.score.total,
            "graph_edge_kind": selected.best_evidence.edge_kind.value,
            "graph_seed_rank": selected.best_evidence.seed_rank,
            "graph_evidence_count": selected.evidence_count,
        }
    )
    return SearchHit(
        uid=indexed.uid,
        document=indexed.document,
        metadata=metadata,
        score=selected.score.total,
    )


def augment_fixed_hits(
    baseline_hits: Iterable[SearchHit],
    available_hits: Iterable[SearchHit],
    graph: CodeGraphBuildResult,
    token_costs: Mapping[str, int],
) -> tuple[list[SearchHit], GraphContextResult]:
    """Apply the frozen 5+5 GRAPH-007 layout to already-retrieved hits."""
    baseline = list(baseline_hits)[:TOP_K]
    available = list(available_hits)
    available_by_uid = {hit.uid: hit for hit in available}
    available_refs = tuple(
        ref
        for hit in available
        if _production_python(hit) and (ref := _chunk_ref(hit)) is not None
    )
    seeds: list[GraphChunkRef] = []
    for hit in baseline:
        if not _production_python(hit):
            continue
        ref = _chunk_ref(hit)
        if ref is not None:
            seeds.append(ref)
        if len(seeds) == GRAPH_SEED_COUNT:
            break

    expanded = expand_graph_one_hop(
        seeds,
        available_refs,
        graph.nodes,
        graph.edges,
        edge_kinds=GRAPH_EDGE_KINDS,
        direction=GRAPH_DIRECTION,
    )
    baseline_uids = {hit.uid for hit in baseline}
    novel = tuple(
        candidate
        for candidate in expanded.candidates
        if candidate.chunk.uid not in baseline_uids
        and candidate.chunk.uid in available_by_uid
        and _production_python(available_by_uid[candidate.chunk.uid])
    )
    novel_costs = {
        candidate.chunk.uid: token_costs[candidate.chunk.uid]
        for candidate in novel
        if candidate.chunk.uid in token_costs
    }
    context = select_graph_context(
        novel,
        novel_costs,
        token_budget=GRAPH_TOKEN_BUDGET,
        max_chunks=GRAPH_MAX_CHUNKS,
    )

    merged: list[SearchHit] = []
    seen: set[str] = set()

    def add(hit: SearchHit) -> None:
        if hit.uid not in seen and len(merged) < TOP_K:
            merged.append(hit)
            seen.add(hit.uid)

    for hit in baseline[:BASELINE_PREFIX_COUNT]:
        add(hit)
    for selected in context.selected:
        indexed = available_by_uid.get(selected.chunk.uid)
        if indexed is not None:
            add(_copy_graph_hit(selected, indexed))
    for hit in baseline[BASELINE_PREFIX_COUNT:]:
        add(hit)
    return merged, context


def _token_costs(hits: Iterable[SearchHit]) -> dict[str, int]:
    from rag.indexer import _get_model

    tokenizer = getattr(_get_model(), "tokenizer", None)
    if tokenizer is None or not callable(getattr(tokenizer, "encode", None)):
        raise RuntimeError("local embedding tokenizer is unavailable")
    costs = {}
    for hit in hits:
        if not _production_python(hit):
            continue
        encoded = tokenizer.encode(hit.document, add_special_tokens=False)
        costs[hit.uid] = max(1, len(encoded))
    return costs


def _matches_label(hit: SearchHit, label: str) -> bool:
    parsed = _LABEL.fullmatch(label.replace("\\", "/"))
    if parsed is None:
        return hit.uid.replace("\\", "/") == label.replace("\\", "/")
    file = str(hit.metadata.get("file", "")).replace("\\", "/")
    if file != parsed.group(1):
        return False
    start = hit.metadata.get("start_line")
    end = hit.metadata.get("end_line")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    target_start, target_end = int(parsed.group(2)), int(parsed.group(3))
    overlap = max(0, min(end, target_end) - max(start, target_start) + 1)
    shorter = min(end - start + 1, target_end - target_start + 1)
    return shorter > 0 and overlap / shorter >= 0.5


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    return statistics.fmean(materialized) if materialized else 0.0


def _p95(values: Iterable[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)] if ordered else 0.0


def _bootstrap_ci(
    values: list[float], *, seed: int = 20260829, samples: int = 2_000
) -> list[float]:
    if not values:
        return [0.0, 0.0]
    rng = random.Random(seed)
    size = len(values)
    estimates = sorted(
        statistics.fmean(values[rng.randrange(size)] for _ in range(size))
        for _ in range(samples)
    )
    return [estimates[int(samples * 0.025)], estimates[int(samples * 0.975)]]


def _round(value: float) -> float:
    return round(value, 6)


def compare_graph_retrieval(
    items: list[dict[str, Any]], root: Path
) -> dict[str, Any]:
    """Run one fixed retrieval per case, then time only the graph post-stage."""
    from config import config
    from rag.indexer import _get_collection
    from rag.retriever import _collection_documents, retrieve

    if bool(config.get("rag.adaptive_routing.enabled", False)):
        raise ValueError("GRAPH-007 requires adaptive routing to remain disabled")
    if bool(config.get("rag.reranker.enabled", False)):
        raise ValueError("GRAPH-007 requires rerank to remain disabled")
    if bool(config.get("rag.include_docs", False)):
        raise ValueError("GRAPH-007 requires documentation retrieval to remain disabled")

    collection = _get_collection(str(root))
    if collection is None:
        raise LookupError("project must be indexed before GRAPH-007 evaluation")
    available = _collection_documents(collection, include_docs=False)
    if not available:
        raise LookupError("indexed code corpus is empty")
    sources = _tracked_python_sources(root)
    graph = build_python_code_graph(sources)
    costs = _token_costs(available)

    retrieve(str(items[0]["query"]), str(root), TOP_K, mode="hybrid")
    rows = []
    for item in items:
        query = str(item["query"])
        required = {str(value) for value in item["required"]}
        supporting = {str(value) for value in item.get("supporting", [])}
        started = time.perf_counter()
        baseline = retrieve(query, str(root), TOP_K, mode="hybrid")
        baseline_ms = (time.perf_counter() - started) * 1_000

        graph_started = time.perf_counter()
        augmented, context = augment_fixed_hits(baseline, available, graph, costs)
        graph_ms = (time.perf_counter() - graph_started) * 1_000
        baseline_recall, baseline_mrr = calculate_metrics(baseline, required)
        graph_recall, graph_mrr = calculate_metrics(augmented, required)
        baseline_uids = {hit.uid for hit in baseline}
        additions = [hit for hit in augmented if hit.uid not in baseline_uids]
        relevant = required | supporting
        irrelevant = sum(
            not any(_matches_label(hit, label) for label in relevant)
            for hit in additions
        )
        rows.append(
            {
                "id": item["id"],
                "baseline_recall_at_10": _round(baseline_recall),
                "graph_recall_at_10": _round(graph_recall),
                "recall_difference": _round(graph_recall - baseline_recall),
                "baseline_mrr_at_10": _round(baseline_mrr),
                "graph_mrr_at_10": _round(graph_mrr),
                "mrr_difference": _round(graph_mrr - baseline_mrr),
                "baseline_latency_ms": round(baseline_ms, 3),
                "graph_overhead_ms": round(graph_ms, 3),
                "graph_added_count": len(additions),
                "graph_relevant_added_count": len(additions) - irrelevant,
                "graph_irrelevant_added_count": irrelevant,
                "graph_test_added_count": sum(
                    _is_test_file(hit.metadata.get("file")) for hit in additions
                ),
                "graph_document_added_count": sum(
                    hit.metadata.get("content_type") == "document" for hit in additions
                ),
                "graph_selected_token_count": context.selected_token_count,
            }
        )

    recall_differences = [float(row["recall_difference"]) for row in rows]
    mrr_differences = [float(row["mrr_difference"]) for row in rows]
    graph_overheads = [float(row["graph_overhead_ms"]) for row in rows]
    irrelevant_counts = [float(row["graph_irrelevant_added_count"]) for row in rows]
    baseline_recall = _mean(float(row["baseline_recall_at_10"]) for row in rows)
    graph_recall = _mean(float(row["graph_recall_at_10"]) for row in rows)
    test_additions = sum(int(row["graph_test_added_count"]) for row in rows)
    document_additions = sum(int(row["graph_document_added_count"]) for row in rows)
    recall_delta = graph_recall - baseline_recall
    overhead_p95 = _p95(graph_overheads)
    irrelevant_p95 = _p95(irrelevant_counts)
    acceptance = {
        "recall_delta_at_least_5pp": recall_delta >= MIN_RECALL_DELTA,
        "graph_overhead_p95_within_limit": overhead_p95 <= MAX_GRAPH_OVERHEAD_P95_MS,
        "no_test_additions": test_additions == 0,
        "no_document_additions": document_additions == 0,
        "irrelevant_additions_p95_within_limit": (
            irrelevant_p95 <= MAX_IRRELEVANT_ADDITIONS_P95
        ),
        "ordinary_runtime_unchanged": True,
    }
    acceptance["passed"] = all(acceptance.values())
    return {
        "schema_version": 1,
        "task": "GRAPH-007",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query_count": len(rows),
        "strategies": ["fixed_hybrid", "fixed_hybrid_plus_graph"],
        "overall": {
            "fixed_hybrid": {
                "recall_at_10": _round(baseline_recall),
                "mrr_at_10": _round(
                    _mean(float(row["baseline_mrr_at_10"]) for row in rows)
                ),
                "latency_ms": {
                    "average": round(
                        _mean(float(row["baseline_latency_ms"]) for row in rows), 3
                    ),
                    "p95": round(
                        _p95(float(row["baseline_latency_ms"]) for row in rows), 3
                    ),
                },
            },
            "fixed_hybrid_plus_graph": {
                "recall_at_10": _round(graph_recall),
                "mrr_at_10": _round(
                    _mean(float(row["graph_mrr_at_10"]) for row in rows)
                ),
                "graph_overhead_ms": {
                    "average": round(_mean(graph_overheads), 3),
                    "p95": round(overhead_p95, 3),
                    "maximum": round(max(graph_overheads), 3),
                },
            },
        },
        "paired_difference": {
            "recall_at_10": {
                "mean": _round(recall_delta),
                "ci95": [_round(value) for value in _bootstrap_ci(recall_differences)],
                "improved": sum(value > 0 for value in recall_differences),
                "degraded": sum(value < 0 for value in recall_differences),
                "tied": sum(value == 0 for value in recall_differences),
            },
            "mrr_at_10": {
                "mean": _round(_mean(mrr_differences)),
                "ci95": [
                    _round(value)
                    for value in _bootstrap_ci(mrr_differences, seed=20260830)
                ],
            },
        },
        "pollution": {
            "graph_added_count": sum(int(row["graph_added_count"]) for row in rows),
            "graph_relevant_added_count": sum(
                int(row["graph_relevant_added_count"]) for row in rows
            ),
            "graph_irrelevant_added_count": sum(
                int(row["graph_irrelevant_added_count"]) for row in rows
            ),
            "irrelevant_additions_per_query_p95": irrelevant_p95,
            "test_additions": test_additions,
            "document_additions": document_additions,
        },
        "acceptance": acceptance,
        "per_case": rows,
        "limitations": [
            "same-repository, internally authored validation; not external generalization",
            "annotations are statically reachable one-hop CALLS cases by construction",
            "graph weights and merge policy were frozen before retrieval and not tuned",
            "latency is local warm sequential wall-clock, not a production SLO",
            "ordinary runtime is unchanged because graph retrieval is not product-integrated",
        ],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    fixed = report["overall"]["fixed_hybrid"]
    graph = report["overall"]["fixed_hybrid_plus_graph"]
    paired = report["paired_difference"]
    pollution = report["pollution"]
    acceptance = report["acceptance"]
    verdict = "通过" if acceptance["passed"] else "未完全通过"
    return "\n".join(
        [
            "# CodePilot GRAPH-007 跨模块专项评测（2026-08-29）",
            "",
            "> 数据集与完整策略在检索前冻结；结果未用于修改查询、标注、权重或合并策略。",
            "",
            "## 结果",
            "",
            "| 策略 | Recall@10 | MRR@10 | P95 latency / overhead (ms) |",
            "|---|---:|---:|---:|",
            f"| 固定 Hybrid | {fixed['recall_at_10']:.6f} | {fixed['mrr_at_10']:.6f} | {fixed['latency_ms']['p95']:.3f} |",
            f"| 固定 Hybrid + 图 | {graph['recall_at_10']:.6f} | {graph['mrr_at_10']:.6f} | +{graph['graph_overhead_ms']['p95']:.3f} |",
            "",
            f"Recall@10 成对差值 `{paired['recall_at_10']['mean']:+.6f}`，95% CI "
            f"`[{paired['recall_at_10']['ci95'][0]:+.6f}, {paired['recall_at_10']['ci95'][1]:+.6f}]`；"
            f"改善/下降/持平为 {paired['recall_at_10']['improved']}/"
            f"{paired['recall_at_10']['degraded']}/{paired['recall_at_10']['tied']}。",
            "",
            "## 上下文污染与验收",
            "",
            f"- 图新增 {pollution['graph_added_count']} 个Chunk，其中相关 {pollution['graph_relevant_added_count']}、无关 {pollution['graph_irrelevant_added_count']}。",
            f"- 测试Chunk新增 {pollution['test_additions']}，文档Chunk新增 {pollution['document_additions']}，单题无关新增P95为 {pollution['irrelevant_additions_per_query_p95']:.0f}。",
            f"- GRAPH-007 总体验收：**{verdict}**。",
            "",
            "## 边界",
            "",
            "- 这是同仓库、内部编写且按静态一跳CALLS可达性筛选的专项集，不是外部泛化证据。",
            "- 图权重与5+5合并策略未经本数据集调参；评测后未修改冻结内容。",
            "- 延迟为本机预热后顺序墙钟；图仍未接入产品Retriever，因此普通查询运行时没有变化。",
            "- 不启用Rerank，不联网，不调用付费API。",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze or run GRAPH-007 evaluation")
    parser.add_argument(
        "dataset", nargs="?", type=Path, default=Path(f".rag-eval/{DATASET_NAME}")
    )
    parser.add_argument("--project", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--dev", type=Path, default=Path(f".rag-eval/{DEVELOPMENT_DATASET_NAME}")
    )
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=Path(f".rag-eval/{RESULT_NAME}")
    )
    parser.add_argument(
        "--markdown-output", type=Path, default=Path(f".rag-eval/{MARKDOWN_NAME}")
    )
    args = parser.parse_args()
    if args.freeze and args.check:
        raise ValueError("choose only one of --freeze or --check")
    root = args.project.resolve()
    dataset = args.dataset.resolve()
    manifest = (
        args.manifest.resolve()
        if args.manifest
        else dataset.with_name(MANIFEST_NAME)
    )
    if args.freeze:
        frozen = freeze_cross_module_dataset(
            dataset, manifest, root, args.dev.resolve()
        )
        print(
            f"[冻结未评分] {frozen['query_count']} 条跨模块查询，"
            f"SHA-256={frozen['dataset_sha256']}"
        )
        return
    checked = check_cross_module_manifest(dataset, manifest)
    if args.check:
        print(
            "[通过] GRAPH-007 数据集与策略未修改: "
            f"{checked['dataset_sha256']}"
        )
        return
    _require_name(args.output, RESULT_NAME, "result")
    _require_name(args.markdown_output, MARKDOWN_NAME, "markdown result")
    for output in (args.output, args.markdown_output):
        if output.exists():
            raise FileExistsError(f"refusing to overwrite GRAPH-007 result: {output}")
    current_corpus = corpus_sha256(root)
    if current_corpus != checked.get("corpus_sha256_at_freeze"):
        raise ValueError("code corpus changed after GRAPH-007 freeze")
    items = json.loads(dataset.read_text(encoding="utf-8"))
    report = compare_graph_retrieval(items, root)
    report.update(
        {
            "dataset": dataset.relative_to(root).as_posix(),
            "dataset_sha256": checked["dataset_sha256"],
            "evaluation_profile_sha256": checked["evaluation_profile_sha256"],
            "corpus_sha256_at_evaluation": current_corpus,
            "git_head": _git_head(root),
            "offline": True,
            "rerank_enabled": False,
            "runtime_integrated": False,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown = render_markdown(report)
    args.markdown_output.write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
