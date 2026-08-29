"""Single-model worker, deadline, backpressure, and circuit tests."""

from dataclasses import FrozenInstanceError
import json
import threading
import time

import pytest

from rag.rerank_worker import (
    MAX_RERANK_COOLDOWN_SECONDS,
    MAX_RERANK_DEADLINE_SECONDS,
    MAX_RERANK_FAILURE_THRESHOLD,
    RerankCircuitOpenError,
    RerankCircuitPhase,
    RerankInferenceError,
    RerankInferenceTimeoutError,
    RerankModelLoadError,
    RerankQueueFullError,
    RerankRecoveryProbeInProgressError,
    RerankWorker,
    RerankWorkerClosedError,
    RerankWorkerError,
    RerankWarmupResult,
)
from rag.rerank_worker_state import RerankWorkerPhase
from rag.runtime_metrics import metrics_snapshot, reset_metrics_for_tests


def _wait_for_phase(worker, phase, timeout=1.0):
    deadline = time.monotonic() + timeout
    while worker.state().phase is not phase:
        if time.monotonic() >= deadline:
            pytest.fail(f"worker did not reach {phase.value}")
        time.sleep(0.001)


def test_background_warmup_is_non_blocking_and_idempotent():
    loading = threading.Event()
    release = threading.Event()
    attempts = []

    def loader():
        attempts.append(threading.get_ident())
        loading.set()
        release.wait(timeout=2)

    worker = RerankWorker(capacity=2, loader=loader)
    try:
        before = time.monotonic()
        first = worker.start_warmup()
        elapsed = time.monotonic() - before
        assert first == RerankWarmupResult(True, "rerank_warmup_scheduled")
        assert elapsed < 0.5
        assert loading.wait(timeout=1)
        assert worker.state().phase is RerankWorkerPhase.LOADING

        second = worker.start_warmup()
        assert second.reason_code == "rerank_warmup_already_pending"
        assert second.scheduled is False
        release.set()
        _wait_for_phase(worker, RerankWorkerPhase.READY)
        assert len(attempts) == 1
        assert attempts[0] != threading.get_ident()

        third = worker.start_warmup()
        assert third.reason_code == "rerank_warmup_not_needed"
        assert json.loads(json.dumps(third.to_dict())) == third.to_dict()
        with pytest.raises(FrozenInstanceError):
            third.scheduled = True
    finally:
        release.set()
        worker.close(wait=True, timeout=1)


def test_background_warmup_failure_updates_state_without_raising_to_scheduler():
    attempted = threading.Event()

    def loader():
        attempted.set()
        raise RuntimeError("private warmup detail")

    worker = RerankWorker(capacity=1, loader=loader)
    try:
        result = worker.start_warmup()
        assert result.reason_code == "rerank_warmup_scheduled"
        assert attempted.wait(timeout=1)
        _wait_for_phase(worker, RerankWorkerPhase.FAILED)
        assert worker.circuit_snapshot().consecutive_failures == 1
        assert "private warmup detail" not in repr(result.to_dict())
    finally:
        worker.close(wait=True, timeout=1)


def test_close_rejects_background_warmup_with_stable_result():
    worker = RerankWorker(capacity=1, loader=lambda: None)
    assert worker.close(wait=True, timeout=1)
    result = worker.start_warmup()
    assert result == RerankWarmupResult(False, "rerank_warmup_worker_closed")


def test_worker_lazy_loads_once_and_reuses_single_thread():
    loader_threads = []
    operation_threads = []
    worker = RerankWorker(
        capacity=2,
        loader=lambda: loader_threads.append(threading.get_ident()),
    )
    try:
        first = worker.submit(
            lambda: operation_threads.append(threading.get_ident()) or "first", 1
        )
        second = worker.submit(
            lambda: operation_threads.append(threading.get_ident()) or "second", 1
        )

        assert (first, second) == ("first", "second")
        assert len(loader_threads) == 1
        assert len(set(operation_threads)) == 1
        assert operation_threads == [loader_threads[0], loader_threads[0]]
        assert worker.state().phase is RerankWorkerPhase.READY
    finally:
        assert worker.close(wait=True, timeout=1)


