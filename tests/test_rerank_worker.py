"""Single-model worker deadline and backpressure tests for MODEL-003."""

import threading
import time

import pytest

from rag.rerank_worker import (
    MAX_RERANK_DEADLINE_SECONDS,
    RerankInferenceError,
    RerankInferenceTimeoutError,
    RerankModelLoadError,
    RerankQueueFullError,
    RerankWorker,
    RerankWorkerClosedError,
)
from rag.rerank_worker_state import RerankWorkerPhase


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
    finally:
        worker.close(wait=True, timeout=1)
