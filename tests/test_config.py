"""
码搭 CodePilot · 配置管理测试
"""

import pytest
from config import Config, config


class TestConfig:
    def test_defaults_loaded(self):
        assert config.get("agent.max_iterations") == 10
        assert config.get("model.temperature") == 0.3
        assert config.get("rag.chunk_lines") == 30

    def test_dot_path_access(self):
        assert config.get("agent.max_context_tokens") == 8000
        assert config.get("tools.shell_timeout") == 30

    def test_nonexistent_key_returns_default(self):
        assert config.get("nonexistent.key") is None
        assert config.get("nonexistent.key", "fallback") == "fallback"

    def test_get_all_returns_dict(self):
        data = config.get_all()
        assert isinstance(data, dict)
        assert "agent" in data
        assert "model" in data
        assert "tools" in data
        assert "rag" in data

    def test_reload_works(self):
        config.reload()
        assert config.get("agent.max_iterations") == 10


class TestContextManager:
    def test_estimate_tokens(self):
        from context_mgr import estimate_tokens
        from langchain_core.messages import HumanMessage, AIMessage
        msgs = [HumanMessage(content="hello"), AIMessage(content="world")]
        tokens = estimate_tokens(msgs)
        assert tokens > 0

    def test_trim_preserves_system_message(self):
        from context_mgr import ContextManager
        from langchain_core.messages import HumanMessage, SystemMessage
        msgs = [SystemMessage(content="you are a bot"), HumanMessage(content="hi")]
        cm = ContextManager(max_tokens=200000)
        trimmed = cm.trim(msgs)
        assert isinstance(trimmed[0], SystemMessage)
        assert len(trimmed) == 2

    def test_trim_drops_old_messages(self):
        from context_mgr import ContextManager
        from langchain_core.messages import HumanMessage, SystemMessage
        # 消息 1: system, 消息 2: 超长(会截断), 消息 3: 短消息会保留
        msgs = [
            SystemMessage(content="bot"),
            HumanMessage(content="b" * 15000),
            HumanMessage(content="keep me"),
        ]
        cm = ContextManager(max_tokens=2000)
        trimmed = cm.trim(msgs)
        # 应该包含截断提示
        contents = [m.content for m in trimmed]
        assert any("截断" in c or "trimmed" in c.lower() for c in contents)
        # "keep me" 应该被保留
        assert "keep me" in contents[-1]