def test_worker_records_real_model_load_duration():
    reset_metrics_for_tests()
    worker = RerankWorker(capacity=1, loader=lambda: None)
    try:
        assert worker.submit(lambda: "ready", 1) == "ready"
        metrics = metrics_snapshot().to_dict()
        assert metrics["model_load_count"] == 1
        assert metrics["model_load_seconds_sum"] >= 0
    finally:
        worker.close(wait=True, timeout=1)


def test_load_failure_is_stable_and_next_request_can_retry():
    attempts = 0

    def loader():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("private load detail")

    worker = RerankWorker(capacity=1, loader=loader)
    try:
        with pytest.raises(RerankModelLoadError) as caught:
            worker.submit(lambda: "unused", 1)
        assert str(caught.value) == "rerank_load_error"
        assert worker.state().phase is RerankWorkerPhase.FAILED

        assert worker.submit(lambda: "recovered", 1) == "recovered"
        assert worker.state().phase is RerankWorkerPhase.READY
        assert attempts == 2
    finally:
        worker.close(wait=True, timeout=1)


def test_inference_failure_degrades_and_success_recovers():
    worker = RerankWorker(capacity=1, loader=lambda: None)
    try:
        with pytest.raises(RerankInferenceError) as caught:
            worker.submit(
                lambda: (_ for _ in ()).throw(ValueError("private inference detail")),
                1,
            )
        assert str(caught.value) == "rerank_inference_error"
        assert worker.state().phase is RerankWorkerPhase.DEGRADED

        assert worker.submit(lambda: "ok", 1) == "ok"
        assert worker.state().phase is RerankWorkerPhase.READY
    finally:
        worker.close(wait=True, timeout=1)


def test_active_inference_timeout_returns_while_operation_finishes_in_worker():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    worker = RerankWorker(capacity=1, loader=lambda: None)

    def operation():
        started.set()
        release.wait(timeout=2)
        finished.set()
        return "late"

    try:
        before = time.monotonic()
        with pytest.raises(RerankInferenceTimeoutError) as caught:
            worker.submit(operation, 0.2)
        elapsed = time.monotonic() - before

        assert caught.value.reason_code == "rerank_timeout"
        assert started.is_set()
        assert elapsed < 0.8
        assert not finished.is_set()
        release.set()
        assert finished.wait(timeout=1)
        assert worker.submit(lambda: "next", 1) == "next"
    finally:
        release.set()
        worker.close(wait=True, timeout=1)


def test_timed_out_pending_request_is_cancelled_before_execution():
    active = threading.Event()
    release = threading.Event()
    worker = RerankWorker(capacity=1, loader=lambda: None)
    first_result = []

    def first():
        active.set()
        release.wait(timeout=2)
        return "first"

    first_thread = threading.Thread(
        target=lambda: first_result.append(worker.submit(first, 1))
    )
    first_thread.start()
    assert active.wait(timeout=1)
    executed = threading.Event()

    try:
        with pytest.raises(RerankInferenceTimeoutError):
            worker.submit(lambda: executed.set(), 0.02)
        release.set()
        first_thread.join(timeout=1)
        assert first_result == ["first"]
        assert not executed.wait(timeout=0.05)
    finally:
        release.set()
        first_thread.join(timeout=1)
        worker.close(wait=True, timeout=1)


