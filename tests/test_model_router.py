import model_router


def test_qwen_flash_is_listed_without_changing_existing_priority(monkeypatch):
    for name in (
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test-key")

    router = model_router.ModelRouter()

    assert router.default_name() == "deepseek-chat"
    qwen = next(item for item in router.list_models() if item["name"] == "qwen3.7-flash")
    assert qwen == {
        "name": "qwen3.7-flash",
        "provider": "qwen",
        "cost_tier": "budget",
        "display_name": "Qwen 3.7 Flash",
        "available": True,
        "current": False,
    }


def test_qwen_flash_uses_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test-key")
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(model_router, "ChatOpenAI", FakeChatOpenAI)

    llm = model_router.ModelRouter().create("qwen3.7-flash")

    assert isinstance(llm, FakeChatOpenAI)
    assert captured["model"] == "qwen3.7-flash"
    assert captured["api_key"] == "dashscope-test-key"
    assert captured["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_qwen_flash_respects_custom_endpoint(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://workspace.example/compatible-mode/v1")
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(model_router, "ChatOpenAI", FakeChatOpenAI)

    model_router.ModelRouter().create("qwen3.7-flash")

    assert captured["base_url"] == "https://workspace.example/compatible-mode/v1"
