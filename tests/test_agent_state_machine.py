import json
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from agent import AgentSession
from execution_state import AgentPhase, PhaseBudgets, TaskMode


def _edit(success=True, error_code=None, revision=1, rolled_back=False):
    return json.dumps({
        "success": success,
        "path": "src/app.py",
        "before_sha256": ("a" if revision == 1 else "b") * 64,
        "after_sha256": (("b" if revision == 1 else "c") * 64) if success else None,
        "rolled_back": rolled_back,
        "error_code": error_code,
    })


def _tool_message(name, call_id, **args):
    call = {"name": name, "args": args, "id": call_id}
    return AIMessage(content="", tool_calls=[call]), [call]


def _session(tmp_path, monkeypatch, responses, results, **kwargs):
    monkeypatch.setattr(AgentSession, "_init_mcp", lambda self: None)

    class FakeLLM:
        def bind_tools(self, _definitions):
            return self

    session = AgentSession(str(tmp_path), task_mode=TaskMode.MUTATION_REQUIRED, **kwargs)
    session._llm = FakeLLM()
    observed = []
    scripted = iter(responses)

    def stream(_llm, messages, _on_stream):
        observed.append(messages)
        return next(scripted)

    session._stream_llm = stream
    queues = {name: iter(values) for name, values in results.items()}
    session._execute_tool = lambda name, _args: next(queues[name])
    return session, observed


def _normal_responses():
    return [
        _tool_message("search_code", "search-1", pattern="bug"),
        _tool_message("read_file", "read-1", path="src/app.py"),
        _tool_message("edit_file_transaction", "edit-1", path="src/app.py"),
        _tool_message("run_shell", "test-1", command="pytest -q"),
        _tool_message("git_diff", "diff-1"),
        (AIMessage(content="fixed"), []),
    ]


def test_normal_mutation_requires_fresh_test_and_diff_review(tmp_path, monkeypatch):
    session, observed = _session(tmp_path, monkeypatch, _normal_responses(), {
        "search_code": ["found"],
        "read_file": ["source"],
        "edit_file_transaction": [_edit()],
        "run_shell": ["passed\n[returncode] 0"],
        "git_diff": ["diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"],
    })

    assert session.run("fix it") == "fixed"
    state = session.execution_state
    assert state.current_phase is AgentPhase.COMPLETE
    assert state.mutation_revision == state.verified_revision == state.reviewed_revision == 1
    assert state.reviewed_files == ["src/app.py"]
    for messages in observed:
        calls = [m for m in messages if getattr(m, "tool_calls", None)]
        for call_message in calls:
            ids = {call["id"] for call in call_message.tool_calls}
            paired = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
            assert ids <= paired or call_message is messages[-1]


def test_sha_conflict_uses_transient_recovery_directive(tmp_path, monkeypatch):
    responses = [
        _tool_message("read_file", "read-1", path="src/app.py"),
        _tool_message("edit_file_transaction", "edit-bad", path="src/app.py"),
        _tool_message("read_file", "read-2", path="src/app.py"),
        _tool_message("edit_file_transaction", "edit-good", path="src/app.py"),
        _tool_message("run_shell", "test-1", command="pytest -q"),
        _tool_message("git_diff", "diff-1"),
        (AIMessage(content="done"), []),
    ]
    session, observed = _session(tmp_path, monkeypatch, responses, {
        "read_file": ["old", "fresh"],
        "edit_file_transaction": [_edit(False, "sha_mismatch"), _edit()],
        "run_shell": ["ok\n[returncode] 0"],
        "git_diff": ["diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py"],
    })

    assert session.run("fix") == "done"
    recovery_call = observed[2]
    controls = [m.content for m in recovery_call[1:] if isinstance(m, SystemMessage)]
    assert any("reread the target" in text for text in controls)
    later_controls = [m.content for m in observed[3][1:] if isinstance(m, SystemMessage)]
    assert not any("reread the target" in text for text in later_controls)
    assert session.execution_state.recovery_count == 1


def test_match_and_python_syntax_failures_recover_with_structured_actions(
    tmp_path, monkeypatch
):
    for error_code, directive_fragment in [
        ("match_count_mismatch", "exact, unique match"),
        ("python_syntax_error", "syntactically valid"),
    ]:
        responses = [
            _tool_message("edit_file_transaction", f"{error_code}-bad", path="src/app.py"),
            _tool_message("edit_file_transaction", f"{error_code}-good", path="src/app.py"),
            _tool_message("run_shell", f"{error_code}-test", command="pytest -q"),
            _tool_message("git_diff", f"{error_code}-diff"),
            (AIMessage(content="done"), []),
        ]
        session, observed = _session(tmp_path, monkeypatch, responses, {
            "edit_file_transaction": [_edit(False, error_code), _edit()],
            "run_shell": ["ok\n[returncode] 0"],
            "git_diff": ["diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py"],
        })
        assert session.run("fix") == "done"
        controls = [m.content for m in observed[1][1:] if isinstance(m, SystemMessage)]
        assert any(directive_fragment in text for text in controls)


