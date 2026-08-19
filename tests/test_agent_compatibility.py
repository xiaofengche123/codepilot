import ast
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

import agent
from agent import AgentSession


def test_legacy_agent_run_positional_arguments_remain_compatible(tmp_path, monkeypatch):
    captured = {}

    class FakeSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, user_input, on_tool_call=None, on_stream=None):
            captured["run"] = (user_input, on_tool_call, on_stream)
            return "ok"

    monkeypatch.setattr(agent, "AgentSession", FakeSession)
    callback = lambda *args: None
    stream = lambda chunk: None
    assert agent.run("hello", str(tmp_path), callback, stream) == "ok"
    assert captured["run"] == ("hello", callback, stream)
    assert captured["task_mode"].value == "auto"


def test_agent_run_accepts_optional_explicit_task_mode(tmp_path, monkeypatch):
    captured = {}

    class FakeSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, *_args, **_kwargs):
            return "ok"

    monkeypatch.setattr(agent, "AgentSession", FakeSession)
    assert agent.run("fix", str(tmp_path), task_mode="mutation_required") == "ok"
    assert captured["task_mode"] == "mutation_required"


def test_stream_fallback_does_not_repeat_already_emitted_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentSession, "_init_mcp", lambda self: None)
    session = AgentSession(str(tmp_path))

    class PartialThenFallback:
        def stream(self, _messages):
            yield AIMessageChunk(content="hello ")
            raise RuntimeError("stream interrupted")

        def invoke(self, _messages):
            return AIMessage(content="hello world")

    chunks = []
    response, calls = session._stream_llm(
        PartialThenFallback(), [], chunks.append
    )
    assert "".join(chunks) == "hello world"
    assert response.content == "hello world"
    assert calls == []


def test_model_failure_propagation_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentSession, "_init_mcp", lambda self: None)
    session = AgentSession(str(tmp_path))

    class BrokenModel:
        def stream(self, _messages):
            raise RuntimeError("stream failed")
            yield

        def invoke(self, _messages):
            raise RuntimeError("invoke failed")

    with pytest.raises(RuntimeError, match="invoke failed"):
        session._stream_llm(BrokenModel(), [], None)


def test_eval_worker_explicitly_marks_known_fix_tasks_as_mutations():
    path = Path(__file__).parent.parent / ".rag-eval" / "agent_eval_worker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "AgentSession"
    ]
    assert len(calls) == 1
    values = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    assert ast.literal_eval(values["task_mode"]) == "mutation_required"
