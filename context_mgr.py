"""
码搭 CodePilot · 上下文管理

Token 预算估算 + 滑动窗口裁剪。
"""

from langchain_core.messages import SystemMessage

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
    return int(total / 3 * 1.2)


class ContextManager:
    def __init__(self, max_tokens: int = 8000):
        self.max_tokens = max_tokens

    def trim(self, messages: list) -> list:
        """保留 SystemMessage + 从尾部向前累积到预算上限的消息。"""
        if not messages:
            return messages

        # 分离 SystemMessage
        head = []
        rest = list(messages)
        if isinstance(rest[0], SystemMessage):
            head = [rest.pop(0)]

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

        # 如果有消息被截断，插入提示
        final = list(head)
        dropped = len(rest) - len(kept)
        if dropped > 0:
            final.append(SystemMessage(content=TRUNCATION_NOTE))
        final.extend(kept)

        return final
