"""Freeze the independent, unscored ROUTE-007 retrieval validation set.

This module deliberately does not import the retriever or evaluator.  ROUTE-007
only seals annotations and the already-selected ROUTE-006 router profile;
strategy comparison belongs to ROUTE-008.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from rag.eval_dataset import corpus_sha256, sha256_file, validate_dataset
from rag.retrieval_router import (
    BASELINE_BM25_WEIGHT,
    BASELINE_VECTOR_WEIGHT,
    CROSS_MODULE_BM25_WEIGHT,
    CROSS_MODULE_CANDIDATE_COUNT,
    CROSS_MODULE_VECTOR_WEIGHT,
    DEFAULT_CANDIDATE_COUNT,
    DEFAULT_RRF_K,
    DISAGREEMENT_BM25_WEIGHT,
    DISAGREEMENT_CANDIDATE_COUNT,
    DISAGREEMENT_VECTOR_WEIGHT,
    EXACT_BM25_WEIGHT,
    EXACT_VECTOR_WEIGHT,
    MIXED_CANDIDATE_COUNT,
    MIXED_LANGUAGE_BM25_WEIGHT,
    MIXED_LANGUAGE_VECTOR_WEIGHT,
    NATURAL_LANGUAGE_BM25_WEIGHT,
    NATURAL_LANGUAGE_VECTOR_WEIGHT,
    ROUTER_VERSION,
)


VALIDATION_DATASET_NAME = "codepilot-validation-v1.json"
DEVELOPMENT_DATASET_NAME = "codepilot-dev.json"
EXPECTED_TOTAL = 50
EXPECTED_PER_CATEGORY = 10
_ID_PATTERNS = {
    "identifier": re.compile(r"^CPV1-I\d{2}$"),
    "natural_language": re.compile(r"^CPV1-N\d{2}$"),
    "bug_symptom": re.compile(r"^CPV1-B\d{2}$"),
    "cross_module": re.compile(r"^CPV1-C\d{2}$"),
    "mixed_language": re.compile(r"^CPV1-M\d{2}$"),
}


def _require_name(path: Path, expected: str, role: str) -> None:
    if path.name.casefold() != expected:
        raise ValueError(f"ROUTE-007 accepts only {expected} as the {role}")


def _normalized_query(value: object) -> str:
    return " ".join(str(value).casefold().split())


def router_profile() -> dict[str, Any]:
    """Return the complete data-selected ROUTE-006 fusion profile."""
    def family(bm25: float, vector: float, candidates: int) -> dict[str, float | int]:
        return {
            "bm25_weight": bm25,
            "vector_weight": vector,
            "candidate_count": candidates,
        }

    return {
        "router_version": ROUTER_VERSION,
        "rrf_k": DEFAULT_RRF_K,
        "families": {
            "baseline": family(
                BASELINE_BM25_WEIGHT,
                BASELINE_VECTOR_WEIGHT,
                DEFAULT_CANDIDATE_COUNT,
            ),
            "exact_code": family(
                EXACT_BM25_WEIGHT,
                EXACT_VECTOR_WEIGHT,
                DEFAULT_CANDIDATE_COUNT,
            ),
            "natural_language": family(
                NATURAL_LANGUAGE_BM25_WEIGHT,
                NATURAL_LANGUAGE_VECTOR_WEIGHT,
                DEFAULT_CANDIDATE_COUNT,
            ),
            "mixed_language": family(
                MIXED_LANGUAGE_BM25_WEIGHT,
                MIXED_LANGUAGE_VECTOR_WEIGHT,
                MIXED_CANDIDATE_COUNT,
            ),
            "cross_module": family(
                CROSS_MODULE_BM25_WEIGHT,
                CROSS_MODULE_VECTOR_WEIGHT,
                CROSS_MODULE_CANDIDATE_COUNT,
            ),
            "ranking_disagreement": family(
                DISAGREEMENT_BM25_WEIGHT,
                DISAGREEMENT_VECTOR_WEIGHT,
                DISAGREEMENT_CANDIDATE_COUNT,
            ),
        },
    }


def profile_sha256(profile: dict[str, Any] | None = None) -> str:
    payload = json.dumps(
        profile or router_profile(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_independent_items(
    items: list[dict], root: Path, development_items: list[dict]
) -> dict[str, Any]:
    """Validate structure, chunk labels, balanced IDs, and dev isolation."""
    dev_queries = {
        _normalized_query(item.get("query", "")) for item in development_items
    }
    validation_queries = {
        _normalized_query(item.get("query", "")) for item in items
    }
    nonempty_validation_queries = validation_queries - {""}
    if len(nonempty_validation_queries) != len(items):
        raise ValueError("独立验证集存在规范化后重复或为空的 query")
    overlap = sorted((validation_queries & dev_queries) - {""})
    if overlap:
        raise ValueError(
            f"独立验证集与开发集存在 {len(overlap)} 条规范化 query 重复"
        )

    bad_ids = []
    for item in items:
        category = str(item.get("category", ""))
        pattern = _ID_PATTERNS.get(category)
        if pattern is None or pattern.fullmatch(str(item.get("id", ""))) is None:
            bad_ids.append(str(item.get("id", "<missing>")))
    if bad_ids:
        raise ValueError("独立验证集 id/category 不匹配: " + ", ".join(bad_ids))

    summary = validate_dataset(
        items,
        root,
        expected_total=EXPECTED_TOTAL,
        expected_per_category=EXPECTED_PER_CATEGORY,
    )
    return {**summary, "development_query_overlap": 0}


def freeze_validation_dataset(
    dataset_path: Path,
    root: Path,
    manifest_path: Path,
    development_path: Path,
) -> dict[str, Any]:
    """Seal annotations and router parameters without running retrieval."""
    _require_name(dataset_path, VALIDATION_DATASET_NAME, "validation dataset")
    _require_name(development_path, DEVELOPMENT_DATASET_NAME, "development dataset")
    raw = dataset_path.read_bytes()
    items = json.loads(raw.decode("utf-8"))
    development_items = json.loads(development_path.read_text(encoding="utf-8"))
    if not isinstance(items, list) or not isinstance(development_items, list):
        raise ValueError("validation and development datasets must be JSON arrays")
    summary = validate_independent_items(items, root, development_items)
    profile = router_profile()
    manifest = {
        "schema_version": 1,
        "dataset_role": "independent_validation",
        "status": "frozen_unscored",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset_path.relative_to(root).as_posix(),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "development_dataset_sha256": sha256_file(development_path),
        "corpus_sha256_at_freeze": corpus_sha256(root),
        **summary,
        "router_profile": profile,
        "router_profile_sha256": profile_sha256(profile),
        "policy": {
            "retrieval_not_run_during_route_007": True,
            "results_must_not_change_queries_or_labels": True,
            "router_profile_must_not_change_before_route_008": True,
            "required_grade": 2,
            "supporting_grade": 1,
            "comparison_deferred_to": "ROUTE-008",
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def check_validation_manifest(dataset_path: Path, manifest_path: Path) -> dict[str, Any]:
    _require_name(dataset_path, VALIDATION_DATASET_NAME, "validation dataset")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset_role") != "independent_validation":
        raise ValueError("manifest dataset role is not independent_validation")
    if sha256_file(dataset_path) != manifest.get("dataset_sha256"):
        raise ValueError("独立验证集 SHA-256 不匹配；不得根据结果修改标注")
    if profile_sha256() != manifest.get("router_profile_sha256"):
        raise ValueError("ROUTE-006 路由参数已变化；不得在独立验证前重新调参")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze or check the unscored ROUTE-007 validation set"
    )
    parser.add_argument(
        "dataset", nargs="?", default=f".rag-eval/{VALIDATION_DATASET_NAME}", type=Path
    )
    parser.add_argument("--project", default=Path("."), type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--dev", default=Path(f".rag-eval/{DEVELOPMENT_DATASET_NAME}"), type=Path
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.project.resolve()
    dataset = args.dataset.resolve()
    manifest = (
        args.manifest.resolve()
        if args.manifest
        else dataset.with_suffix(".manifest.json")
    )
    if args.check:
        checked = check_validation_manifest(dataset, manifest)
        print(f"[通过] 独立验证集和路由参数未修改: {checked['dataset_sha256']}")
        return
    frozen = freeze_validation_dataset(dataset, root, manifest, args.dev.resolve())
    print(
        f"[冻结未评分] {frozen['query_count']} 条查询，"
        f"SHA-256={frozen['dataset_sha256']}；策略比较留待 ROUTE-008"
    )


if __name__ == "__main__":
    main()
