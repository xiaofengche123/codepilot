"""冻结并校验仓库内 RAG 评测集，防止评测后静默修改答案。"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from rag.indexer import (
    CODE_EXTENSIONS,
    _has_skipped_part,
    _split_fixed,
    _split_python,
)


EXPECTED_CATEGORIES = {
    "identifier", "natural_language", "bug_symptom", "cross_module",
    "mixed_language",
}
_LABEL = re.compile(r"^(.+):(\d+)-(\d+)$")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corpus_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in CODE_EXTENSIONS:
            continue
        rel = path.relative_to(root)
        if _has_skipped_part(set(rel.parts)):
            continue
        digest.update(rel.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_head(root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            text=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _git_dirty(root: Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True,
            text=True, timeout=10, check=True,
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def _label_has_chunk(root: Path, label: str) -> bool:
    parsed = _LABEL.fullmatch(label.replace("\\", "/"))
    if not parsed:
        return False
    path = root / parsed.group(1)
    if not path.is_file():
        return False
    start, end = int(parsed.group(2)), int(parsed.group(3))
    rel = path.relative_to(root).as_posix()
    content = path.read_text(encoding="utf-8", errors="ignore")
    chunks = (
        _split_python(path, content, rel)
        if path.suffix == ".py" else _split_fixed(path, content, rel)
    )
    for chunk in chunks:
        overlap = max(
            0,
            min(end, chunk["end_line"]) - max(start, chunk["start_line"]) + 1,
        )
        shorter = min(
            end - start + 1,
            chunk["end_line"] - chunk["start_line"] + 1,
        )
        if shorter > 0 and overlap / shorter >= 0.5:
            return True
    return False


def validate_dataset(
    dataset: list[dict], root: Path, *, dev_queries: set[str] | None = None,
    expected_total: int | None = None, expected_per_category: int | None = None,
) -> dict:
    errors = []
    ids = [str(item.get("id", "")) for item in dataset]
    queries = [str(item.get("query", "")).strip() for item in dataset]
    categories = Counter(str(item.get("category", "")) for item in dataset)
    if len(set(ids)) != len(ids):
        errors.append("存在重复 id")
    if len(set(queries)) != len(queries):
        errors.append("存在重复 query")
    if not all(ids) or not all(queries):
        errors.append("id/query 不能为空")
    if expected_total is not None and len(dataset) != expected_total:
        errors.append(f"期望 {expected_total} 条，实际 {len(dataset)} 条")
    if set(categories) != EXPECTED_CATEGORIES:
        errors.append(f"类别集合错误: {sorted(categories)}")
    if expected_per_category is not None:
        for category in EXPECTED_CATEGORIES:
            if categories[category] != expected_per_category:
                errors.append(
                    f"{category} 期望 {expected_per_category} 条，"
                    f"实际 {categories[category]} 条"
                )
    if dev_queries:
        overlap = sorted(set(queries) & dev_queries)
        if overlap:
            errors.append(f"与开发集存在 {len(overlap)} 条精确 query 重复")

    required_count = supporting_count = 0
    for item in dataset:
        item_id = item.get("id", "<missing>")
        required = item.get("required")
        supporting = item.get("supporting")
        if not isinstance(required, list) or not required:
            errors.append(f"{item_id}: required 必须是非空数组")
            continue
        if not isinstance(supporting, list):
            errors.append(f"{item_id}: supporting 必须是数组")
            continue
        if set(required) & set(supporting):
            errors.append(f"{item_id}: required/supporting 不得重叠")
        required_count += len(required)
        supporting_count += len(supporting)
        for label in [*required, *supporting]:
            if not _label_has_chunk(root, str(label)):
                errors.append(f"{item_id}: 无法映射到当前 chunk: {label}")

    if errors:
        raise ValueError("评测集校验失败:\n- " + "\n- ".join(errors))
    return {
        "query_count": len(dataset),
        "categories": dict(sorted(categories.items())),
        "required_labels": required_count,
        "supporting_labels": supporting_count,
    }


def freeze_dataset(
    dataset_path: Path, root: Path, manifest_path: Path, dev_path: Path | None,
) -> dict:
    raw = dataset_path.read_bytes()
    dataset = json.loads(raw.decode("utf-8"))
    dev_queries = None
    if dev_path and dev_path.exists():
        dev_queries = {
            str(item.get("query", "")).strip()
            for item in json.loads(dev_path.read_text(encoding="utf-8"))
        }
    summary = validate_dataset(
        dataset, root, dev_queries=dev_queries,
        expected_total=150, expected_per_category=30,
    )
    manifest = {
        "schema_version": 1,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset_path.relative_to(root).as_posix(),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "corpus_sha256_at_freeze": corpus_sha256(root),
        "git_head_at_freeze": _git_head(root),
        "git_dirty_at_freeze": _git_dirty(root),
        **summary,
        "policy": {
            "test_results_must_not_change_labels": True,
            "required_grade": 2,
            "supporting_grade": 1,
            "new_labels_require_a_new_dataset_version": True,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def check_manifest(dataset_path: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = sha256_file(dataset_path)
    if actual != manifest.get("dataset_sha256"):
        raise ValueError(
            "冻结集 SHA-256 不匹配；不要修改 v1，请创建 codepilot-test-v2.json"
        )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="冻结或检查 CodePilot RAG 测试集")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--project", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--dev", type=Path, default=Path(".rag-eval/codepilot-dev.json"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.project.resolve()
    dataset_path = args.dataset.resolve()
    manifest_path = (
        args.manifest.resolve()
        if args.manifest else dataset_path.with_suffix(".manifest.json")
    )
    if args.check:
        manifest = check_manifest(dataset_path, manifest_path)
        print(f"[通过] 冻结集未修改: {manifest['dataset_sha256']}")
        return
    manifest = freeze_dataset(dataset_path, root, manifest_path, args.dev.resolve())
    print(
        f"[冻结] {manifest['query_count']} 条查询，"
        f"SHA-256={manifest['dataset_sha256']}"
    )


if __name__ == "__main__":
    main()
