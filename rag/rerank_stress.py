"""Offline deterministic stress harness for the single rerank worker.

The harness uses only fake sleep-based inference. It never reads retrieval data,
loads a model, accesses the network, or writes a report file.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
import threading
import time

from rag.rerank_worker import RerankWorker, RerankWorkerError


MAX_STRESS_REQUESTS = 5_000
MAX_STRESS_CONCURRENCY = 512
MAX_STRESS_OPERATION_SECONDS = 10.0
MAX_STRESS_JOIN_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class RerankStressReport:
    total_requests: int
    concurrency: int
    queue_capacity: int
    operation_seconds: float
    caller_timeout_seconds: float
    terminal_outcomes: tuple[tuple[str, int], ...]
    unfinished_callers: int
    monitor_completed: bool
    shutdown_completed: bool
    deadlock_detected: bool
    worker_thread_count: int
    max_queue_size: int
    elapsed_seconds: float
    terminal_throughput_rps: float
    completed_throughput_rps: float
    caller_latency_p50_ms: float
    caller_latency_p95_ms: float
    caller_latency_max_ms: float
    completed_latency_p95_ms: float

    def to_dict(self) -> dict:
        return {
            "schema_version": "rerank-stress-v1",
            "total_requests": self.total_requests,
            "concurrency": self.concurrency,
            "queue_capacity": self.queue_capacity,
            "operation_seconds": self.operation_seconds,
            "caller_timeout_seconds": self.caller_timeout_seconds,
            "terminal_outcomes": dict(self.terminal_outcomes),
            "unfinished_callers": self.unfinished_callers,
            "monitor_completed": self.monitor_completed,
            "shutdown_completed": self.shutdown_completed,
            "deadlock_detected": self.deadlock_detected,
            "worker_thread_count": self.worker_thread_count,
            "max_queue_size": self.max_queue_size,
            "elapsed_seconds": self.elapsed_seconds,
            "terminal_throughput_rps": self.terminal_throughput_rps,
            "completed_throughput_rps": self.completed_throughput_rps,
            "caller_latency_p50_ms": self.caller_latency_p50_ms,
            "caller_latency_p95_ms": self.caller_latency_p95_ms,
            "caller_latency_max_ms": self.caller_latency_max_ms,
            "completed_latency_p95_ms": self.completed_latency_p95_ms,
        }


def run_worker_stress(
    *,
    total_requests: int = 256,
    concurrency: int = 64,
    queue_capacity: int = 8,
    operation_seconds: float = 0.002,
    caller_timeout_seconds: float = 0.25,
    join_timeout_seconds: float = 10.0,
) -> RerankStressReport:
    """Run bounded callers against one fake worker and return content-free data."""
    total = _bounded_int("total_requests", total_requests, 1, MAX_STRESS_REQUESTS)
    callers = _bounded_int(
        "concurrency", concurrency, 1, MAX_STRESS_CONCURRENCY
    )
    callers = min(callers, total)
    capacity = _bounded_int("queue_capacity", queue_capacity, 1, 10_000)
    operation_delay = _bounded_float(
        "operation_seconds", operation_seconds, 0.0, MAX_STRESS_OPERATION_SECONDS
    )
    caller_timeout = _bounded_float(
        "caller_timeout_seconds", caller_timeout_seconds, 0.000_001, 3_600.0
    )
    join_timeout = _bounded_float(
        "join_timeout_seconds", join_timeout_seconds, 0.001, MAX_STRESS_JOIN_SECONDS
    )

    worker_thread_ids: set[int] = set()
    outcomes: Counter[str] = Counter()
    latencies_ms: list[float] = []
    completed_latencies_ms: list[float] = []
    result_lock = threading.Lock()
    request_lock = threading.Lock()
    next_request = 0
    start = threading.Event()
    monitor_stop = threading.Event()
    max_queue_size = [0]
    worker = RerankWorker(capacity=capacity, loader=lambda: None)

    def operation(request_id: int) -> int:
        with result_lock:
            worker_thread_ids.add(threading.get_ident())
        if operation_delay:
            time.sleep(operation_delay)
        return request_id

    def caller() -> None:
        nonlocal next_request
        start.wait()
        while True:
            with request_lock:
                if next_request >= total:
                    return
                request_id = next_request
                next_request += 1
            began = time.perf_counter()
            reason = "rerank_success"
            try:
                worker.submit(
                    lambda request_id=request_id: operation(request_id),
                    caller_timeout,
                )
            except RerankWorkerError as exc:
                reason = exc.reason_code
            except BaseException:
                reason = "rerank_unexpected_error"
            latency_ms = (time.perf_counter() - began) * 1_000.0
            with result_lock:
                outcomes[reason] += 1
                latencies_ms.append(latency_ms)
                if reason == "rerank_success":
                    completed_latencies_ms.append(latency_ms)

    def monitor() -> None:
        while not monitor_stop.is_set():
            size = worker.queue_snapshot().size
            with result_lock:
                max_queue_size[0] = max(max_queue_size[0], size)
            monitor_stop.wait(0.0005)

    threads = [
        threading.Thread(target=caller, name=f"rerank-stress-{index}", daemon=True)
        for index in range(callers)
    ]
    monitor_thread = threading.Thread(
        target=monitor, name="rerank-stress-monitor", daemon=True
    )
    began = time.perf_counter()
    monitor_thread.start()
    for thread in threads:
        thread.start()
    start.set()
    deadline = time.monotonic() + join_timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    unfinished = sum(thread.is_alive() for thread in threads)
    monitor_stop.set()
    monitor_thread.join(timeout=0.5)
    monitor_completed = not monitor_thread.is_alive()
    shutdown_completed = False
    if unfinished == 0:
        shutdown_completed = worker.close(
            wait=True, timeout=min(2.0, join_timeout)
        )
    elapsed = max(time.perf_counter() - began, 0.000_001)

    with result_lock:
        ordered_outcomes = tuple(sorted(outcomes.items()))
        latency_values = sorted(latencies_ms)
        completed_latency_values = sorted(completed_latencies_ms)
        unique_worker_threads = len(worker_thread_ids)
        observed_queue_size = max_queue_size[0]
    completed = outcomes["rerank_success"]
    terminal = sum(outcomes.values())
    return RerankStressReport(
        total_requests=total,
        concurrency=callers,
        queue_capacity=capacity,
        operation_seconds=operation_delay,
        caller_timeout_seconds=caller_timeout,
        terminal_outcomes=ordered_outcomes,
        unfinished_callers=unfinished,
        monitor_completed=monitor_completed,
        shutdown_completed=shutdown_completed,
        deadlock_detected=(
            unfinished > 0
            or terminal != total
            or not monitor_completed
            or not shutdown_completed
        ),
        worker_thread_count=unique_worker_threads,
        max_queue_size=observed_queue_size,
        elapsed_seconds=elapsed,
        terminal_throughput_rps=terminal / elapsed,
        completed_throughput_rps=completed / elapsed,
        caller_latency_p50_ms=_percentile(latency_values, 0.50),
        caller_latency_p95_ms=_percentile(latency_values, 0.95),
        caller_latency_max_ms=max(latency_values, default=0.0),
        completed_latency_p95_ms=_percentile(completed_latency_values, 0.95),
    )


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    index = max(0, math.ceil(len(values) * quantile) - 1)
    return values[index]


def _bounded_int(name: str, value: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_float(
    name: str, value: float, minimum: float, maximum: float
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < minimum or normalized > maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline rerank worker stress")
    parser.add_argument("--requests", type=int, default=256)
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument("--capacity", type=int, default=8)
    parser.add_argument("--operation-ms", type=float, default=2.0)
    parser.add_argument("--timeout-ms", type=float, default=250.0)
    args = parser.parse_args()
    report = run_worker_stress(
        total_requests=args.requests,
        concurrency=args.concurrency,
        queue_capacity=args.capacity,
        operation_seconds=args.operation_ms / 1_000.0,
        caller_timeout_seconds=args.timeout_ms / 1_000.0,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=True, sort_keys=True))
    if report.deadlock_detected:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