def test_failed_test_then_second_edit_invalidates_old_evidence(tmp_path, monkeypatch):
    responses = [
        _tool_message("edit_file_transaction", "edit-1", path="src/app.py"),
        _tool_message("run_shell", "test-bad", command="pytest -q"),
        _tool_message("edit_file_transaction", "edit-2", path="src/app.py"),
        _tool_message("run_shell", "test-good", command="pytest -q"),
        _tool_message("git_diff", "diff-2"),
        (AIMessage(content="done"), []),
    ]
    session, _ = _session(tmp_path, monkeypatch, responses, {
        "edit_file_transaction": [_edit(revision=1), _edit(revision=2)],
        "run_shell": ["failed\n[returncode] 1", "passed\n[returncode] 0"],
        "git_diff": ["diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py"],
    })
    assert session.run("fix") == "done"
    state = session.execution_state
    assert state.mutation_revision == 2
    assert state.verified_revision == state.reviewed_revision == 2


def test_early_answer_requests_missing_diff_instead_of_completing(tmp_path, monkeypatch):
    responses = [
        _tool_message("edit_file_transaction", "edit-1", path="src/app.py"),
        _tool_message("run_shell", "test-1", command="pytest -q"),
        (AIMessage(content="premature"), []),
        _tool_message("git_diff", "diff-1"),
        (AIMessage(content="complete"), []),
    ]
    session, observed = _session(tmp_path, monkeypatch, responses, {
        "edit_file_transaction": [_edit()],
        "run_shell": ["ok\n[returncode] 0"],
        "git_diff": ["diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py"],
    })
    assert session.run("fix") == "complete"
    controls = [m.content for m in observed[3][1:] if isinstance(m, SystemMessage)]
    assert any("call git_diff" in text for text in controls)


def test_explicit_mutation_early_answers_never_complete(tmp_path, monkeypatch):
    responses = [
        (AIMessage(content="claimed fixed"), []),
        (AIMessage(content="still claimed fixed"), []),
    ]
    session, _ = _session(
        tmp_path, monkeypatch, responses, {}, max_iterations=2
    )
    answer = session.run("fix")
    assert "最大执行步数" in answer
    assert session.execution_state.current_phase is AgentPhase.FAILED
    assert session.execution_state.terminal_reason == "max_iterations_exhausted"


def test_trace_explains_review_on_last_iteration_without_final_response(
    tmp_path, monkeypatch
):
    responses = _normal_responses()[:-1]
    session, _ = _session(
        tmp_path,
        monkeypatch,
        responses,
        {
            "search_code": ["found"],
            "read_file": ["source"],
            "edit_file_transaction": [_edit()],
            "run_shell": ["passed\n[returncode] 0"],
            "git_diff": [
                "diff --git a/src/app.py b/src/app.py\n"
                "--- a/src/app.py\n+++ b/src/app.py\n"
            ],
        },
        max_iterations=5,
    )

    assert "最大执行步数" in session.run("fix")
    trace = session.execution_state.trace_snapshot()

    assert trace["final_status"] == "failed"
    assert trace["phases"][-2]["event_type"] == "review"
    assert trace["phases"][-2]["iteration"] == 5
    assert trace["phases"][-2]["success"] is True
    assert trace["phases"][-1]["reason"] == "max_iterations_exhausted"
    assert not any(
        event["event_type"] == "completion_decision"
        for event in trace["phases"]
    )


def test_later_edit_invalidates_old_test_and_review(tmp_path, monkeypatch):
    responses = [
        _tool_message("edit_file_transaction", "edit-1", path="src/app.py"),
        _tool_message("run_shell", "test-1", command="pytest -q"),
        _tool_message("git_diff", "diff-1"),
        _tool_message("edit_file_transaction", "edit-2", path="src/app.py"),
        (AIMessage(content="too early"), []),
        _tool_message("run_shell", "test-2", command="pytest -q"),
        _tool_message("git_diff", "diff-2"),
        (AIMessage(content="done"), []),
    ]
    diff = "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py"
    session, observed = _session(tmp_path, monkeypatch, responses, {
        "edit_file_transaction": [_edit(revision=1), _edit(revision=2)],
        "run_shell": ["ok\n[returncode] 0", "ok\n[returncode] 0"],
        "git_diff": [diff, diff],
    })
    assert session.run("fix") == "done"
    controls = [m.content for m in observed[5][1:] if isinstance(m, SystemMessage)]
    assert any("objective test" in text for text in controls)


