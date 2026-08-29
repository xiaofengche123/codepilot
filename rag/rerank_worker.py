"""Single-thread rerank worker with bounded admission and caller deadlines.

The worker serializes model access without spawning one inference thread per
request.  A caller timeout abandons its result but cannot forcibly interrupt a
running Python/PyTorch call; the single worker finishes that call before taking
the next request. Consecutive model failures open a cooldown circuit with one
recovery probe. Background warmup uses the same worker without blocking startup.
"""

from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from enum import Enum
import math
import threading
import time
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
MAX_RERANK_FAILURE_THRESHOLD = 100
MAX_RERANK_COOLDOWN_SECONDS = 86_400.0


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


class RerankCircuitOpenError(RerankWorkerError):
    reason_code = "rerank_circuit_open"


class RerankRecoveryProbeInProgressError(RerankWorkerError):
    reason_code = "rerank_recovery_probe_in_progress"


class RerankCircuitPhase(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class RerankCircuitSnapshot:
    phase: RerankCircuitPhase
    consecutive_failures: int
    failure_threshold: int
    cooldown_seconds: float
    cooldown_remaining_seconds: float
    probe_in_progress: bool

    def to_dict(self) -> dict[str, str | int | float | bool]:
        return {
            "phase": self.phase.value,
            "consecutive_failures": self.consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,
            "cooldown_remaining_seconds": self.cooldown_remaining_seconds,
            "probe_in_progress": self.probe_in_progress,
        }


@dataclass(frozen=True, slots=True)
class RerankWarmupResult:
    scheduled: bool
    reason_code: str

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "scheduled": self.scheduled,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class RerankWorkerSnapshot:
    state: RerankWorkerState
    queue_size: int
    queue_capacity: int
    queue_closed: bool
    circuit: RerankCircuitSnapshot
    warmup_pending: bool
    thread_alive: bool

    def to_dict(self) -> dict:
        return {
            "state": self.state.to_dict(),
            "queue": {
                "size": self.queue_size,
                "capacity": self.queue_capacity,
                "closed": self.queue_closed,
            },
            "circuit": self.circuit.to_dict(),
            "warmup_pending": self.warmup_pending,
            "thread_alive": self.thread_alive,
        }


@dataclass(slots=True)
class _WorkItem(Generic[T]):
    operation: Callable[[], T] = field(repr=False)
    future: Future[T] = field(default_factory=Future, repr=False)
    recovery_probe: bool = False
    background_warmup: bool = False


class RerankWorker:
    """One lazy-loading model worker backed by a bounded request queue."""

    def __init__(
        self,
        capacity: int,
        loader: Callable[[], object],
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(loader):
            raise TypeError("loader must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._loader = loader
        self._failure_threshold = _validate_failure_threshold(failure_threshold)
        self._cooldown_seconds = _validate_cooldown(cooldown_seconds)
        self._clock = clock
        self._queue: BoundedRerankQueue[_WorkItem[object]] = BoundedRerankQueue(
            capacity
        )
        self._state = RerankWorkerState()
        self._state_lock = threading.Lock()
        self._model_loaded = False
        self._consecutive_failures = 0
        self._circuit_opened_at: float | None = None
        self._probe_in_progress = False
        self._warmup_item: _WorkItem[object] | None = None
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
        recovery_probe = self._admit_circuit()
        item: _WorkItem[T] = _WorkItem(
            operation=operation, recovery_probe=recovery_probe
        )
        offered = self._queue.offer(item)
        if not offered.accepted:
            if recovery_probe:
                self._abandon_probe()
            if offered.reason_code == "rerank_queue_full":
                raise RerankQueueFullError()
            raise RerankWorkerClosedError()
        try:
            return item.future.result(timeout=timeout)
        except FutureTimeout:
            cancelled = item.future.cancel()
            if cancelled and recovery_probe:
                self._abandon_probe()
            raise RerankInferenceTimeoutError() from None

    def state(self) -> RerankWorkerState:
        with self._state_lock:
            return self._state

    def start_warmup(self) -> RerankWarmupResult:
        """Schedule one content-free model load without waiting for it."""
        item: _WorkItem[object] = _WorkItem(
            operation=lambda: None,
            background_warmup=True,
        )
        with self._state_lock:
            if self._model_loaded:
                return RerankWarmupResult(False, "rerank_warmup_not_needed")
            if self._circuit_opened_at is not None:
                return RerankWarmupResult(False, "rerank_warmup_circuit_open")
            if self._warmup_item is not None:
                return RerankWarmupResult(False, "rerank_warmup_already_pending")
            self._warmup_item = item

        offered = self._queue.offer(item)
        if offered.accepted:
            return RerankWarmupResult(True, "rerank_warmup_scheduled")

        self._finish_warmup(item)
        reason_code = (
            "rerank_warmup_queue_full"
            if offered.reason_code == "rerank_queue_full"
            else "rerank_warmup_worker_closed"
        )
        return RerankWarmupResult(False, reason_code)

    def queue_snapshot(self):
        return self._queue.snapshot()

    def circuit_snapshot(self) -> RerankCircuitSnapshot:
        with self._state_lock:
            return self._circuit_snapshot_unlocked()

    def runtime_snapshot(self) -> RerankWorkerSnapshot:
        """Return one bounded snapshot without request or model content."""
        with self._state_lock:
            state = self._state
            circuit = self._circuit_snapshot_unlocked()
            warmup_pending = self._warmup_item is not None
        queue = self._queue.snapshot()
        return RerankWorkerSnapshot(
            state=state,
            queue_size=queue.size,
            queue_capacity=queue.capacity,
            queue_closed=queue.closed,
            circuit=circuit,
            warmup_pending=warmup_pending,
            thread_alive=self._thread.is_alive(),
        )

    def close(self, *, wait: bool = False, timeout: float | None = None) -> bool:
        """Stop admission and optionally wait for queued/running work to finish."""
        if not isinstance(wait, bool):
            raise TypeError("wait must be a boolean")
        join_timeout = _validate_optional_join_timeout(timeout)
        with self._state_lock:
            warmup_item = self._warmup_item
        if warmup_item is not None and warmup_item.future.cancel():
            self._finish_warmup(warmup_item)
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
                    if item.background_warmup:
                        self._finish_warmup(item)
                    continue
                try:
                    rejection = self._execution_rejection(item.recovery_probe)
                    if rejection is not None:
                        item.future.set_exception(rejection)
                        continue
                    try:
                        self._ensure_loaded(item.recovery_probe)
                        result = item.operation()
                    except RerankWorkerError as exc:
                        item.future.set_exception(exc)
                        continue
                    except BaseException as exc:
                        self._record_inference_failure(item.recovery_probe)
                        wrapped = RerankInferenceError()
                        wrapped.__cause__ = exc
                        item.future.set_exception(wrapped)
                        continue
                    self._record_success()
                    item.future.set_result(result)
                finally:
                    if item.background_warmup:
                        self._finish_warmup(item)
        finally:
            self._mark_unloaded()

    def _ensure_loaded(self, recovery_probe: bool) -> None:
        with self._state_lock:
            if self._model_loaded:
                return
            if not recovery_probe:
                self._state = transition_rerank_worker(
                    self._state, RerankWorkerEvent.START_LOAD, "worker_lazy_load"
                )
        started = time.perf_counter()
        try:
            self._loader()
        except BaseException as exc:
            self._record_load_failure(recovery_probe)
            wrapped = RerankModelLoadError()
            wrapped.__cause__ = exc
            raise wrapped
        finally:
            from rag.runtime_metrics import observe_model_load

            observe_model_load(time.perf_counter() - started)
        with self._state_lock:
            self._model_loaded = True
            if not recovery_probe:
                self._state = transition_rerank_worker(
                    self._state, RerankWorkerEvent.LOAD_SUCCEEDED, "model_loaded"
                )

    def _record_load_failure(self, recovery_probe: bool) -> None:
        with self._state_lock:
            self._model_loaded = False
            self._consecutive_failures += 1
            if recovery_probe:
                self._reopen_after_probe_unlocked("recovery_probe_load_failed")
                return
            self._state = transition_rerank_worker(
                self._state, RerankWorkerEvent.LOAD_FAILED, "model_load_failed"
            )
            if self._consecutive_failures >= self._failure_threshold:
                self._open_circuit_unlocked()

    def _record_inference_failure(self, recovery_probe: bool) -> None:
        with self._state_lock:
            self._consecutive_failures += 1
            if self._state.phase in {
                RerankWorkerPhase.READY,
                RerankWorkerPhase.DEGRADED,
            }:
                self._state = transition_rerank_worker(
                    self._state,
                    RerankWorkerEvent.INFERENCE_FAILED,
                    "inference_failed",
                )
            if recovery_probe:
                self._reopen_after_probe_unlocked("recovery_probe_failed")
            elif self._consecutive_failures >= self._failure_threshold:
                self._state = transition_rerank_worker(
                    self._state,
                    RerankWorkerEvent.FAILURE_LIMIT_REACHED,
                    "circuit_failure_limit",
                )
                self._open_circuit_unlocked()

    def _record_success(self) -> None:
        with self._state_lock:
            if self._state.phase is RerankWorkerPhase.DEGRADED:
                self._state = transition_rerank_worker(
                    self._state,
                    RerankWorkerEvent.INFERENCE_SUCCEEDED,
                    "inference_recovered",
                )
            self._consecutive_failures = 0
            self._circuit_opened_at = None
            self._probe_in_progress = False
            self._warmup_item = None

    def _finish_warmup(self, item: _WorkItem[object]) -> None:
        with self._state_lock:
            if self._warmup_item is item:
                self._warmup_item = None

    def _admit_circuit(self) -> bool:
        with self._state_lock:
            if self._circuit_opened_at is None:
                return False
            if self._probe_in_progress:
                raise RerankRecoveryProbeInProgressError()
            if self._cooldown_remaining_unlocked() > 0.0:
                raise RerankCircuitOpenError()
            self._probe_in_progress = True
            self._state = transition_rerank_worker(
                self._state,
                RerankWorkerEvent.START_RECOVERY_PROBE,
                "recovery_probe_started",
            )
            return True

    def _execution_rejection(
        self, recovery_probe: bool
    ) -> RerankWorkerError | None:
        with self._state_lock:
            if recovery_probe:
                return None
            if self._circuit_opened_at is not None:
                return RerankCircuitOpenError()
            return None

    def _abandon_probe(self) -> None:
        with self._state_lock:
            if not self._probe_in_progress:
                return
            self._probe_in_progress = False
            if self._state.phase is RerankWorkerPhase.DEGRADED:
                self._state = transition_rerank_worker(
                    self._state,
                    RerankWorkerEvent.FAILURE_LIMIT_REACHED,
                    "recovery_probe_abandoned",
                )

    def _reopen_after_probe_unlocked(self, reason_code: str) -> None:
        self._probe_in_progress = False
        self._circuit_opened_at = self._clock()
        if self._state.phase is RerankWorkerPhase.DEGRADED:
            self._state = transition_rerank_worker(
                self._state,
                RerankWorkerEvent.FAILURE_LIMIT_REACHED,
                reason_code,
            )

    def _open_circuit_unlocked(self) -> None:
        self._circuit_opened_at = self._clock()
        self._probe_in_progress = False

    def _cooldown_remaining_unlocked(self) -> float:
        if self._circuit_opened_at is None:
            return 0.0
        elapsed = max(0.0, self._clock() - self._circuit_opened_at)
        return max(0.0, self._cooldown_seconds - elapsed)

    def _circuit_snapshot_unlocked(self) -> RerankCircuitSnapshot:
        if self._circuit_opened_at is None:
            phase = RerankCircuitPhase.CLOSED
        elif self._probe_in_progress:
            phase = RerankCircuitPhase.HALF_OPEN
        else:
            phase = RerankCircuitPhase.OPEN
        return RerankCircuitSnapshot(
            phase=phase,
            consecutive_failures=self._consecutive_failures,
            failure_threshold=self._failure_threshold,
            cooldown_seconds=self._cooldown_seconds,
            cooldown_remaining_seconds=self._cooldown_remaining_unlocked(),
            probe_in_progress=self._probe_in_progress,
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
            self._model_loaded = False
            self._consecutive_failures = 0
            self._circuit_opened_at = None
            self._probe_in_progress = False


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


def _validate_failure_threshold(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("failure_threshold must be an integer")
    if value <= 0 or value > MAX_RERANK_FAILURE_THRESHOLD:
        raise ValueError(
            "failure_threshold must be between 1 and "
            f"{MAX_RERANK_FAILURE_THRESHOLD}"
        )
    return value


def _validate_cooldown(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("cooldown_seconds must be a number")
    normalized = float(value)
    if (
        not math.isfinite(normalized)
        or normalized <= 0.0
        or normalized > MAX_RERANK_COOLDOWN_SECONDS
    ):
        raise ValueError(
            "cooldown_seconds must be positive, finite, and no greater than "
            f"{MAX_RERANK_COOLDOWN_SECONDS:g}"
        )
    return normalized
