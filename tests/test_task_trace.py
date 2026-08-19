import json

from execution_state import AgentPhase, TaskExecutionState, TaskMode
from task_trace import (
    MAX_PHASE_EVENTS,
    TRACE_SCHEMA_VERSION,
    safe_trace_path,
)


def _edit(path="src/app.py"):
    return json.dumps({
        "success": True,
        "path": path,
        "before_sha256": "a" * 64,
        "after_sha256": "b" * 64,
        "rolled_back": False,
        "error_code": None,
    })


def test_trace_records_phase_retrieval_edit_test_and_completion():
    state = TaskExecutionState(
        task_id="task-trace", task_mode=TaskMode.MUTATION_REQUIRED
    )
    state.begin_iteration(1)
    state.observe_tool_result("search_code", {"pattern": "bug"}, "found")
    state.observe_tool_result("read_file", {"path": "src/app.py"}, "source")
    state.observe_tool_result(
        "edit_file_transaction", {"path": "src/app.py"}, _edit()
    )
    state.observe_tool_result(
        "run_shell", {"command": "pytest -q"}, "passed\n[returncode] 0"
    )
    state.observe_tool_result(
        "git_diff", {},
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n",
    )
    assert state.request_completion().accepted

    trace = state.trace_snapshot()

    assert trace["schema_version"] == TRACE_SCHEMA_VERSION
    assert trace["task_id"] == "task-trace"
    assert trace["final_status"] == "complete"
    assert trace["failure_stage"] is None
    retrieval = dict(trace["retrieval_calls"][0])
    assert isinstance(retrieval.pop("timestamp"), float)
    assert retrieval == {
        "tool_name": "search_code",
        "iteration": 1,
        "success": True,
        "error_code": None,
        "result_files": [],
    }
    assert trace["inspected_files"] == ["src/app.py"]
    assert trace["changed_files"] == ["src/app.py"]
    edit = dict(trace["edit_attempts"][0])
    assert isinstance(edit.pop("timestamp"), float)
    assert edit == {
        "tool_name": "edit_file_transaction",
        "path": "src/app.py",
        "iteration": 1,
        "success": True,
        "byte_changed": True,
        "rolled_back": False,
        "legacy": False,
        "revision": 1,
        "error_code": None,
    }
    test = dict(trace["test_runs"][0])
    assert isinstance(test.pop("timestamp"), float)
    assert test == {
        "tool_name": "run_shell",
        "iteration": 1,
        "returncode": 0,
        "success": True,
        "revision": 1,
        "error_code": None,
    }
    event_types = [event["event_type"] for event in trace["phases"]]
    assert event_types[0] == "iteration_started"
    assert "transition" in event_types
    assert event_types[-2:] == ["completion_decision", "transition"]
    assert trace["phases"][-2]["completion_allowed"] is True


def test_trace_distinguishes_last_tool_evidence_from_completion_decision():
    state = TaskExecutionState(
        task_id="last-tool", task_mode=TaskMode.MUTATION_REQUIRED,
        max_iterations=1,
    )
    state.begin_iteration(1)
    state.observe_tool_result("edit_file_transaction", {}, _edit())
    state.observe_tool_result(
        "run_shell", {"command": "pytest -q"}, "ok\n[returncode] 0"
    )
    state.observe_tool_result(
        "git_diff", {},
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n",
    )
    state.fail("max_iterations_exhausted")

    trace = state.trace_snapshot()
    assert trace["final_status"] == "failed"
    assert trace["phases"][-1]["event_type"] == "transition"
    assert trace["phases"][-1]["from_phase"] == "review"
    assert trace["phases"][-1]["to_phase"] == "failed"
    assert trace["phases"][-1]["reason"] == "max_iterations_exhausted"
    assert trace["phases"][-2]["event_type"] == "review"
    assert trace["phases"][-2]["success"] is True
    assert not any(
        event["event_type"] == "completion_decision" for event in trace["phases"]
    )


def test_trace_does_not_store_queries_content_output_or_sensitive_paths():
    secret = "sk-" + "secret-value"
    state = TaskExecutionState(task_id="redacted")
    state.begin_iteration(1)
    state.observe_tool_result("search_semantic", {"query": secret}, secret)
    state.observe_tool_result("read_file", {"path": ".env"}, f"KEY={secret}")
    state.observe_tool_result("edit_file_transaction", {}, _edit(".env"))
    state.observe_tool_result(
        "run_shell", {"command": "pytest -q"}, f"{secret}\n[returncode] 1"
    )

    serialized = json.dumps(state.trace_snapshot())
    assert secret not in serialized
    assert "KEY=" not in serialized
    assert ".env" not in serialized
    assert "[sensitive_path]" in serialized


def test_trace_is_isolated_between_execution_states():
    first = TaskExecutionState(task_id="first")
    second = TaskExecutionState(task_id="second")
    first.begin_iteration(1)
    first.observe_tool_result("search_code", {}, "found")

    assert len(first.trace_snapshot()["retrieval_calls"]) == 1
    assert second.trace_snapshot()["retrieval_calls"] == []


def test_trace_redaction_and_limits_cover_identifiers_paths_and_event_history():
    secret = "sk-" + "x" * 80
    state = TaskExecutionState(task_id=secret, session_id=f"Bearer {secret}")
    for iteration in range(1, MAX_PHASE_EVENTS + 25):
        state.begin_iteration(iteration)
    state.transition(AgentPhase.DISCOVER, f"reason:{secret}")
    state.observe_tool_result(
        "read_file", {"path": f"secrets/{secret}/credentials.json"}, "source"
    )

    snapshot = state.trace_snapshot()
    serialized = json.dumps(snapshot)
    assert secret not in serialized
    assert len(snapshot["phases"]) == MAX_PHASE_EVENTS
    assert snapshot["task_id"] == "[redacted]"
    assert snapshot["session_id"] == "[redacted]"
    assert snapshot["inspected_files"] == ["[sensitive_path]"]
    assert len(safe_trace_path("a" * 2000)) <= 500


def test_retrieval_trace_extracts_only_bounded_file_paths_not_snippets():
    secret = "sk-" + "private"
    state = TaskExecutionState(task_id="retrieval-files")
    state.begin_iteration(1)
    state.observe_tool_result(
        "search_code",
        {"pattern": "secret"},
        "找到 2 条结果:\n"
        f"  src/app.py:12: API_KEY={secret}\n"
        "  tests/test_app.py:8: assert True",
    )

    trace = state.trace_snapshot()["retrieval_calls"][0]
    assert trace["result_files"] == ["src/app.py", "tests/test_app.py"]
    assert secret not in json.dumps(trace)
