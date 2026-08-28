"""Frozen three-strategy comparison tests for ROUTE-008."""

import json

import pytest

from rag import retrieval_comparison
from rag.retriever import SearchHit


def _hit(uid):
    return SearchHit(
        uid=uid,
        document=uid,
        metadata={"file": uid.split(":", 1)[0], "start_line": 1, "end_line": 10},
    )


def test_fixed_rrf_and_vector_are_predeclared_and_distinct():
    vector = [_hit("vector.py:1-10"), _hit("target.py:1-10")]
    bm25 = [_hit("target.py:1-10"), _hit("keyword.py:1-10")]
    vector_result, vector_family = retrieval_comparison._rank_from_raw(
        "vector", "query", vector, bm25
    )
    fixed_result, fixed_family = retrieval_comparison._rank_from_raw(
        "fixed_rrf", "query", vector, bm25
    )
    assert vector_result[0].uid == "vector.py:1-10"
    assert fixed_result[0].uid == "target.py:1-10"
    assert vector_family is fixed_family is None


def test_adaptive_uses_frozen_router_and_reports_family():
    vector = [_hit("vector.py:1-10"), _hit("target.py:1-10")]
    bm25 = [_hit("target.py:1-10"), _hit("keyword.py:1-10")]
    results, family = retrieval_comparison._rank_from_raw(
        "adaptive", "中文 mixed query across modules", vector, bm25
    )
    assert results
    assert family in {
        "baseline", "exact_code", "natural_language", "mixed_language",
        "cross_module", "ranking_disagreement",
    }


def test_ranker_rejects_undeclared_strategy():
    with pytest.raises(ValueError, match="unsupported"):
        retrieval_comparison._rank_from_raw("oracle", "q", [], [])


@pytest.mark.parametrize(
    "name", ["codepilot-dev.json", "codepilot-test-v1.json", "formal-results.json"]
)
def test_loader_rejects_other_artifacts_before_reading(tmp_path, name):
    with pytest.raises(ValueError, match="accepts only codepilot-validation-v1.json"):
        retrieval_comparison.load_frozen_validation(
            tmp_path / name, tmp_path / "manifest.json"
        )


def test_loader_checks_manifest_before_reading_annotations(tmp_path, monkeypatch):
    dataset = tmp_path / "codepilot-validation-v1.json"
    dataset.write_text("not json", encoding="utf-8")
    called = []

    def reject(*args):
        called.append(True)
        raise ValueError("frozen mismatch")

    monkeypatch.setattr(retrieval_comparison, "check_validation_manifest", reject)
    with pytest.raises(ValueError, match="frozen mismatch"):
        retrieval_comparison.load_frozen_validation(dataset, tmp_path / "manifest.json")
    assert called == [True]


def test_paired_comparison_is_against_fixed_rrf():
    rows = {
        "fixed_rrf": [
            {"recall_at_10": 0.5, "mrr_at_10": 0.5},
            {"recall_at_10": 1.0, "mrr_at_10": 0.5},
        ],
        "vector": [
            {"recall_at_10": 1.0, "mrr_at_10": 0.25},
            {"recall_at_10": 1.0, "mrr_at_10": 0.5},
        ],
        "adaptive": [
            {"recall_at_10": 0.5, "mrr_at_10": 1.0},
            {"recall_at_10": 0.5, "mrr_at_10": 0.5},
        ],
    }
    paired = retrieval_comparison._paired(rows)
    assert paired["vector"]["recall_at_10"]["mean_difference"] == 0.25
    assert paired["adaptive"]["mrr_at_10"]["mean_difference"] == 0.25


def test_report_omits_queries_and_ranked_documents():
    report = {
        "results": {
            strategy: {
                "overall": {
                    "recall_at_10": 1.0,
                    "mrr_at_10": 1.0,
                    "latency_ms": {"p95": 1.0},
                }
            }
            for strategy in retrieval_comparison.STRATEGIES
        },
        "paired_vs_fixed_rrf": {
            strategy: {
                metric: {"mean_difference": 0.0}
                for metric in ("recall_at_10", "mrr_at_10")
            }
            for strategy in ("vector", "adaptive")
        },
        "adaptive_route_families": {"baseline": 1},
    }
    for strategy in ("vector", "adaptive"):
        for metric in ("recall_at_10", "mrr_at_10"):
            report["paired_vs_fixed_rrf"][strategy][metric]["ci95"] = [0.0, 0.0]
    rendered = retrieval_comparison.render_markdown(report)
    assert "Recall@10" in rendered
    assert '"query"' not in json.dumps(report)
    assert "top_results" not in json.dumps(report)


def test_frozen_output_names_are_stable():
    assert retrieval_comparison.RESULT_NAME.endswith("2026-08-28.json")
    assert retrieval_comparison.MARKDOWN_NAME.endswith("2026-08-28.md")
