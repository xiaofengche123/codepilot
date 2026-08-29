import numpy as np
import pytest
import threading
import time

import rag.reranker as reranker
import rag.retriever as retriever
from rag.rerank_worker import (
    RerankCircuitOpenError,
    RerankInferenceError,
    RerankQueueFullError,
)
from rag.retriever import SearchHit


def _hit(uid: str, document: str, rrf_score: float = 0.0) -> SearchHit:
    return SearchHit(
        uid=uid,
        document=document,
        metadata={"file": f"{uid}.py", "start_line": 1, "end_line": 3},
        score=rrf_score,
    )


def test_cross_encoder_reranks_pairs_and_preserves_rrf_rank(monkeypatch):
    class FakeCrossEncoder:
        def predict(self, pairs, **kwargs):
            assert pairs[0][0] == "authentication"
            assert "file: first.py" in pairs[0][1]
            assert kwargs["show_progress_bar"] is False
            return np.array([0.1, 0.9, 0.4])

    monkeypatch.setattr(reranker, "_load_model", lambda: FakeCrossEncoder())
    monkeypatch.setattr(
        reranker,
        "_settings",
        lambda: {"batch_size": 8},
    )
    hits = [_hit("first", "cache"), _hit("second", "login"), _hit("third", "token")]

    ranked = reranker.rerank("authentication", hits, limit=2)

    assert [hit.uid for hit in ranked] == ["second", "third"]
    assert ranked[0].rerank_score == pytest.approx(0.9)
    assert ranked[0].rrf_rank == 2


def test_rerank_mode_falls_back_to_rrf_on_model_error(monkeypatch):
    candidates = [_hit("one", "first", 0.2), _hit("two", "second", 0.1)]
    for rank, hit in enumerate(candidates, start=1):
        hit.rrf_rank = rank
    monkeypatch.setattr(retriever, "_get_collection", lambda _project: object())
    monkeypatch.setattr(
        retriever,
        "_collection_documents",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        retriever,
        "_hybrid_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        reranker,
        "rerank_via_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RerankInferenceError()),
    )

    with pytest.warns(RuntimeWarning, match="falling back to RRF"):
        hits = retriever.retrieve("query", ".", n=1, mode="rerank")

    assert [hit.uid for hit in hits] == ["one"]
    assert hits[0].metadata["rerank_fallback"] is True
    assert hits[0].metadata["rerank_fallback_reason"] == "rerank_inference_error"
    assert hits[0].rrf_rank == 1


def test_queue_full_fallback_preserves_rrf_order_and_reason(monkeypatch):
    candidates = [_hit("one", "first", 0.2), _hit("two", "second", 0.1)]
    for rank, hit in enumerate(candidates, start=1):
        hit.rrf_rank = rank
    monkeypatch.setattr(retriever, "_get_collection", lambda _project: object())
    monkeypatch.setattr(retriever, "_collection_documents", lambda *_a, **_k: candidates)
    monkeypatch.setattr(retriever, "_hybrid_candidates", lambda *_a, **_k: candidates)
    monkeypatch.setattr(
        reranker,
        "rerank_via_worker",
        lambda *_a, **_k: (_ for _ in ()).throw(RerankQueueFullError()),
    )

    with pytest.warns(RuntimeWarning, match="rerank_queue_full"):
        hits = retriever.retrieve("query", ".", n=2, mode="rerank")

    assert [hit.uid for hit in hits] == ["one", "two"]
    assert [hit.rrf_rank for hit in hits] == [1, 2]
    assert all(
        hit.metadata["rerank_fallback_reason"] == "rerank_queue_full" for hit in hits
    )


def test_untrusted_exception_text_or_reason_is_not_exposed(monkeypatch):
    class UntrustedError(RuntimeError):
        reason_code = "API_KEY_secret"

    candidates = [_hit("one", "first", 0.2)]
    candidates[0].rrf_rank = 1
    monkeypatch.setattr(retriever, "_get_collection", lambda _project: object())
    monkeypatch.setattr(retriever, "_collection_documents", lambda *_a, **_k: candidates)
    monkeypatch.setattr(retriever, "_hybrid_candidates", lambda *_a, **_k: candidates)
    monkeypatch.setattr(
        reranker,
        "rerank_via_worker",
        lambda *_a, **_k: (_ for _ in ()).throw(UntrustedError("private detail")),
    )

    with pytest.warns(RuntimeWarning) as caught:
        hits = retriever.retrieve("query", ".", n=1, mode="rerank")

    assert "private detail" not in str(caught[0].message)
    assert "API_KEY_secret" not in str(caught[0].message)
    assert hits[0].metadata["rerank_fallback_reason"] == "rerank_unexpected_error"


