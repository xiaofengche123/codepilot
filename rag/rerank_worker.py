"""Single-thread rerank worker with bounded admission and caller deadlines.

The worker serializes model access without spawning one inference thread per
request.  A caller timeout abandons its result but cannot forcibly interrupt a
running Python/PyTorch call; the single worker finishes that call before taking
the next request.  Circuit breaking and warmup are intentionally deferred.
"""

from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
import math
import threading
from typing import Callable, Generic, TypeVar

from rag.rerank_request_queue import BoundedRerankQueue, RerankQueueClosed
from rag.rerank_worker_state import (
    RerankWorkerEvent,
    RerankWorkerPhase,
    RerankWorkerState,
    transition_rerank_worker,
)


T = TypeVar("T")
MAX_RERANK_DEADLINE_SECONDS = 3_600.0


class RerankWorkerError(RuntimeError):
    reason_code = "rerank_worker_error"

    def __init__(self) -> None:
        super().__init__(self.reason_code)


class RerankQueueFullError(RerankWorkerError):
    reason_code = "rerank_queue_full"


class RerankWorkerClosedError(RerankWorkerError):
    reason_code = "rerank_queue_closed"


class RerankInferenceTimeoutError(RerankWorkerError):
    reason_code = "rerank_timeout"


class RerankModelLoadError(RerankWorkerError):
    reason_code = "rerank_load_error"


class RerankInferenceError(RerankWorkerError):
    reason_code = "rerank_inference_error"


@dataclass(slots=True)
class _WorkItem(Generic[T]):
    operation: Callable[[], T] = field(repr=False)
    future: Future[T] = field(default_factory=Future, repr=False)


class RerankWorker:
    """One lazy-loading model worker backed by a bounded request queue."""

    def __init__(self, capacity: int, loader: Callable[[], object]) -> None:
        if not callable(loader):
            raise TypeError("loader must be callable")
        self._loader = loader
        self._queue: BoundedRerankQueue[_WorkItem[object]] = BoundedRerankQueue(
            capacity
        )
        self._state = RerankWorkerState()
        self._state_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run,
            name="rerank-worker",
            daemon=True,
        )
        self._thread.start()

    def submit(self, operation: Callable[[], T], timeout_seconds: float) -> T:
        """Submit one operation and wait no longer than the caller deadline."""
        if not callable(operation):
            raise TypeError("operation must be callable")
        timeout = _validate_deadline(timeout_seconds)
        item: _WorkItem[T] = _WorkItem(operation=operation)
        offered = self._queue.offer(item)
        if not offered.accepted:
            if offered.reason_code == "rerank_queue_full":
                raise RerankQueueFullError()
            raise RerankWorkerClosedError()
        try:
            return item.future.result(timeout=timeout)
        except FutureTimeout:
            item.future.cancel()
            raise RerankInferenceTimeoutError() from None

    def state(self) -> RerankWorkerState:
        with self._state_lock:
            return self._state

    def queue_snapshot(self):
        return self._queue.snapshot()

    def close(self, *, wait: bool = False, timeout: float | None = None) -> bool:
        """Stop admission and optionally wait for queued/running work to finish."""
        if not isinstance(wait, bool):
            raise TypeError("wait must be a boolean")
        join_timeout = _validate_optional_join_timeout(timeout)
        self._queue.close()
        if wait and threading.current_thread() is not self._thread:
            self._thread.join(join_timeout)
        return not self._thread.is_alive()

    def _run(self) -> None:
        try:
            while True:
                try:
                    item = self._queue.take()
                except RerankQueueClosed:
                    return
                if not item.future.set_running_or_notify_cancel():
                    continue
                try:
                    self._ensure_loaded()
                    result = item.operation()
                except RerankWorkerError as exc:
                    item.future.set_exception(exc)
                    continue
                except BaseException as exc:
                    self._mark_inference_failed()
                    wrapped = RerankInferenceError()
                    wrapped.__cause__ = exc
                    item.future.set_exception(wrapped)
                    continue
                self._mark_inference_succeeded()
                item.future.set_result(result)
        finally:
            self._mark_unloaded()

    def _ensure_loaded(self) -> None:
        with self._state_lock:
            if self._state.phase in {
                RerankWorkerPhase.READY,
                RerankWorkerPhase.DEGRADED,
            }:
                return
            self._state = transition_rerank_worker(
                self._state, RerankWorkerEvent.START_LOAD, "worker_lazy_load"
            )
        try:
            self._loader()
        except BaseException as exc:
            with self._state_lock:
                self._state = transition_rerank_worker(
                    self._state, RerankWorkerEvent.LOAD_FAILED, "model_load_failed"
                )
            wrapped = RerankModelLoadError()
            wrapped.__cause__ = exc
            raise wrapped
        with self._state_lock:
            self._state = transition_rerank_worker(
                self._state, RerankWorkerEvent.LOAD_SUCCEEDED, "model_loaded"
            )

    def _mark_inference_failed(self) -> None:
        with self._state_lock:
            if self._state.phase in {
                RerankWorkerPhase.READY,
                RerankWorkerPhase.DEGRADED,
            }:
                self._state = transition_rerank_worker(
                    self._state,
                    RerankWorkerEvent.INFERENCE_FAILED,
                    "inference_failed",
                )

    def _mark_inference_succeeded(self) -> None:
        with self._state_lock:
            if self._state.phase is RerankWorkerPhase.DEGRADED:
                self._state = transition_rerank_worker(
                    self._state,
                    RerankWorkerEvent.INFERENCE_SUCCEEDED,
                    "inference_recovered",
                )

    def _mark_unloaded(self) -> None:
        with self._state_lock:
            if self._state.phase in {
                RerankWorkerPhase.READY,
                RerankWorkerPhase.DEGRADED,
                RerankWorkerPhase.FAILED,
            }:
                self._state = transition_rerank_worker(
                    self._state, RerankWorkerEvent.UNLOAD, "worker_closed"
                )


def _validate_deadline(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout_seconds must be a number")
    normalized = float(value)
    if (
        not math.isfinite(normalized)
        or normalized <= 0.0
        or normalized > MAX_RERANK_DEADLINE_SECONDS
    ):
        raise ValueError(
            "timeout_seconds must be positive, finite, and no greater than "
            f"{MAX_RERANK_DEADLINE_SECONDS:g}"
        )
    return normalized


def _validate_optional_join_timeout(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout must be a number or None")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValueError("timeout must be finite and non-negative")
    return normalized