def test_queue_full_rejects_third_request_with_stable_reason():
    active = threading.Event()
    release = threading.Event()
    worker = RerankWorker(capacity=1, loader=lambda: None)
    results = []

    def blocking(value):
        active.set()
        release.wait(timeout=2)
        return value

    first = threading.Thread(
        target=lambda: results.append(worker.submit(lambda: blocking("first"), 1))
    )
    first.start()
    assert active.wait(timeout=1)
    second = threading.Thread(
        target=lambda: results.append(worker.submit(lambda: "second", 1))
    )
    second.start()
    deadline = time.monotonic() + 1
    while worker.queue_snapshot().size != 1 and time.monotonic() < deadline:
        time.sleep(0.001)

    try:
        with pytest.raises(RerankQueueFullError) as caught:
            worker.submit(lambda: "third", 1)
        assert caught.value.reason_code == "rerank_queue_full"
        assert worker.circuit_snapshot().consecutive_failures == 0
    finally:
        release.set()
        first.join(timeout=1)
        second.join(timeout=1)
        worker.close(wait=True, timeout=1)
    assert sorted(results) == ["first", "second"]


def test_close_rejects_new_requests_and_unloads_state():
    worker = RerankWorker(capacity=1, loader=lambda: None)
    assert worker.submit(lambda: "ok", 1) == "ok"
    assert worker.close(wait=True, timeout=1)
    assert worker.state().phase is RerankWorkerPhase.UNLOADED
    with pytest.raises(RerankWorkerClosedError):
        worker.submit(lambda: "late", 1)


@pytest.mark.parametrize("value", [True, "1", None])
def test_submit_rejects_non_numeric_deadlines(value):
    worker = RerankWorker(capacity=1, loader=lambda: None)
    try:
        with pytest.raises(TypeError):
            worker.submit(lambda: None, value)
    finally:
        worker.close(wait=True, timeout=1)


@pytest.mark.parametrize(
    "value", [0, -1, float("nan"), float("inf"), MAX_RERANK_DEADLINE_SECONDS + 1]
)
def test_submit_rejects_invalid_numeric_deadlines(value):
    worker = RerankWorker(capacity=1, loader=lambda: None)
    try:
        with pytest.raises(ValueError):
            worker.submit(lambda: None, value)
    finally:
        worker.close(wait=True, timeout=1)


def test_state_and_queue_snapshots_do_not_expose_operation_content():
    secret = "compare secret_alpha.py and secret_beta.py"
    worker = RerankWorker(capacity=1, loader=lambda: None)
    try:
        assert worker.submit(lambda: secret, 1) == secret
        assert secret not in repr(worker.state().to_dict())
        assert secret not in repr(worker.queue_snapshot().to_dict())
        assert secret not in repr(worker.circuit_snapshot().to_dict())
        runtime = worker.runtime_snapshot()
        assert runtime.state.phase is RerankWorkerPhase.READY
        assert runtime.queue_size == 0
        assert runtime.thread_alive is True
        assert secret not in repr(runtime.to_dict())
        json.dumps(runtime.to_dict())
    finally:
        worker.close(wait=True, timeout=1)


def test_consecutive_failures_open_circuit_at_exact_threshold():
    now = [100.0]
    calls = 0
    worker = RerankWorker(
        capacity=1,
        loader=lambda: None,
        failure_threshold=2,
        cooldown_seconds=10,
        clock=lambda: now[0],
    )

    def fail():
        nonlocal calls
        calls += 1
        raise RuntimeError("private")

    try:
        with pytest.raises(RerankInferenceError):
            worker.submit(fail, 1)
        assert worker.circuit_snapshot().phase is RerankCircuitPhase.CLOSED
        with pytest.raises(RerankInferenceError):
            worker.submit(fail, 1)

        snapshot = worker.circuit_snapshot()
        assert snapshot.phase is RerankCircuitPhase.OPEN
        assert snapshot.consecutive_failures == 2
        assert snapshot.cooldown_remaining_seconds == 10
        assert worker.state().phase is RerankWorkerPhase.FAILED
        json.dumps(snapshot.to_dict())
        with pytest.raises(FrozenInstanceError):
            snapshot.consecutive_failures = 0
        with pytest.raises(RerankCircuitOpenError):
            worker.submit(fail, 1)
        assert calls == 2
    finally:
        worker.close(wait=True, timeout=1)


