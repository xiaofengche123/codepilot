"""
码搭 CodePilot · 上下文管理

Token 预算估算 + 滑动窗口裁剪。
裁剪时保护工具调用配对：不会产生"AIMessage(tool_calls) 被裁掉
但其 ToolMessage 被保留"的孤儿消息（否则模型 API 直接报 400）。
"""

import json

from langchain_core.messages import SystemMessage, ToolMessage

TRUNCATION_NOTE = (
    "[更早的对话已超出上下文窗口被截断，最近的内容已保留。"
    "如需之前的信息，可以询问用户。]"
)


def estimate_tokens(messages: list) -> int:
    """字符级启发式估算：中英文混合按 ~3 字符/token，加 20% 余量。"""
    total = 0
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        total += len(content)
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            total += len(json.dumps(tool_calls, ensure_ascii=False, default=str))
    return int(total / 3 * 1.2)


class ContextManager:
    def __init__(self, max_tokens: int = None):
        if max_tokens is None:
            from config import config
            max_tokens = config.get("agent.max_context_tokens", 8000)
        self.max_tokens = max_tokens

    def trim(self, messages: list) -> list:
        """保留 SystemMessage + 从尾部向前累积到预算上限的消息。"""
        if not messages:
            return messages

        rest = list(messages)

        # 分离头部 SystemMessage（系统提示词）
        head = []
        if isinstance(rest[0], SystemMessage) and rest[0].content != TRUNCATION_NOTE:
            head = [rest.pop(0)]

        # 移除上一轮插入的截断提示，避免每轮 trim 后越积越多
        rest = [
            m for m in rest
            if not (isinstance(m, SystemMessage) and m.content == TRUNCATION_NOTE)
        ]

        # 从尾部向前累积
        kept = []
        current_tokens = estimate_tokens(head)
        for msg in reversed(rest):
            msg_tokens = estimate_tokens([msg])
            if current_tokens + msg_tokens <= self.max_tokens:
                kept.insert(0, msg)
                current_tokens += msg_tokens
            else:
                break

        # 丢弃开头的孤儿 ToolMessage：其对应的 AIMessage(tool_calls)
        # 已被裁掉，保留它们会违反 API 的消息配对约束。
        # kept 是尾部连续片段，所以孤儿 ToolMessage 只会出现在开头。
        while kept and isinstance(kept[0], ToolMessage):
            kept.pop(0)

        # 如果有消息被截断，插入提示
        final = list(head)
        dropped = len(rest) - len(kept)
        if dropped > 0:
            final.append(SystemMessage(content=TRUNCATION_NOTE))
        final.extend(kept)

        return final
