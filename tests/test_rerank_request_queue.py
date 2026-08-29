"""Bounded Rerank request queue tests for MODEL-002."""

from dataclasses import FrozenInstanceError
import json
import threading

import pytest

from rag.rerank_request_queue import (
    MAX_RERANK_QUEUE_CAPACITY,
    MAX_RERANK_QUEUE_WAIT_SECONDS,
    BoundedRerankQueue,
    RerankQueueClosed,
    RerankQueueEmpty,
)


def test_offer_and_take_preserve_fifo_order():
    queue = BoundedRerankQueue[str](2)

    first = queue.offer("first")
    second = queue.offer("second")

    assert first.to_dict() == {
        "accepted": True,
        "reason_code": "rerank_queue_accepted",
        "size": 1,
        "capacity": 2,
    }
    assert second.accepted is True
    assert queue.take(timeout=0) == "first"
    assert queue.take(timeout=0) == "second"


def test_full_queue_rejects_without_blocking_or_replacing_work():
    queue = BoundedRerankQueue[str](1)
    assert queue.offer("kept").accepted is True

    rejected = queue.offer("rejected")

    assert rejected.to_dict() == {
        "accepted": False,
        "reason_code": "rerank_queue_full",
        "size": 1,
        "capacity": 1,
    }
    assert queue.take(timeout=0) == "kept"


def test_concurrent_offers_never_exceed_capacity():
    capacity = 5
    queue = BoundedRerankQueue[int](capacity)
    barrier = threading.Barrier(21)
    results = []
    result_lock = threading.Lock()

    def producer(value):
        barrier.wait()
        offered = queue.offer(value)
        with result_lock:
            results.append(offered)

    threads = [
        threading.Thread(target=producer, args=(value,)) for value in range(20)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(result.accepted for result in results) == capacity
    assert len(queue) == capacity
    assert {result.reason_code for result in results} == {
        "rerank_queue_accepted",
        "rerank_queue_full",
    }


def test_waiting_consumer_receives_offered_item():
    queue = BoundedRerankQueue[object](1)
    started = threading.Event()
    received = []

    def consumer():
        started.set()
        received.append(queue.take())

    thread = threading.Thread(target=consumer)
    thread.start()
    assert started.wait(timeout=1)
    item = object()
    assert queue.offer(item).accepted is True
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert received == [item]


def test_take_timeout_has_stable_error_code():
    queue = BoundedRerankQueue[str](1)
    with pytest.raises(RerankQueueEmpty) as caught:
        queue.take(timeout=0)
    assert caught.value.error_code == "rerank_queue_empty"


def test_close_rejects_new_work_but_allows_pending_work_to_drain():
    queue = BoundedRerankQueue[str](2)
    queue.offer("pending")

    snapshot = queue.close()
    rejected = queue.offer("late")

    assert snapshot.to_dict() == {"size": 1, "capacity": 2, "closed": True}
    assert rejected.reason_code == "rerank_queue_closed"
    assert queue.take(timeout=0) == "pending"
    with pytest.raises(RerankQueueClosed):
        queue.take(timeout=0)


def test_close_is_idempotent_and_wakes_blocked_consumers():
    queue = BoundedRerankQueue[str](1)
    started = threading.Event()
    errors = []

    def consumer():
        started.set()
        try:
            queue.take()
        except RerankQueueClosed as exc:
            errors.append(exc.error_code)

    thread = threading.Thread(target=consumer)
    thread.start()
    assert started.wait(timeout=1)
    first = queue.close()
    second = queue.close()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert first == second
    assert errors == ["rerank_queue_closed"]


def test_snapshot_is_immutable_json_ready_and_content_free():
    secret = "compare secret_alpha.py and secret_beta.py"
    queue = BoundedRerankQueue[object](2)
    queue.offer({"query": secret})

    snapshot = queue.snapshot()
    serialized = json.dumps(snapshot.to_dict())

    assert serialized == '{"size": 1, "capacity": 2, "closed": false}'
    assert secret not in serialized
    with pytest.raises(FrozenInstanceError):
        snapshot.size = 0


@pytest.mark.parametrize("capacity", [True, 1.0, "1", None])
def test_capacity_rejects_non_integer_values(capacity):
    with pytest.raises(TypeError):
        BoundedRerankQueue(capacity)


@pytest.mark.parametrize("capacity", [0, -1, MAX_RERANK_QUEUE_CAPACITY + 1])
def test_capacity_is_positive_and_hard_bounded(capacity):
    with pytest.raises(ValueError):
        BoundedRerankQueue(capacity)


@pytest.mark.parametrize("timeout", [True, "1", object()])
def test_take_rejects_non_numeric_timeout(timeout):
    with pytest.raises(TypeError):
        BoundedRerankQueue(1).take(timeout=timeout)


@pytest.mark.parametrize(
    "timeout",
    [-1, float("nan"), float("inf"), MAX_RERANK_QUEUE_WAIT_SECONDS + 1],
)
def test_take_rejects_invalid_numeric_timeout(timeout):
    with pytest.raises(ValueError):
        BoundedRerankQueue(1).take(timeout=timeout)


def test_none_cannot_be_used_as_an_implicit_sentinel():
    with pytest.raises(TypeError):
        BoundedRerankQueue(1).offer(None)


def test_module_does_not_import_model_config_or_retrieval_runtime():
    import rag.rerank_request_queue as module

    names = set(module.__dict__)
    assert not ({"CrossEncoder", "config", "Retriever", "rerank"} & names)