def test_success_resets_consecutive_failure_count():
    worker = RerankWorker(
        capacity=1,
        loader=lambda: None,
        failure_threshold=2,
        cooldown_seconds=10,
    )
    fail = lambda: (_ for _ in ()).throw(RuntimeError("private"))
    try:
        with pytest.raises(RerankInferenceError):
            worker.submit(fail, 1)
        assert worker.submit(lambda: "ok", 1) == "ok"
        with pytest.raises(RerankInferenceError):
            worker.submit(fail, 1)

        snapshot = worker.circuit_snapshot()
        assert snapshot.phase is RerankCircuitPhase.CLOSED
        assert snapshot.consecutive_failures == 1
    finally:
        worker.close(wait=True, timeout=1)


def test_cooldown_allows_only_one_recovery_probe_and_success_closes():
    now = [10.0]
    worker = RerankWorker(
        capacity=2,
        loader=lambda: None,
        failure_threshold=1,
        cooldown_seconds=5,
        clock=lambda: now[0],
    )
    with pytest.raises(RerankInferenceError):
        worker.submit(lambda: (_ for _ in ()).throw(RuntimeError("fail")), 1)
    with pytest.raises(RerankCircuitOpenError):
        worker.submit(lambda: "early", 1)

    now[0] += 5
    started = threading.Event()
    release = threading.Event()
    results = []

    def probe():
        started.set()
        release.wait(timeout=2)
        return "probe_ok"

    thread = threading.Thread(target=lambda: results.append(worker.submit(probe, 1)))
    thread.start()
    assert started.wait(timeout=1)
    try:
        snapshot = worker.circuit_snapshot()
        assert snapshot.phase is RerankCircuitPhase.HALF_OPEN
        assert snapshot.probe_in_progress is True
        with pytest.raises(RerankRecoveryProbeInProgressError):
            worker.submit(lambda: "second_probe", 1)
        release.set()
        thread.join(timeout=1)

        assert results == ["probe_ok"]
        assert worker.circuit_snapshot().phase is RerankCircuitPhase.CLOSED
        assert worker.circuit_snapshot().consecutive_failures == 0
        assert worker.state().phase is RerankWorkerPhase.READY
    finally:
        release.set()
        thread.join(timeout=1)
        worker.close(wait=True, timeout=1)


def test_failed_recovery_probe_reopens_full_cooldown():
    now = [50.0]
    worker = RerankWorker(
        capacity=1,
        loader=lambda: None,
        failure_threshold=1,
        cooldown_seconds=7,
        clock=lambda: now[0],
    )
    fail = lambda: (_ for _ in ()).throw(RuntimeError("private"))
    try:
        with pytest.raises(RerankInferenceError):
            worker.submit(fail, 1)
        now[0] += 7
        with pytest.raises(RerankInferenceError):
            worker.submit(fail, 1)

        snapshot = worker.circuit_snapshot()
        assert snapshot.phase is RerankCircuitPhase.OPEN
        assert snapshot.cooldown_remaining_seconds == 7
        assert snapshot.probe_in_progress is False
        assert worker.state().phase is RerankWorkerPhase.FAILED
    finally:
        worker.close(wait=True, timeout=1)


def test_load_failures_open_circuit_and_probe_reloads_model():
    now = [0.0]
    attempts = 0

    def loader():
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise RuntimeError("load unavailable")

    worker = RerankWorker(
        capacity=1,
        loader=loader,
        failure_threshold=2,
        cooldown_seconds=3,
        clock=lambda: now[0],
    )
    try:
        with pytest.raises(RerankModelLoadError):
            worker.submit(lambda: "unused", 1)
        with pytest.raises(RerankModelLoadError):
            worker.submit(lambda: "unused", 1)
        with pytest.raises(RerankCircuitOpenError):
            worker.submit(lambda: "blocked", 1)

        now[0] += 3
        assert worker.submit(lambda: "recovered", 1) == "recovered"
        assert attempts == 3
        assert worker.state().phase is RerankWorkerPhase.READY
        assert worker.circuit_snapshot().phase is RerankCircuitPhase.CLOSED
    finally:
        worker.close(wait=True, timeout=1)


