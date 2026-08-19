import pytest

from trace_analysis import (
    FAILURE_STAGES,
    aggregate_runtime_traces,
    aggregate_trace_funnel,
    analyze_report,
)
from task_queue import Task, TaskQueue


def _trace(**overrides):
    trace = {
        "schema_version": 1,
        "task_id": "A01",
        "session_id": "A01",
        "phases": [],
        "retrieval_calls": [{
            "tool_name": "search_code", "iteration": 1, "success": True,
            "error_code": None, "result_files": ["src/app.py"], "timestamp": 1.0,
        }],
        "inspected_files": ["src/app.py", "tests/test_app.py"],
        "edit_attempts": [{
            "tool_name": "edit_file_transaction", "path": "src/app.py",
            "iteration": 2, "success": True, "byte_changed": True,
            "rolled_back": False, "legacy": False, "revision": 1,
            "error_code": None, "timestamp": 2.0,
        }],
        "test_runs": [{
            "tool_name": "run_shell", "iteration": 3, "returncode": 0,
            "success": True, "revision": 1, "error_code": None,
            "timestamp": 3.0,
        }],
        "changed_files": ["src/app.py"],
        "final_status": "complete",
        "failure_stage": None,
        "secondary_failure_reasons": [],
        "failure_domain": None,
        "failure_reason_code": None,
    }
    trace.update(overrides)
    return trace


def _report(**overrides):
    report = {
        "task_id": "A01",
        "condition": "hybrid",
        "success": True,
        "expected_files": ["src/app.py"],
        "execution_trace": _trace(),
        "edit_attempted": True,
        "modified_expected_file": True,
        "mutation_restored": True,
        "test_returncode": 0,
        "unexpected_files": [],
        "edit_error_codes": {},
        "agent_error": None,
    }
    report.update(overrides)
    return report


def test_trace_funnel_aggregates_each_objective_stage():
    success = _report()
    missed = _report(
        task_id="A02", success=False, edit_attempted=False,
        modified_expected_file=False, mutation_restored=False,
        test_returncode=1,
        execution_trace=_trace(
            retrieval_calls=[], inspected_files=[], edit_attempts=[], test_runs=[],
            changed_files=[], final_status="failed",
        ),
    )

    funnel = aggregate_trace_funnel([success, missed])

    assert funnel["tasks_total"] == 2
    assert funnel["retrieval_hit_required"] == 1
    assert funnel["correct_file_inspected"] == 1
    assert funnel["relevant_test_inspected"] == 1
    assert funnel["edit_attempted"] == 1
    assert funnel["target_file_modified"] == 1
    assert funnel["oracle_satisfied"] == 1
    assert funnel["test_executed"] == 1
    assert funnel["test_passed"] == 1
    assert funnel["no_unexpected_files"] == 2
    assert funnel["rates"]["test_passed"] == 0.5


@pytest.mark.parametrize(
    ("updates", "stage", "domain"),
    [
        ({"execution_trace": _trace(retrieval_calls=[], inspected_files=[], edit_attempts=[])}, "retrieval_miss", "code"),
        ({"execution_trace": _trace(inspected_files=["other.py"], edit_attempts=[])}, "wrong_file_inspected", "code"),
        ({"execution_trace": _trace(edit_attempts=[])}, "no_edit_attempt", "code"),
        ({"edit_error_codes": {"match_count_mismatch": 1}}, "edit_precondition_failed", "code"),
        ({"edit_error_codes": {"python_syntax_error": 1}}, "syntax_failure", "code"),
        ({"mutation_restored": False}, "incorrect_edit", "code"),
        ({"test_returncode": 1}, "test_assertion_failure", "code"),
        ({"unexpected_files": ["extra.py"]}, "unexpected_file_change", "code"),
        ({"execution_trace": _trace(phases=[{"event_type": "transition", "to_phase": "failed", "reason": "max_iterations_exhausted"}])}, "iteration_budget_exhausted", "control"),
        ({"worker_failure": True, "worker_returncode": 124}, "environment_failure", "environment"),
    ],
)
def test_failure_classifier_returns_one_stable_primary_stage(updates, stage, domain):
    report = _report(success=False)
    report.update(updates)
    if stage in {"retrieval_miss", "wrong_file_inspected", "no_edit_attempt"}:
        report["edit_attempted"] = False
        report["modified_expected_file"] = False
        report["mutation_restored"] = False
    result = analyze_report(report)

    assert result["failure_stage"] == stage
    assert result["failure_stage"] in FAILURE_STAGES
    assert result["failure_domain"] == domain
    assert stage not in result["secondary_failure_reasons"]
    assert isinstance(result["failure_reason_code"], str)


def test_environment_failure_is_primary_and_code_symptom_is_secondary():
    result = analyze_report(_report(
        success=False, worker_failure=True, worker_returncode=124,
        test_returncode=1, mutation_restored=False,
    ))

    assert result["failure_stage"] == "environment_failure"
    assert result["failure_domain"] == "environment"
    assert "test_assertion_failure" in result["secondary_failure_reasons"]


def test_successful_report_has_no_failure_classification():
    assert analyze_report(_report()) == {
        "failure_stage": None,
        "secondary_failure_reasons": [],
        "failure_domain": None,
        "failure_reason_code": None,
    }


def test_oracle_success_with_failed_agent_state_keeps_control_failure():
    result = analyze_report(_report(
        success=True,
        execution_trace=_trace(
            final_status="failed",
            phases=[{
                "event_type": "transition", "to_phase": "failed",
                "reason": "max_iterations_exhausted",
            }],
        ),
    ))

    assert result["failure_stage"] == "iteration_budget_exhausted"
    assert result["failure_domain"] == "control"


def test_runtime_trace_metrics_cover_phase_funnel_and_failures():
    complete = _trace(phases=[{"event_type": "review", "success": True}])
    failed = _trace(
        final_status="failed", failure_stage="iteration_budget_exhausted",
        failure_domain="control", inspected_files=[], edit_attempts=[],
        test_runs=[], changed_files=[],
    )

    metrics = aggregate_runtime_traces([complete, failed])

    assert metrics["tasks_total"] == 2
    assert metrics["retrieval_attempted"] == 2
    assert metrics["edit_attempted"] == 1
    assert metrics["test_passed"] == 1
    assert metrics["review_passed"] == 1
    assert metrics["completed"] == 1
    assert metrics["failure_stages"] == {"iteration_budget_exhausted": 1}
    assert metrics["failure_domains"] == {"control": 1}


def test_task_queue_stats_include_isolated_runtime_trace_aggregate():
    queue = TaskQueue()
    first = Task("one", ".")
    second = Task("two", ".")
    first.execution_trace = _trace()
    second.execution_trace = _trace(
        final_status="failed", failure_stage="environment_failure",
        failure_domain="environment",
    )
    queue._tasks = {first.id: first, second.id: second}

    stats = queue.stats()

    assert stats["trace"]["tasks_total"] == 2
    assert stats["trace"]["failure_stages"] == {"environment_failure": 1}
