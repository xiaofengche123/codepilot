import json

import pytest

from rag import codesearchnet, eval_dataset, evaluate
from rag.retriever import SearchHit


def _hit(uid: str, file: str, start: int, end: int) -> SearchHit:
    return SearchHit(
        uid=uid,
        document="code",
        metadata={"file": file, "start_line": start, "end_line": end},
    )


def test_query_metrics_separates_required_and_supporting_relevance():
    hits = [
        _hit("test.py:1-5", "test.py", 1, 5),
        _hit("core.py:10-20", "core.py", 10, 20),
    ]
    metrics = evaluate._query_metrics(
        hits,
        required={"core.py:10-20"},
        supporting={"test.py:1-5"},
    )
    assert metrics["required_recall"] == 1.0
    assert metrics["mrr"] == 0.5
    assert 0.0 < metrics["graded_ndcg"] < 1.0
    assert metrics["missing_required"] == []


def test_query_metrics_does_not_count_same_label_twice():
    hits = [
        _hit("core.py:10-20", "core.py", 10, 20),
        _hit("core.py:11-19", "core.py", 11, 19),
    ]
    metrics = evaluate._query_metrics(
        hits,
        required={"core.py:10-20"},
        supporting=set(),
    )
    assert metrics["required_recall"] == 1.0
    assert metrics["graded_ndcg"] == 1.0


def test_labels_supports_legacy_relevant_format():
    required, supporting = evaluate._labels(
        {"relevant": ["a.py:1-2"], "supporting": ["b.py:1-2"]}
    )
    assert required == {"a.py:1-2"}
    assert supporting == {"b.py:1-2"}


def test_bootstrap_ci_is_deterministic_and_contains_mean():
    first = evaluate._bootstrap_ci([0.0, 0.5, 1.0], samples=200)
    second = evaluate._bootstrap_ci([0.0, 0.5, 1.0], samples=200)
    assert first == second
    assert first[0] <= 0.5 <= first[1]


def test_validate_dataset_checks_all_categories_and_chunk_labels(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("def target():\n    return 1\n", encoding="utf-8")
    categories = sorted(eval_dataset.EXPECTED_CATEGORIES)
    dataset = [
        {
            "id": f"Q-{index}",
            "category": category,
            "query": f"query {index}",
            "required": ["sample.py:1-2"],
            "supporting": [],
        }
        for index, category in enumerate(categories)
    ]
    summary = eval_dataset.validate_dataset(
        dataset,
        tmp_path,
        expected_total=5,
        expected_per_category=1,
    )
    assert summary["query_count"] == 5
    assert summary["required_labels"] == 5


def test_check_manifest_rejects_modified_frozen_dataset(tmp_path):
    dataset_path = tmp_path / "test.json"
    dataset_path.write_text("[]", encoding="utf-8")
    manifest_path = tmp_path / "test.manifest.json"
    manifest_path.write_text(
        json.dumps({"dataset_sha256": eval_dataset.sha256_file(dataset_path)}),
        encoding="utf-8",
    )
    dataset_path.write_text("[{}]", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        eval_dataset.check_manifest(dataset_path, manifest_path)


def test_codesearchnet_url_parser_uses_pinned_raw_commit():
    raw, start, end = codesearchnet._parse_github_url(
        "https://github.com/org/repo/blob/abc123/src/a.py#L10-L20"
    )
    assert raw == "https://raw.githubusercontent.com/org/repo/abc123/src/a.py"
    assert (start, end) == (10, 20)


def test_codesearchnet_ndcg_rewards_better_graded_order():
    qrels = {"best": 3, "useful": 1, "wrong": 0}
    ideal = codesearchnet._ndcg(["best", "useful", "wrong"], qrels, 3)
    reversed_score = codesearchnet._ndcg(
        ["wrong", "useful", "best"], qrels, 3
    )
    assert ideal == pytest.approx(1.0)
    assert reversed_score < ideal
