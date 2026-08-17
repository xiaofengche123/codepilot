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
    assert manager.cleaned is True

    events = server._event_buffer.get_all(task.id)
    stream_text = "".join(
        event["data"]["content"]
        for event in events
        if event["type"] == "stream"
    )
    assert stream_text == "hello world"
    assert any(event["type"] == "tool_call" for event in events)
    assert any(event["type"] == "completed" for event in events)