def test_circuit_open_uses_stable_rrf_fallback_reason(monkeypatch):
    candidates = [_hit("one", "first", 0.2)]
    candidates[0].rrf_rank = 1
    monkeypatch.setattr(retriever, "_get_collection", lambda _project: object())
    monkeypatch.setattr(retriever, "_collection_documents", lambda *_a, **_k: candidates)
    monkeypatch.setattr(retriever, "_hybrid_candidates", lambda *_a, **_k: candidates)
    monkeypatch.setattr(
        reranker,
        "rerank_via_worker",
        lambda *_a, **_k: (_ for _ in ()).throw(RerankCircuitOpenError()),
    )

    with pytest.warns(RuntimeWarning, match="rerank_circuit_open"):
        hits = retriever.retrieve("query", ".", n=1, mode="rerank")

    assert hits[0].rrf_rank == 1
    assert hits[0].metadata["rerank_fallback_reason"] == "rerank_circuit_open"


def test_worker_timeout_cannot_mutate_returned_rrf_fallback(monkeypatch):
    candidates = [_hit("one", "first", 0.2), _hit("two", "second", 0.1)]
    for rank, hit in enumerate(candidates, start=1):
        hit.rrf_rank = rank
    started = threading.Event()
    release = threading.Event()

    def slow_rerank(_query, isolated, _limit):
        started.set()
        release.wait(timeout=2)
        isolated[0].rerank_score = 99.0
        return isolated

    reranker.reset_for_tests()
    monkeypatch.setattr(retriever, "_get_collection", lambda _project: object())
    monkeypatch.setattr(retriever, "_collection_documents", lambda *_a, **_k: candidates)
    monkeypatch.setattr(retriever, "_hybrid_candidates", lambda *_a, **_k: candidates)
    monkeypatch.setattr(reranker, "_load_model", lambda: object())
    monkeypatch.setattr(reranker, "rerank", slow_rerank)
    monkeypatch.setattr(
        reranker,
        "_settings",
        lambda: {
            "queue_capacity": 1,
            "inference_timeout_seconds": 0.2,
            "failure_threshold": 3,
            "circuit_cooldown_seconds": 60.0,
        },
    )
    try:
        with pytest.warns(RuntimeWarning, match="rerank_timeout"):
            hits = retriever.retrieve("query", ".", n=2, mode="rerank")
        assert started.is_set()
        assert [hit.rerank_score for hit in hits] == [None, None]
        assert [hit.rrf_rank for hit in hits] == [1, 2]
        assert all(hit.metadata["rerank_fallback"] is True for hit in hits)
        release.set()
    finally:
        release.set()
        reranker.reset_for_tests()


def test_load_model_uses_local_files_only_for_online_queries(monkeypatch):
    captured = {}

    class FakeCrossEncoder:
        def __init__(self, model_name, **kwargs):
            captured.update(model_name=model_name, **kwargs)

    monkeypatch.setattr("sentence_transformers.CrossEncoder", FakeCrossEncoder)
    monkeypatch.setattr(
        reranker,
        "_settings",
        lambda: {
            "enabled": True,
            "model_name": "local-model",
            "device": None,
            "max_length": 512,
            "cache_folder": ".cache",
            "local_files_only": True,
        },
    )
    reranker.reset_for_tests()
    reranker._load_model()
    assert captured["local_files_only"] is True
    reranker.reset_for_tests()


def test_model_cache_environment_override_preserves_absolute_path(
    monkeypatch, tmp_path
):
    requested_cache = tmp_path / "model-cache"
    monkeypatch.setenv("CODEPILOT_MODEL_CACHE", str(requested_cache))

    assert reranker._settings()["cache_folder"] == str(requested_cache)


def test_predict_calls_are_serialized(monkeypatch):
    state = {"active": 0, "maximum": 0}
    state_lock = threading.Lock()

    class FakeCrossEncoder:
        def predict(self, pairs, **_kwargs):
            with state_lock:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            time.sleep(0.02)
            with state_lock:
                state["active"] -= 1
            return np.zeros(len(pairs))

    model = FakeCrossEncoder()
    monkeypatch.setattr(reranker, "_load_model", lambda: model)
    monkeypatch.setattr(reranker, "_settings", lambda: {"batch_size": 1})
    threads = [
        threading.Thread(target=reranker.rerank, args=("q", [_hit(str(i), "x")], 1))
        for i in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert state["maximum"] == 1


def test_reranker_status_does_not_load_model(monkeypatch):
    reranker.reset_for_tests()
    monkeypatch.setattr(
        reranker,
        "_settings",
        lambda: {
            "enabled": True,
            "model_name": "test-model",
        },
    )
    current = reranker.status()
    assert current.enabled is True
    assert current.loaded is False
    assert current.model_name == "test-model"


def test_worker_settings_have_bounded_runtime_defaults():
    settings = reranker._settings()
    assert settings["queue_capacity"] == 8
    assert settings["inference_timeout_seconds"] == 30.0
    assert settings["failure_threshold"] == 3
    assert settings["circuit_cooldown_seconds"] == 60.0
