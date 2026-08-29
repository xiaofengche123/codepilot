"""Bounded, thread-safe request queue for the M6 rerank worker.

Producers never block: an offer is accepted immediately or returns a stable
backpressure reason.  The queue treats requests as opaque objects and exposes
only content-free snapshots.  RRF fallback and inference deadlines belong to
MODEL-003, not to this transport primitive.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import threading
import time
from typing import Any, Generic, TypeVar


T = TypeVar("T")
MAX_RERANK_QUEUE_CAPACITY = 10_000
MAX_RERANK_QUEUE_WAIT_SECONDS = 86_400.0


class RerankQueueClosed(RuntimeError):
    """Raised when a closed and drained queue cannot provide another item."""

    error_code = "rerank_queue_closed"

    def __init__(self) -> None:
        super().__init__(self.error_code)


class RerankQueueEmpty(TimeoutError):
    """Raised when no item becomes available within the consumer wait bound."""

    error_code = "rerank_queue_empty"

    def __init__(self) -> None:
        super().__init__(self.error_code)


@dataclass(frozen=True, slots=True)
class RerankQueueOffer:
    accepted: bool
    reason_code: str
    size: int
    capacity: int

    def to_dict(self) -> dict[str, bool | str | int]:
        return {
            "accepted": self.accepted,
            "reason_code": self.reason_code,
            "size": self.size,
            "capacity": self.capacity,
        }


@dataclass(frozen=True, slots=True)
class RerankQueueSnapshot:
    size: int
    capacity: int
    closed: bool

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "size": self.size,
            "capacity": self.capacity,
            "closed": self.closed,
        }


class BoundedRerankQueue(Generic[T]):
    """FIFO queue with non-blocking producers and interruptible consumers."""

    def __init__(self, capacity: int) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise TypeError("capacity must be an integer")
        if capacity <= 0 or capacity > MAX_RERANK_QUEUE_CAPACITY:
            raise ValueError(
                f"capacity must be between 1 and {MAX_RERANK_QUEUE_CAPACITY}"
            )
        self._capacity = capacity
        self._items: deque[T] = deque()
        self._closed = False
        self._condition = threading.Condition()

    @property
    def capacity(self) -> int:
        return self._capacity

    def offer(self, item: T) -> RerankQueueOffer:
        """Try to enqueue one request without blocking the producer."""
        if item is None:
            raise TypeError("item must not be None")
        with self._condition:
            if self._closed:
                return self._offer_result(False, "rerank_queue_closed")
            if len(self._items) >= self._capacity:
                return self._offer_result(False, "rerank_queue_full")
            self._items.append(item)
            result = self._offer_result(True, "rerank_queue_accepted")
            self._condition.notify()
            return result

    def take(self, timeout: float | None = None) -> T:
        """Take the oldest request, waiting until available, closed, or timed out."""
        normalized_timeout = _validate_timeout(timeout)
        deadline = (
            None
            if normalized_timeout is None
            else time.monotonic() + normalized_timeout
        )
        with self._condition:
            while not self._items:
                if self._closed:
                    raise RerankQueueClosed()
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise RerankQueueEmpty()
                self._condition.wait(remaining)
            return self._items.popleft()

    def close(self) -> RerankQueueSnapshot:
        """Reject new offers, preserve queued work, and wake waiting consumers."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            return self._snapshot_unlocked()

    def snapshot(self) -> RerankQueueSnapshot:
        with self._condition:
            return self._snapshot_unlocked()

    def __len__(self) -> int:
        with self._condition:
            return len(self._items)

    def _offer_result(self, accepted: bool, reason_code: str) -> RerankQueueOffer:
        return RerankQueueOffer(
            accepted=accepted,
            reason_code=reason_code,
            size=len(self._items),
            capacity=self._capacity,
        )

    def _snapshot_unlocked(self) -> RerankQueueSnapshot:
        return RerankQueueSnapshot(
            size=len(self._items),
            capacity=self._capacity,
            closed=self._closed,
        )


def _validate_timeout(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("timeout must be a number or None")
    normalized = float(timeout)
    if (
        not math.isfinite(normalized)
        or normalized < 0.0
        or normalized > MAX_RERANK_QUEUE_WAIT_SECONDS
    ):
        raise ValueError(
            "timeout must be finite, non-negative, and no greater than "
            f"{MAX_RERANK_QUEUE_WAIT_SECONDS:g} seconds"
        )
    return normalized
