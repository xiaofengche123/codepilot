import json

import pytest

from rag.agent_eval_report import (
    load_reports,
    percentile,
    summarize_reports,
    validate_run_id,
)


@pytest.mark.parametrize("run_id", ["agent-v2-transactional", "run_01", "v1.2"])
def test_validate_run_id_accepts_safe_directory_names(run_id):
    assert validate_run_id(run_id) == run_id


@pytest.mark.parametrize("run_id", ["", "../escape", "a/b", " space", "x" * 65])
def test_validate_run_id_rejects_unsafe_directory_names(run_id):
    with pytest.raises(ValueError):
        validate_run_id(run_id)


def test_percentile_uses_nearest_rank_and_handles_empty_input():
    assert percentile([], 0.95) == 0.0
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert percentile([1, 2, 3, 4, 5], 0.95) == 5


def _report(task_id, condition, success, *, elapsed=1.0, unexpected=None):
    return {
        "task_id": task_id,
        "condition": condition,
        "success": success,
        "elapsed_seconds": elapsed,
        "modified_expected_file": success,
        "mutation_restored": success,
        "unexpected_files": unexpected or [],
        "edit_attempted": True,
        "transactional_edit_attempt_count": 1,
        "legacy_write_attempt_count": 0,
        "edit_attempt_count": 1,
        "edit_success_count": int(success),
        "edit_precondition_failure_count": int(not success),
        "edit_write_failure_count": 0,
        "edit_rollback_count": 0,
        "edit_error_codes": {} if success else {"match_count_mismatch": 1},
        "semantic_search_calls": 2,
        "tool_call_count": 5,
        "test_returncode": 0 if success else 1,
        "agent_error": None,
    }


def test_summary_reports_condition_metrics_failures_and_pairs():
    reports = [
        _report("A01", "hybrid", True, elapsed=10),
        _report("A01", "rerank", True, elapsed=20),
        _report("A02", "hybrid", False, elapsed=30, unexpected=["extra.py"]),
        _report("A02", "rerank", True, elapsed=40),
        _report("A03", "hybrid", True, elapsed=50),
        _report("A03", "rerank", False, elapsed=60),
        _report("A04", "hybrid", False, elapsed=70),
        _report("A04", "rerank", False, elapsed=80),
    ]

    summary = summarize_reports(reports, ("hybrid", "rerank"))

    hybrid = summary["conditions"]["hybrid"]
    assert hybrid["run_count"] == 4
    assert hybrid["success_count"] == 2
    assert hybrid["success_rate"] == 0.5
    assert hybrid["edit_precondition_failure_count"] == 2
    assert hybrid["edit_error_codes"] == {"match_count_mismatch": 2}
    assert hybrid["tasks_with_unexpected_files"] == 1
    assert hybrid["elapsed_seconds"] == {
        "average": 40.0,
        "median": 40.0,
        "p95": 70.0,
        "maximum": 70.0,
        "total": 160.0,
    }
    assert [failure["task_id"] for failure in hybrid["failures"]] == ["A02", "A04"]
    assert summary["paired"] == {
        "complete_pair_count": 4,
        "both_success": 1,
        "both_failed": 1,
        "hybrid_only_success": 1,
        "rerank_only_success": 1,
    }


def test_load_reports_ignores_manifest_and_summary(tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "A01-hybrid.json").write_text(
        json.dumps({"task_id": "A01", "condition": "hybrid"}), encoding="utf-8"
    )
    (tmp_path / "unrelated.json").write_text(
        json.dumps({"task_id": "X", "condition": "hybrid"}), encoding="utf-8"
    )

    reports = load_reports(tmp_path)

    assert reports == [{"task_id": "A01", "condition": "hybrid"}]
