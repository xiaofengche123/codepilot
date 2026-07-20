"""
码搭 CodePilot · 上下文裁剪测试

重点验证工具调用配对保护：裁剪后不允许出现孤儿 ToolMessage
（其对应的 AIMessage(tool_calls) 已被裁掉），否则模型 API 会报 400。
"""

from langchain_core.messages import (
    SystemMessage, HumanMessage, AIMessage, ToolMessage,
)

from context_mgr import ContextManager, TRUNCATION_NOTE


def _body(messages):
    """去掉 SystemMessage 后的消息主体。"""
    return [m for m in messages if not isinstance(m, SystemMessage)]


def test_trim_keeps_system_and_recent():
    cm = ContextManager(max_tokens=1000)
    msgs = [SystemMessage(content="sys")] + [
        HumanMessage(content=f"q{i}") for i in range(10)
    ]
    out = cm.trim(msgs)
    assert out[0].content == "sys"
    assert out[-1].content == "q9"


def test_trim_noop_when_within_budget():
    cm = ContextManager(max_tokens=100000)
    msgs = [SystemMessage(content="sys"), HumanMessage(content="hi")]
    out = cm.trim(msgs)
    assert out == msgs


def test_trim_drops_orphan_tool_message():
    """预算只够保留尾部时，开头的孤儿 ToolMessage 必须被丢弃。"""
    cm = ContextManager(max_tokens=50)
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="old question " * 10),
        AIMessage(content="x" * 100, tool_calls=[{"name": "t", "args": {}, "id": "1"}]),
        ToolMessage(content="tool result", tool_call_id="1"),
        HumanMessage(content="latest"),
    ]
    out = cm.trim(msgs)
    body = _body(out)
    assert body, "裁剪后应至少保留最近的用户消息"
    assert not isinstance(body[0], ToolMessage), "孤儿 ToolMessage 未被清理"
    assert body[-1].content == "latest"


def test_trim_keeps_tool_pair_intact():
    """预算充足时 AIMessage(tool_calls) + ToolMessage 配对完整保留。"""
    cm = ContextManager(max_tokens=100000)
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="run something"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "1"}]),
        ToolMessage(content="done", tool_call_id="1"),
        AIMessage(content="final answer"),
    ]
    out = cm.trim(msgs)
    body = _body(out)
    ai_with_tc = [m for m in body if getattr(m, "tool_calls", None)]
    tool_msgs = [m for m in body if isinstance(m, ToolMessage)]
    assert len(ai_with_tc) == 1
    assert len(tool_msgs) == 1
    assert body.index(tool_msgs[0]) == body.index(ai_with_tc[0]) + 1


def test_truncation_note_not_duplicated():
    """多轮 trim 不会累积多条截断提示。"""
    cm = ContextManager(max_tokens=50)
    msgs = [SystemMessage(content="sys")] + [
        HumanMessage(content="x" * 100 + str(i)) for i in range(5)
    ]
    first = cm.trim(msgs)
    second = cm.trim(first)
    notes = [
        m for m in second
        if isinstance(m, SystemMessage) and m.content == TRUNCATION_NOTE
    ]
    assert len(notes) <= 1