def test_requests_queued_before_open_are_rejected_without_execution():
    active = threading.Event()
    release = threading.Event()
    executed = threading.Event()
    errors = []
    worker = RerankWorker(
        capacity=2,
        loader=lambda: None,
        failure_threshold=1,
        cooldown_seconds=10,
    )

    def first_operation():
        active.set()
        release.wait(timeout=2)
        raise RuntimeError("open circuit")

    def submit_and_capture(operation):
        try:
            worker.submit(operation, 1)
        except RerankWorkerError as exc:
            errors.append(exc.reason_code)

    first = threading.Thread(target=submit_and_capture, args=(first_operation,))
    second = threading.Thread(
        target=submit_and_capture, args=(lambda: executed.set(),)
    )
    first.start()
    assert active.wait(timeout=1)
    second.start()
    deadline = time.monotonic() + 1
    while worker.queue_snapshot().size != 1 and time.monotonic() < deadline:
        time.sleep(0.001)
    release.set()
    first.join(timeout=1)
    second.join(timeout=1)
    try:
        assert not executed.is_set()
        assert sorted(errors) == ["rerank_circuit_open", "rerank_inference_error"]
    finally:
        release.set()
        first.join(timeout=1)
        second.join(timeout=1)
        worker.close(wait=True, timeout=1)


def test_caller_timeout_does_not_count_as_model_failure():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    worker = RerankWorker(
        capacity=1,
        loader=lambda: None,
        failure_threshold=1,
        cooldown_seconds=10,
    )

    def slow_success():
        started.set()
        release.wait(timeout=2)
        finished.set()
        return "late"

    try:
        with pytest.raises(RerankInferenceTimeoutError):
            worker.submit(slow_success, 0.2)
        assert started.is_set()
        release.set()
        assert finished.wait(timeout=1)
        deadline = time.monotonic() + 1
        while worker.state().phase is not RerankWorkerPhase.READY:
            if time.monotonic() >= deadline:
                pytest.fail("worker did not finish timed-out inference")
            time.sleep(0.001)
        snapshot = worker.circuit_snapshot()
        assert snapshot.phase is RerankCircuitPhase.CLOSED
        assert snapshot.consecutive_failures == 0
    finally:
        release.set()
        worker.close(wait=True, timeout=1)


@pytest.mark.parametrize("value", [True, 1.0, "3", None])
def test_failure_threshold_rejects_non_integer_values(value):
    with pytest.raises(TypeError):
        RerankWorker(capacity=1, loader=lambda: None, failure_threshold=value)


@pytest.mark.parametrize("value", [0, -1, MAX_RERANK_FAILURE_THRESHOLD + 1])
def test_failure_threshold_is_hard_bounded(value):
    with pytest.raises(ValueError):
        RerankWorker(capacity=1, loader=lambda: None, failure_threshold=value)


@pytest.mark.parametrize("value", [True, "1", None])
def test_cooldown_rejects_non_numeric_values(value):
    with pytest.raises(TypeError):
        RerankWorker(capacity=1, loader=lambda: None, cooldown_seconds=value)


@pytest.mark.parametrize(
    "value", [0, -1, float("nan"), float("inf"), MAX_RERANK_COOLDOWN_SECONDS + 1]
)
def test_cooldown_is_positive_finite_and_hard_bounded(value):
    with pytest.raises(ValueError):
        RerankWorker(capacity=1, loader=lambda: None, cooldown_seconds=value)
