"""
码搭 CodePilot · 任务事件模型

记录任务全生命周期事件，支持增量拉取实现实时进度监控。
"""

import time
import threading
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
    sequence: int = 0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "type": self.type,
            "timestamp": self.timestamp,
            "data": self.data,
            "sequence": self.sequence,
        }


class EventBuffer:
    """内存环形缓冲区，按 task_id 存储事件。"""

    def __init__(self, max_total: int = 10000):
        self._events: dict[str, deque[TaskEvent]] = {}
        self._total = 0
        self._max_total = max_total
        self._next_sequence: dict[str, int] = {}
        self._lock = threading.RLock()

    def append(self, event: TaskEvent):
        with self._lock:
            q = self._events.setdefault(event.task_id, deque(maxlen=MAX_EVENTS_PER_TASK))
            if len(q) == q.maxlen:
                self._total -= 1
            next_sequence = self._next_sequence.get(event.task_id, 0) + 1
            self._next_sequence[event.task_id] = next_sequence
            event.sequence = next_sequence
            q.append(event)
            self._total += 1
            if self._total > self._max_total:
                self._drop_oldest()

    def get_since(self, task_id: str, cursor: int = 0) -> tuple[list[dict], int]:
        """按单调递增 sequence 增量拉取，队列淘汰不会让游标错位。"""
        with self._lock:
            q = self._events.get(task_id)
            if not q:
                return [], cursor
            events = [e.to_dict() for e in q if e.sequence > cursor]
            new_cursor = q[-1].sequence if q else cursor
            return events, new_cursor

    def get_all(self, task_id: str) -> list[dict]:
        with self._lock:
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
        with self._lock:
            q = self._events.pop(task_id, None)
            if q:
                self._total -= len(q)
            self._next_sequence.pop(task_id, None)


# 全局单例
_event_buffer: Optional[EventBuffer] = None


def get_event_buffer() -> EventBuffer:
    global _event_buffer
    if _event_buffer is None:
        _event_buffer = EventBuffer()
    return _event_buffer
