import pytest

import agent
import model_router
import server
from task_queue import Task


class FakeWorktreeManager:
    def __init__(self, worktree_path):
        self.worktree_path = str(worktree_path)
        self.cleaned = False

    def create(self, task_id):
        return self.worktree_path

    def collect_diff(self, worktree_path):
        return "diff"

    def cleanup(self, worktree_path, task_id):
        self.cleaned = True


class FakeRouter:
    def default_name(self):
        return "deepseek-chat"


class FakeSession:
    created_with = None

    def __init__(self, **kwargs):
        FakeSession.created_with = kwargs
        self.model_unavailable = False
        self.execution_state = type("CompletedState", (), {
            "current_phase": type("Phase", (), {"value": "complete"})(),
            "snapshot": lambda self: {
                "current_phase": "complete",
                "trace": {"schema_version": 1, "final_status": "complete"},
            },
        })()

    def run(self, user_input, on_tool_call=None, on_stream=None):
        on_stream("hello ")
        on_stream("world")
        on_tool_call("read_file", {"path": "a.py"}, "content")
        return "hello world"


@pytest.mark.asyncio
async def test_executor_uses_isolated_model_memory_and_stream_events(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    worktree = tmp_path / "worktree"
    project.mkdir()
    worktree.mkdir()
    manager = FakeWorktreeManager(worktree)
    monkeypatch.setattr(server, "_get_worktree_manager", lambda path: manager)
    monkeypatch.setattr(agent, "AgentSession", FakeSession)
    monkeypatch.setattr(model_router, "get_router", lambda: FakeRouter())

    task = Task("hello", str(project), session_id="session-1")
    result = await server._executor(task)

    assert result == {"answer": "hello world", "diff": "diff"}
    assert FakeSession.created_with["working_dir"] == str(worktree)
    assert FakeSession.created_with["memory_dir"] == str(project)
    assert FakeSession.created_with["session_id"] == "session-1"
    assert FakeSession.created_with["task_id"] == task.id
    assert FakeSession.created_with["model_name"] == "deepseek-chat"
    assert FakeSession.created_with["task_mode"] == "auto"
    assert FakeSession.created_with["confirm"]("run_shell", {"command": "pytest -q"}) is False
    assert manager.cleaned is True

    events = server._event_buffer.get_all(task.id)
    stream_text = "".join(
        event["data"]["content"]
        for event in events
        if event["type"] == "stream"
    )
    assert stream_text == "hello world"
    assert any(event["type"] == "tool_call" for event in events)
    completed = [event for event in events if event["type"] == "completed"]
    assert completed
    assert completed[-1]["data"]["execution_state"]["trace"] == {
        "schema_version": 1,
        "final_status": "complete",
    }


@pytest.mark.asyncio
async def test_executor_surfaces_failed_execution_state_without_weakening_shell_policy(
    tmp_path, monkeypatch
):
    project = tmp_path / "project-failed"
    worktree = tmp_path / "worktree-failed"
    project.mkdir()
    worktree.mkdir()
    manager = FakeWorktreeManager(worktree)
    monkeypatch.setattr(server, "_get_worktree_manager", lambda path: manager)
    monkeypatch.setattr(model_router, "get_router", lambda: FakeRouter())

    class FailedState:
        current_phase = type("Phase", (), {"value": "failed"})()
        terminal_reason = "verification_unavailable"

        def snapshot(self):
            return {"current_phase": "failed", "terminal_reason": self.terminal_reason}

    class RejectedShellSession:
        model_unavailable = False

        def __init__(self, **kwargs):
            self.execution_state = FailedState()
            self.confirm = kwargs["confirm"]

        def run(self, user_input, on_tool_call=None, on_stream=None):
            assert self.confirm("run_shell", {"command": "pytest -q"}) is False
            return "任务未完成：verification_unavailable"

    monkeypatch.setattr(agent, "AgentSession", RejectedShellSession)
    task = Task("fix", str(project), task_mode="mutation_required")

    with pytest.raises(RuntimeError, match="verification_unavailable"):
        await server._executor(task)
    failed = [e for e in server._event_buffer.get_all(task.id) if e["type"] == "failed"]
    assert failed[-1]["data"]["execution_state"] == {
        "current_phase": "failed", "terminal_reason": "verification_unavailable"
    }
    assert task.execution_trace["failure_stage"] == "environment_failure"
    assert task.execution_trace["failure_domain"] == "environment"
    assert manager.cleaned is True


@pytest.mark.asyncio
async def test_executor_classifies_worktree_creation_failure_as_environment(
    tmp_path, monkeypatch
):
    project = tmp_path / "project-no-worktree"
    project.mkdir()
    manager = FakeWorktreeManager(tmp_path / "unused")
    manager.create = lambda _task_id: None
    monkeypatch.setattr(server, "_get_worktree_manager", lambda path: manager)
    task = Task("fix", str(project), task_mode="mutation_required")

    with pytest.raises(RuntimeError, match="Worktree"):
        await server._executor(task)

    assert task.execution_trace["final_status"] == "failed"
    assert task.execution_trace["failure_stage"] == "environment_failure"
    assert task.execution_trace["failure_reason_code"] == "worktree_creation_failed"
