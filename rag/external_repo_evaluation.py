"""Freeze and evaluate a small retrieval set across pinned external repositories."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Iterable

from rag.eval_dataset import _label_has_chunk, corpus_sha256
from rag.evaluate import _aggregate, _bootstrap_ci, _mean, _percentile, evaluate


REPOSITORIES = {
    "itsdangerous": {
        "url": "https://github.com/pallets/itsdangerous.git",
        "commit": "672971d66a2ef9f85151e53283113f33d642dabd",
    },
    "markupsafe": {
        "url": "https://github.com/pallets/markupsafe.git",
        "commit": "b2e4d9c7687be25695fffbe93a37622302b24fb1",
    },
    "click": {
        "url": "https://github.com/pallets/click.git",
        "commit": "36baa15ff831b939a22bc527cd76ce653ef6f66d",
    },
}
AUDIT_IDS = ("EXT-I02", "EXT-M03", "EXT-C04")


def _git_head(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True, timeout=10,
    ).stdout.strip()


def validate(dataset: list[dict], corpus_root: Path) -> dict:
    errors: list[str] = []
    ids = [str(item.get("id", "")) for item in dataset]
    if len(dataset) != 12 or len(set(ids)) != 12 or not all(ids):
        errors.append("dataset must contain 12 unique non-empty ids")
    counts = {name: 0 for name in REPOSITORIES}
    repositories = {}
    for name, frozen in REPOSITORIES.items():
        root = corpus_root / name
        if not root.is_dir():
            errors.append(f"missing repository: {name}")
            continue
        head = _git_head(root)
        if head != frozen["commit"]:
            errors.append(f"{name}: expected {frozen['commit']}, got {head}")
        repositories[name] = {
            **frozen, "corpus_sha256": corpus_sha256(root), "git_head": head,
        }
    for item in dataset:
        name = str(item.get("repository", ""))
        if name not in REPOSITORIES:
            errors.append(f"{item.get('id')}: unknown repository {name}")
            continue
        counts[name] += 1
        if not str(item.get("query", "")).strip() or not item.get("required"):
            errors.append(f"{item.get('id')}: query and required are mandatory")
        for label in [*item.get("required", []), *item.get("supporting", [])]:
            if not _label_has_chunk(corpus_root / name, str(label)):
                errors.append(f"{item.get('id')}: invalid chunk label {label}")
    if any(value != 4 for value in counts.values()):
        errors.append(f"expected four items per repository, got {counts}")
    if errors:
        raise ValueError("external dataset validation failed:\n- " + "\n- ".join(errors))
    return {"query_count": len(dataset), "repository_counts": counts,
            "repositories": repositories, "audit_ids": list(AUDIT_IDS)}


def run(dataset_path: Path, corpus_root: Path, modes: Iterable[str]) -> dict:
    raw = dataset_path.read_bytes()
    dataset = json.loads(raw.decode("utf-8"))
    frozen = validate(dataset, corpus_root)
    per_repository = {}
    for name in REPOSITORIES:
        subset = [item for item in dataset if item["repository"] == name]
        per_repository[name] = evaluate(
            subset, str(corpus_root / name), ks=(5, 10), modes=modes,
        )
    combined_rows = {
        mode: [
            row
            for name in REPOSITORIES
            for row in per_repository[name]["results"][mode]["per_query"]
        ]
        for mode in modes
    }
    overall = {}
    for mode, rows in combined_rows.items():
        latencies = [float(row["latency_ms"]) for row in rows]
        overall[mode] = {
            "metrics": _aggregate(rows, [5, 10]),
            "latency_ms": {
                "avg": _mean(latencies),
                "p95": _percentile(latencies, 0.95),
                "max": max(latencies),
            },
            "fallback_rate": sum(bool(row["fallback"]) for row in rows) / len(rows),
        }
    if "hybrid" in combined_rows and "rerank" in combined_rows:
        for k in (5, 10):
            for metric in ("required_recall", "mrr", "graded_ndcg"):
                diffs = [
                    float(r["at_k"][str(k)][metric])
                    - float(h["at_k"][str(k)][metric])
                    for h, r in zip(combined_rows["hybrid"], combined_rows["rerank"])
                ]
                overall.setdefault("rerank_vs_hybrid", {})[f"{metric}@{k}"] = {
                    "mean": _mean(diffs),
                    "ci95": _bootstrap_ci(diffs, seed=20260902 + k),
                    "improved": sum(value > 0 for value in diffs),
                    "degraded": sum(value < 0 for value in diffs),
                    "tied": sum(value == 0 for value in diffs),
                }
    return {
        "schema_version": 1,
        "dataset": dataset_path.name,
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        **frozen,
        "modes": list(modes),
        "overall": overall,
        "per_repository": per_repository,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    if args.check:
        print(json.dumps(validate(dataset, args.corpus_root), ensure_ascii=False))
        return
    result = run(args.dataset, args.corpus_root, ("hybrid", "rerank"))
    if not args.output:
        raise SystemExit("--output is required unless --check is used")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"dataset_sha256": result["dataset_sha256"], "queries": result["query_count"]}))


if __name__ == "__main__":
    main()
