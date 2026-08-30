from langchain_core.messages import AIMessage

from agent import AgentSession


def _session(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentSession, "_init_mcp", lambda self: None)
    return AgentSession(str(tmp_path))


def test_model_usage_aggregates_normalized_usage_metadata(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)

    session._record_model_usage(AIMessage(
        content="",
        usage_metadata={
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
        },
    ))
    session._record_model_usage(AIMessage(
        content="",
        response_metadata={
            "token_usage": {"prompt_tokens": 80, "completion_tokens": 20}
        },
    ))

    assert session.model_usage_snapshot() == {
        "model_turns": 2,
        "metered_turns": 2,
        "unmetered_turns": 0,
        "input_tokens": 200,
        "output_tokens": 50,
        "total_tokens": 250,
        "complete": True,
    }


def test_model_usage_marks_missing_provider_metering(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)

    session._record_model_usage(AIMessage(content="no usage"))

    assert session.model_usage_snapshot()["unmetered_turns"] == 1
    assert session.model_usage_snapshot()["complete"] is False


def test_explicit_unavailable_model_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentSession, "_init_mcp", lambda self: None)
    monkeypatch.setattr("model_router.ModelRouter.create", lambda self, _name: None)
    session = AgentSession(str(tmp_path), model_name="missing-model")

    try:
        session.run("do work")
    except RuntimeError as exc:
        assert str(exc) == "requested model is unavailable: missing-model"
    else:
        raise AssertionError("explicit unavailable model silently fell back")
