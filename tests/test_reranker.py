import numpy as np
import pytest
import threading
import time

import rag.reranker as reranker
import rag.retriever as retriever
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
        "rerank",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    with pytest.warns(RuntimeWarning, match="falling back to RRF"):
        hits = retriever.retrieve("query", ".", n=1, mode="rerank")

    assert [hit.uid for hit in hits] == ["one"]
    assert hits[0].metadata["rerank_fallback"] is True
    assert hits[0].rrf_rank == 1


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


def test_model_cache_environment_override_stays_on_requested_drive(monkeypatch):
    monkeypatch.setenv("CODEPILOT_MODEL_CACHE", "D:/codepilot/.codepilot/model-cache")
    assert reranker._settings()["cache_folder"].replace("\\", "/") == (
        "D:/codepilot/.codepilot/model-cache"
    )


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
