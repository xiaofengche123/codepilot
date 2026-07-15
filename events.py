"""
码搭 CodePilot · 任务事件模型

记录任务全生命周期事件，支持增量拉取实现实时进度监控。
"""

import time
from dataclasses import dataclass, field
from typing import Optional
from collections import deque

MAX_EVENTS_PER_TASK = 500  # 每个任务最多保留 500 条事件


@dataclass
class TaskEvent:
    task_id: str
    type: str  # created, started, tool_call, thinking, completed, failed, cancelled
    timestamp: float = field(default_factory=time.time)
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "type": self.type,
            "timestamp": self.timestamp,
            "data": self.data,
        }


class EventBuffer:
    """内存环形缓冲区，按 task_id 存储事件。"""

    def __init__(self, max_total: int = 10000):
        self._events: dict[str, deque[TaskEvent]] = {}
        self._total = 0
        self._max_total = max_total

    def append(self, event: TaskEvent):
        q = self._events.setdefault(event.task_id, deque(maxlen=MAX_EVENTS_PER_TASK))
        q.append(event)
        self._total += 1
        if self._total > self._max_total:
            self._drop_oldest()

    def get_since(self, task_id: str, cursor: int = 0) -> tuple[list[dict], int]:
        """增量拉取：返回 cursor 之后的事件列表和新 cursor。"""
        q = self._events.get(task_id)
        if not q:
            return [], cursor
        if cursor >= len(q):
            return [], len(q)
        events = [e.to_dict() for e in list(q)[cursor:]]
        return events, len(q)

    def get_all(self, task_id: str) -> list[dict]:
        q = self._events.get(task_id)
        if not q:
            return []
        return [e.to_dict() for e in q]

    def _drop_oldest(self):
        for tid in list(self._events.keys()):
            q = self._events[tid]
            while q and self._total > self._max_total:
                q.popleft()
                self._total -= 1
            if not q:
                del self._events[tid]

    def clear(self, task_id: str):
        q = self._events.pop(task_id, None)
        if q:
            self._total -= len(q)


# 全局单例
_event_buffer: Optional[EventBuffer] = None


def get_event_buffer() -> EventBuffer:
    global _event_buffer
    if _event_buffer is None:
        _event_buffer = EventBuffer()
    return _event_buffer
