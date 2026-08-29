"""Deterministic pressure and deadlock acceptance tests for MODEL-007."""

import json
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor

import pytest

import rag.retriever as retriever
from rag.rerank_stress import run_worker_stress
from rag.rerank_worker import (
    RerankInferenceError,
    RerankInferenceTimeoutError,
    RerankQueueFullError,
    RerankRecoveryProbeInProgressError,
    RerankWorker,
    RerankWorkerClosedError,
    RerankWorkerError,
)
from rag.retriever import SearchHit


def test_pressure_report_records_throughput_p95_and_bounded_queue():
    report = run_worker_stress(
        total_requests=128,
        concurrency=32,
        queue_capacity=4,
        operation_seconds=0.02,
        caller_timeout_seconds=1.0,
        join_timeout_seconds=5.0,
    )
    outcomes = dict(report.terminal_outcomes)

    assert report.deadlock_detected is False
    assert report.unfinished_callers == 0
    assert report.monitor_completed is True
    assert report.shutdown_completed is True
    assert sum(outcomes.values()) == 128
    assert set(outcomes) <= {"rerank_success", "rerank_queue_full"}
    assert outcomes["rerank_success"] > 0
    assert outcomes["rerank_queue_full"] > 0
    assert report.worker_thread_count == 1
    assert report.max_queue_size <= 4
    assert report.terminal_throughput_rps > 0
    assert report.completed_throughput_rps > 0
    assert 0 <= report.caller_latency_p50_ms <= report.caller_latency_p95_ms
    assert report.caller_latency_p95_ms <= report.caller_latency_max_ms
    assert report.completed_latency_p95_ms > 0
    json.dumps(report.to_dict())


def test_deadline_saturation_finishes_all_callers_without_deadlock():
    report = run_worker_stress(
        total_requests=96,
        concurrency=32,
        queue_capacity=4,
        operation_seconds=0.03,
        caller_timeout_seconds=0.005,
        join_timeout_seconds=5.0,
    )
    outcomes = dict(report.terminal_outcomes)

    assert report.deadlock_detected is False
    assert sum(outcomes.values()) == 96
    assert set(outcomes) <= {
        "rerank_success", "rerank_queue_full", "rerank_timeout",
    }
    assert outcomes["rerank_timeout"] > 0
    assert outcomes["rerank_queue_full"] > 0
    assert report.max_queue_size <= 4
    assert report.caller_latency_max_ms < 1_000


def test_pressure_run_is_repeatable_without_leaked_worker_threads():
    for _ in range(5):
        report = run_worker_stress(
            total_requests=64,
            concurrency=16,
            queue_capacity=8,
            operation_seconds=0.001,
            caller_timeout_seconds=0.2,
            join_timeout_seconds=3.0,
        )
        assert report.deadlock_detected is False
        assert report.worker_thread_count == 1
        outcomes = dict(report.terminal_outcomes)
        assert sum(outcomes.values()) == 64
        assert "rerank_unexpected_error" not in outcomes


def test_close_submit_and_snapshot_race_has_bounded_completion():
    active = threading.Event()
    release = threading.Event()
    stop_snapshots = threading.Event()
    worker = RerankWorker(capacity=8, loader=lambda: None)
    outcomes = []
    snapshot_errors = []
    outcome_lock = threading.Lock()

    def operation(request_id):
        if request_id == 0:
            active.set()
            release.wait(timeout=2)
        return request_id

    def submit(request_id):
        reason = "rerank_success"
        try:
            worker.submit(lambda: operation(request_id), 1.0)
        except RerankWorkerError as exc:
            reason = exc.reason_code
        with outcome_lock:
            outcomes.append(reason)

    def take_snapshots():
        try:
            while not stop_snapshots.is_set():
                snapshot = worker.runtime_snapshot()
                if snapshot.queue_size > snapshot.queue_capacity:
                    raise AssertionError("queue exceeded capacity")
                stop_snapshots.wait(0.0001)
        except BaseException as exc:
            snapshot_errors.append(exc)

    first = threading.Thread(target=submit, args=(0,), daemon=True)
    first.start()
    assert active.wait(timeout=1)
    callers = [
        threading.Thread(target=submit, args=(index,), daemon=True)
        for index in range(1, 33)
    ]
    snapshotter = threading.Thread(target=take_snapshots, daemon=True)
    snapshotter.start()
    for thread in callers:
        thread.start()

    deadline = time.monotonic() + 1
    while worker.queue_snapshot().size < 8 and time.monotonic() < deadline:
        time.sleep(0.001)
    assert worker.close(wait=False) is False
    with pytest.raises(RerankWorkerClosedError):
        worker.submit(lambda: "late", 0.1)
    release.set()
    first.join(timeout=2)
    for thread in callers:
        thread.join(timeout=2)
    stop_snapshots.set()
    snapshotter.join(timeout=1)

    assert not first.is_alive()
    assert not any(thread.is_alive() for thread in callers)
    assert not snapshotter.is_alive()
    assert snapshot_errors == []
    assert len(outcomes) == 33
    assert set(outcomes) <= {
        "rerank_success", "rerank_queue_full", "rerank_queue_closed",
        "rerank_timeout",
    }
    assert worker.close(wait=True, timeout=2) is True


