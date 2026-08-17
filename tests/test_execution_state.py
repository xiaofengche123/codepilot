import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import AgentSession
from execution_state import (
    AgentPhase,
    PhaseBudgets,
    TaskExecutionState,
    TaskMode,
)


def _edit_result(success=True, error_code=None, path="src/app.py", **extra):
    return json.dumps({
        "success": success,
        "path": path,
        "error_code": error_code,
        "rolled_back": extra.get("rolled_back", False),
    })


def _inspected_state(**kwargs):
    state = TaskExecutionState(**kwargs)
    assert state.transition(AgentPhase.DISCOVER, "task_discovered").accepted
    assert state.transition(AgentPhase.INSPECT, "file_read").accepted
    return state


def test_legal_phase_transitions_cover_main_chain():
    state = TaskExecutionState(task_mode=TaskMode.MUTATION_REQUIRED)
    chain = [
        AgentPhase.DISCOVER,
        AgentPhase.INSPECT,
        AgentPhase.PLAN,
        AgentPhase.EDIT,
        AgentPhase.VERIFY,
        AgentPhase.REVIEW,
        AgentPhase.COMPLETE,
    ]
    for phase in chain:
        result = state.transition(phase, "test")
        assert result.accepted is True
    assert state.current_phase is AgentPhase.COMPLETE


def test_illegal_transition_is_rejected_with_error_code():
    state = TaskExecutionState()
    result = state.transition(AgentPhase.REVIEW, "skip_everything")
    assert result.accepted is False
    assert result.error_code == "invalid_phase_transition"
    assert state.current_phase is AgentPhase.INIT


def test_agent_sessions_have_isolated_execution_state(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentSession, "_init_mcp", lambda self: None)
    first = AgentSession(str(tmp_path), session_id="one")
    second = AgentSession(str(tmp_path), session_id="two")

    first.execution_state.observe_tool_result("search_code", {}, "found")

    assert first.execution_state.search_call_count == 1
    assert second.execution_state.search_call_count == 0
    assert first.execution_state is not second.execution_state


def test_search_read_edit_and_verify_events_update_objective_evidence():
    state = TaskExecutionState(task_mode=TaskMode.MUTATION_REQUIRED)
    state.observe_tool_result("search_code", {"pattern": "run"}, "found")
    state.observe_tool_result("read_file", {"path": "src/app.py"}, "source")
    state.observe_tool_result(
        "edit_file_transaction", {"path": "src/app.py"}, _edit_result()
    )
    state.observe_tool_result(
        "run_shell", {"command": "python -m pytest -q"}, "passed\n[returncode] 0"
    )

    assert state.search_call_count == 1
    assert state.read_call_count == 1
    assert state.edit_attempt_count == 1
    assert state.edit_success_count == 1
    assert state.verification_count == 1
    assert state.verification_success_count == 1
    assert state.modified_files == ["src/app.py"]
    assert state.last_test_returncode == 0
    assert state.current_phase is AgentPhase.REVIEW


@pytest.mark.parametrize(
    "error_code", ["sha_mismatch", "match_count_mismatch", "write_failed", "rollback_failed"]
)
def test_transactional_edit_failure_enters_recover(error_code):
    state = TaskExecutionState(task_mode=TaskMode.MUTATION_REQUIRED)
    state.observe_tool_result(
        "edit_file_transaction",
        {"path": "src/app.py"},
        _edit_result(False, error_code, rolled_back=error_code == "rollback_failed"),
    )
    assert state.current_phase is AgentPhase.RECOVER
    assert state.recovery_count == 1
    assert state.last_error_code == error_code


def test_failed_test_enters_recover_and_records_returncode():
    state = _inspected_state(task_mode=TaskMode.MUTATION_REQUIRED)
    state.observe_tool_result("edit_file_transaction", {}, _edit_result())
    state.observe_tool_result(
        "run_shell", {"command": "pytest -q tests/test_app.py"}, "failed\n[returncode] 1"
    )
    assert state.current_phase is AgentPhase.RECOVER
    assert state.last_test_returncode == 1
    assert state.last_error_code == "tests_failed"


def test_recovery_can_return_to_edit_and_verify():
    state = _inspected_state(task_mode=TaskMode.MUTATION_REQUIRED)
    state.observe_tool_result(
        "edit_file_transaction", {}, _edit_result(False, "sha_mismatch")
    )
    state.observe_tool_result("edit_file_transaction", {}, _edit_result())
    assert state.current_phase is AgentPhase.EDIT
    state.observe_tool_result(
        "run_shell", {"command": "pytest -q"}, "ok\n[returncode] 0"
    )
    assert state.current_phase is AgentPhase.REVIEW
    assert state.edit_attempt_count == 2
    assert state.edit_success_count == 1