def test_recovery_budget_exhaustion_is_terminal(tmp_path, monkeypatch):
    responses = [
        _tool_message("run_shell", "test-1", command="pytest -q"),
        _tool_message("run_shell", "test-2", command="pytest -q"),
    ]
    session, _ = _session(
        tmp_path, monkeypatch, responses,
        {"run_shell": ["bad\n[returncode] 1", "bad\n[returncode] 1"]},
        phase_budgets=PhaseBudgets(recovery_budget=1),
    )
    assert "recovery_budget_exhausted" in session.run("fix")
    assert session.execution_state.terminal_reason == "recovery_budget_exhausted"


def test_multi_tool_budget_and_terminal_rejections_still_pair_all_calls(tmp_path, monkeypatch):
    calls = [
        {"name": "edit_file_transaction", "args": {"path": "x.py"}, "id": "call-1"},
        {"name": "search_code", "args": {"pattern": "x"}, "id": "call-2"},
        {"name": "read_file", "args": {"path": "x.py"}, "id": "call-3"},
    ]
    response = AIMessage(content="", tool_calls=calls)
    session, observed = _session(
        tmp_path, monkeypatch, [(response, calls)],
        {"edit_file_transaction": [_edit(False, "sha_mismatch")]},
        phase_budgets=PhaseBudgets(discovery_budget=0),
    )
    seen = []
    session.run("fix", on_tool_call=lambda name, args, result: seen.append((name, result)))
    assert [name for name, _ in seen] == ["edit_file_transaction", "search_code", "read_file"]
    tool_messages = [m for m in observed[0] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_messages] == ["call-1", "call-2", "call-3"]
    assert json.loads(tool_messages[1].content)["error_code"] == "discovery_budget_exhausted"
    assert json.loads(tool_messages[2].content)["error_code"] == "task_already_terminal"


def test_read_only_and_api_style_shell_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentSession, "_init_mcp", lambda self: None)

    class FakeLLM:
        def bind_tools(self, _definitions):
            return self

    readonly = AgentSession(str(tmp_path), task_mode=TaskMode.READ_ONLY)
    readonly._llm = FakeLLM()
    readonly._stream_llm = lambda *_: (AIMessage(content="answer"), [])
    assert readonly.run("explain") == "answer"
    assert readonly.execution_state.current_phase is AgentPhase.COMPLETE

    responses = [
        _tool_message("edit_file_transaction", "edit", path="src/app.py"),
        _tool_message("run_shell", "test", command="pytest -q"),
    ]
    mutation, _ = _session(tmp_path, monkeypatch, responses, {
        "edit_file_transaction": [_edit()],
        "run_shell": ["[用户取消] 已拒绝执行 run_shell"],
    })
    assert "verification_unavailable" in mutation.run("fix")
    assert mutation.execution_state.verified_revision is None


def test_concurrent_sessions_do_not_share_revision_directive_or_history(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentSession, "_init_mcp", lambda self: None)

    def build(number):
        state = AgentSession(str(tmp_path), session_id=str(number)).execution_state
        state.observe_tool_result(
            "edit_file_transaction", {}, _edit(revision=1)
        )
        if number % 2:
            state.observe_tool_result(
                "run_shell", {"command": "pytest -q"}, "bad\n[returncode] 1"
            )
        return state

    with ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(build, range(4)))
    assert len({id(s.transition_history) for s in states}) == 4
    assert len({id(s.modified_files) for s in states}) == 4
    assert [bool(s.pending_directive) for s in states] == [False, True, False, True]


def test_snapshot_contains_no_raw_directive_or_sensitive_tool_output():
    from execution_state import TaskExecutionState

    state = TaskExecutionState(task_mode=TaskMode.MUTATION_REQUIRED)
    state.observe_tool_result(
        "run_shell", {"command": "pytest -q"},
        "API_KEY=secret .env full source\n[returncode] 1",
    )
    snapshot = json.dumps(state.snapshot())
    assert "secret" not in snapshot
    assert "full source" not in snapshot
    assert "Recovery control" not in snapshot