def test_cooldown_recovery_allows_one_probe_under_concurrent_pressure():
    now = [0.0]
    worker = RerankWorker(
        capacity=8,
        loader=lambda: None,
        failure_threshold=1,
        cooldown_seconds=1,
        clock=lambda: now[0],
    )
    with pytest.raises(RerankInferenceError):
        worker.submit(
            lambda: (_ for _ in ()).throw(RuntimeError("open circuit")), 1
        )
    now[0] = 1.0
    probe_started = threading.Event()
    release_probe = threading.Event()
    start = threading.Event()
    outcomes = []
    lock = threading.Lock()

    def submit_probe():
        start.wait()
        reason = "rerank_success"
        try:
            worker.submit(
                lambda: probe_started.set()
                or release_probe.wait(timeout=2)
                or "probe",
                1,
            )
        except RerankRecoveryProbeInProgressError as exc:
            reason = exc.reason_code
        with lock:
            outcomes.append(reason)

    callers = [
        threading.Thread(target=submit_probe, daemon=True) for _ in range(32)
    ]
    for thread in callers:
        thread.start()
    start.set()
    assert probe_started.wait(timeout=1)
    deadline = time.monotonic() + 1
    while len(outcomes) < 31 and time.monotonic() < deadline:
        time.sleep(0.001)
    assert len(outcomes) == 31
    release_probe.set()
    for thread in callers:
        thread.join(timeout=2)
    try:
        assert not any(thread.is_alive() for thread in callers)
        assert outcomes.count("rerank_success") == 1
        assert outcomes.count("rerank_recovery_probe_in_progress") == 31
    finally:
        release_probe.set()
        worker.close(wait=True, timeout=2)


def test_concurrent_retriever_fallback_preserves_original_rrf_rank(monkeypatch):
    monkeypatch.setattr(retriever, "_get_collection", lambda _project: object())
    monkeypatch.setattr(retriever, "_collection_documents", lambda *_a, **_k: [])

    def candidates(*_args, **_kwargs):
        return [
            SearchHit(
                uid=f"hit-{rank}",
                document="content",
                metadata={"file": f"file-{rank}.py"},
                score=1.0 / rank,
                rrf_rank=rank,
            )
            for rank in range(1, 4)
        ]

    counter = [0]
    counter_lock = threading.Lock()

    def fail_rerank(*_args, **_kwargs):
        with counter_lock:
            counter[0] += 1
            current = counter[0]
        if current % 2:
            raise RerankQueueFullError()
        raise RerankInferenceTimeoutError()

    monkeypatch.setattr(retriever, "_hybrid_candidates", candidates)
    monkeypatch.setattr("rag.reranker.rerank_via_worker", fail_rerank)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [
                executor.submit(retriever.retrieve, "query", ".", 2, "rerank")
                for _ in range(64)
            ]
            results = [future.result(timeout=2) for future in futures]

    assert counter[0] == 64
    for hits in results:
        assert [hit.uid for hit in hits] == ["hit-1", "hit-2"]
        assert [hit.rrf_rank for hit in hits] == [1, 2]
        assert all(hit.metadata["rerank_fallback"] is True for hit in hits)
        assert {
            hit.metadata["rerank_fallback_reason"] for hit in hits
        } <= {"rerank_queue_full", "rerank_timeout"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"total_requests": 0},
        {"concurrency": 0},
        {"operation_seconds": -1},
        {"caller_timeout_seconds": float("nan")},
        {"join_timeout_seconds": 61},
    ],
)
def test_stress_parameters_are_hard_bounded(kwargs):
    with pytest.raises(ValueError):
        run_worker_stress(**kwargs)
