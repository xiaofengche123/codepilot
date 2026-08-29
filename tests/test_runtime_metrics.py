"""Content-free RAG runtime metrics tests."""

from dataclasses import FrozenInstanceError
import json
import threading

import pytest

from rag.runtime_metrics import (
    metrics_snapshot,
    observe_model_load,
    observe_rerank,
    observe_retrieval,
    prometheus_lines,
    record_rerank_fallback,
    reset_metrics_for_tests,
)


@pytest.fixture(autouse=True)
def reset_metrics():
    reset_metrics_for_tests()
    yield
    reset_metrics_for_tests()


def test_metrics_snapshot_counts_bounded_labels_and_is_json_ready():
    observe_retrieval("hybrid", 0.012)
    observe_retrieval("private-mode", 0.003)
    observe_rerank(0.004)
    observe_model_load(0.5)
    record_rerank_fallback("rerank_timeout")
    record_rerank_fallback("private-reason")

    snapshot = metrics_snapshot()
    data = snapshot.to_dict()

    assert data["retrieval_count"]["hybrid"] == 1
    assert data["retrieval_count"]["unknown"] == 1
    assert data["retrieval_latency_ms_sum"]["hybrid"] == pytest.approx(12)
    assert data["rerank_count"] == 1
    assert data["model_load_count"] == 1
    assert data["fallback_total"]["rerank_timeout"] == 1
    assert data["fallback_total"]["rerank_unexpected_error"] == 1
    assert data["timeout_total"] == 1
    json.dumps(data)
    with pytest.raises(FrozenInstanceError):
        snapshot.rerank_count = 0


def test_metric_updates_are_thread_safe():
    threads = [
        threading.Thread(
            target=lambda: [observe_retrieval("vector", 0.001) for _ in range(50)]
        )
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    assert metrics_snapshot().to_dict()["retrieval_count"]["vector"] == 200


def test_prometheus_output_is_complete_and_does_not_expose_untrusted_labels():
    secret = "secret_query_or_exception"
    observe_retrieval(secret, 0.001)
    record_rerank_fallback(secret)
    output = "\n".join(prometheus_lines(queue_size=2))

    for metric in (
        "rag_retrieval_latency_ms",
        "rag_rerank_latency_ms",
        "rag_rerank_queue_size 2",
        "rag_rerank_fallback_total",
        "rag_rerank_timeout_total",
        "rag_model_load_seconds",
        "rag_search_mode_total",
    ):
        assert metric in output
    assert secret not in output


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_elapsed_time_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        observe_rerank(value)