def test_exhausted_budget_has_explicit_terminal_reason():
    state = TaskExecutionState(
        budgets=PhaseBudgets(discovery_budget=1),
    )
    state.observe_tool_result("search_semantic", {}, "results")
    decision = state.enforce_budget("discovery")
    assert decision.exhausted is True
    assert state.current_phase is AgentPhase.FAILED
    assert state.terminal_reason == "discovery_budget_exhausted"


def test_mutation_task_cannot_complete_without_edit_and_verification_evidence():
    state = _inspected_state(task_mode=TaskMode.MUTATION_REQUIRED)
    result = state.request_completion()
    assert result.accepted is False
    assert result.error_code == "completion_evidence_missing"
    assert state.current_phase is AgentPhase.FAILED
    assert "successful_edit" in state.terminal_reason


def test_read_only_task_can_complete_without_edit():
    state = _inspected_state(task_mode=TaskMode.READ_ONLY)
    result = state.request_completion()
    assert result.accepted is True
    assert state.current_phase is AgentPhase.COMPLETE
    assert state.edit_attempt_count == 0


def test_snapshot_excludes_tool_content_secrets_and_shell_output():
    state = TaskExecutionState(session_id="safe-session")
    state.observe_tool_result(
        "read_file", {"path": ".env"}, "API_KEY=secret-value\n" + "source" * 1000
    )
    state.observe_tool_result(
        "run_shell", {"command": "pytest -q"}, "private-output\n[returncode] 0"
    )
    serialized = json.dumps(state.snapshot())
    assert "secret-value" not in serialized
    assert "private-output" not in serialized
    assert "source" * 10 not in serialized


def test_concurrent_states_do_not_share_counters_or_history():
    def build(number):
        state = TaskExecutionState(task_id=f"task-{number}")
        for _ in range(number):
            state.observe_tool_result("search_code", {}, "found")
        return state

    with ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(build, range(1, 5)))
    assert [state.search_call_count for state in states] == [1, 2, 3, 4]
    assert len({id(state.transition_history) for state in states}) == 4


def test_agent_run_keeps_ai_and_multiple_tool_messages_paired(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentSession, "_init_mcp", lambda self: None)

    class FakeLLM:
        def bind_tools(self, definitions):
            return self

    session = AgentSession(str(tmp_path), session_id="pairing")
    session._llm = FakeLLM()
    observed_messages = []
    responses = iter([
        (
            AIMessage(content="", tool_calls=[
                {"name": "search_code", "args": {"pattern": "x"}, "id": "call-1"},
                {"name": "read_file", "args": {"path": "a.py"}, "id": "call-2"},
            ]),
            [
                {"name": "search_code", "args": {"pattern": "x"}, "id": "call-1"},
                {"name": "read_file", "args": {"path": "a.py"}, "id": "call-2"},
            ],
        ),
        (AIMessage(content="done"), []),
    ])

    def fake_stream(_llm, messages, _on_stream):
        observed_messages.append(list(messages))
        return next(responses)

    monkeypatch.setattr(session, "_stream_llm", fake_stream)
    monkeypatch.setattr(session, "_execute_tool", lambda name, args: f"result:{name}")

    assert session.run("inspect") == "done"
    second_call = observed_messages[1]
    ai_index = next(i for i, item in enumerate(second_call) if getattr(item, "tool_calls", None))
    assert isinstance(second_call[ai_index + 1], ToolMessage)
    assert isinstance(second_call[ai_index + 2], ToolMessage)
    assert second_call[ai_index + 1].tool_call_id == "call-1"
    assert second_call[ai_index + 2].tool_call_id == "call-2"
    assert any(isinstance(item, HumanMessage) for item in second_call)


def test_reused_agent_session_resets_state_for_each_user_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentSession, "_init_mcp", lambda self: None)

    class FakeLLM:
        def bind_tools(self, definitions):
            return self

    session = AgentSession(str(tmp_path))
    session._llm = FakeLLM()
    responses = iter([
        (AIMessage(content="first"), []),
        (AIMessage(content="second"), []),
    ])
    monkeypatch.setattr(session, "_stream_llm", lambda *_args: next(responses))

    assert session.run("one") == "first"
    first_state = session.execution_state
    assert session.run("two") == "second"
    assert session.execution_state is not first_state
    assert session.execution_state.iteration == 1
    assert session.execution_state.search_call_count == 0
