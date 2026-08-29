"""BM25、RRF 与检索评估测试。"""

import pytest

from config import config
from rag.evaluate import calculate_metrics
from rag.query_features import extract_query_features
from rag.runtime_metrics import metrics_snapshot, reset_metrics_for_tests
import rag.retriever as retriever
from rag.retriever import SearchHit, bm25_rank, reciprocal_rank_fusion, tokenize_code


def _hit(uid: str, content: str = "", file: str = "test.py") -> SearchHit:
    return SearchHit(uid=uid, document=content, metadata={"file": file, "start_line": 1})


def test_retrieve_records_mode_and_latency_for_early_return():
    reset_metrics_for_tests()

    assert retriever.retrieve("", ".", mode="hybrid") == []

    snapshot = metrics_snapshot().to_dict()
    assert snapshot["retrieval_count"]["hybrid"] == 1
    assert snapshot["search_mode_total"]["hybrid"] == 1
    assert snapshot["retrieval_latency_ms_sum"]["hybrid"] >= 0


def test_tokenize_code_splits_identifiers_and_chinese():
    tokens = tokenize_code("validateUserToken HTTPServer 用户登录校验")
    assert "validate" in tokens
    assert "user" in tokens
    assert "token" in tokens
    assert "http" in tokens
    assert "server" in tokens
    assert "登录" in tokens


def test_bm25_prefers_exact_identifier_match():
    documents = [
        _hit("auth.py:1-3", "def validate_user_token(token): pass"),
        _hit("cache.py:1-3", "def clear_cache(): pass"),
    ]
    ranked = bm25_rank("validate user token", documents, limit=2)
    assert ranked[0].uid == "auth.py:1-3"
    assert ranked[0].score > 0


def test_rrf_deduplicates_and_boosts_shared_hit():
    vector = [_hit("semantic"), _hit("shared")]
    keyword = [_hit("shared"), _hit("keyword")]
    ranked = reciprocal_rank_fusion(vector, keyword, limit=3)
    assert ranked[0].uid == "shared"
    assert {hit.uid for hit in ranked[1:]} == {"semantic", "keyword"}
    assert ranked[0].vector_rank == 2
    assert ranked[0].bm25_rank == 1
    assert [hit.rrf_rank for hit in ranked] == [1, 2, 3]


def test_calculate_metrics_supports_file_and_chunk_labels():
    hits = [
        _hit("auth.py:10-20", file="auth.py"),
        _hit("user.py:1-8", file="user.py"),
    ]
    recall, mrr = calculate_metrics(hits, {"auth.py", "missing.py"})
    assert recall == 0.5
    assert mrr == 1.0


def test_calculate_metrics_tolerates_shifted_overlapping_chunk_ranges():
    hits = [
        SearchHit(
            uid="rag/retriever.py:122-153",
            document="",
            metadata={
                "file": "rag/retriever.py",
                "start_line": 122,
                "end_line": 153,
            },
        )
    ]
    recall, mrr = calculate_metrics(hits, {r"rag\retriever.py:119-150"})
    assert recall == 1.0
    assert mrr == 1.0


def test_calculate_metrics_rejects_incidental_range_overlap():
    hits = [
        SearchHit(
            uid="rag/retriever.py:180-216",
            document="",
            metadata={
                "file": "rag/retriever.py",
                "start_line": 180,
                "end_line": 216,
            },
        )
    ]
    recall, mrr = calculate_metrics(hits, {"rag/retriever.py:216-258"})
    assert recall == 0.0
    assert mrr == 0.0


def test_include_docs_reads_code_documents_and_legacy_records():
    class FakeCollection:
        def get(self, **kwargs):
            assert "where" not in kwargs
            return {
                "ids": ["code", "doc", "legacy"],
                "documents": ["source", "readme", "old"],
                "metadatas": [
                    {"file": "app.py", "content_type": "code"},
                    {"file": "README.md", "content_type": "document"},
                    {"file": "old.py"},
                ],
            }

    hits = retriever._collection_documents(FakeCollection(), include_docs=True)
    assert [hit.uid for hit in hits] == ["code", "doc", "legacy"]


def test_hybrid_retrieve_runs_two_retrievers_and_fuses(monkeypatch):
    class FakeCollection:
        def count(self):
            return 3

        def get(self, include, where=None):
            assert where == {"content_type": "code"}
            return {
                "ids": ["auth", "cache", "helper"],
                "documents": ["def login_user(): pass", "clear cache", "authentication helper"],
                "metadatas": [
                    {"file": "auth.py", "start_line": 1},
                    {"file": "cache.py", "start_line": 1},
                    {"file": "helper.py", "start_line": 1},
                ],
            }

        def query(self, **kwargs):
            assert kwargs["where"] == {"content_type": "code"}
            return {
                "ids": [["helper", "auth", "cache"]],
                "documents": [["authentication helper", "def login_user(): pass", "clear cache"]],
                "metadatas": [[
                    {"file": "helper.py", "start_line": 1},
                    {"file": "auth.py", "start_line": 1},
                    {"file": "cache.py", "start_line": 1},
                ]],
                "distances": [[0.1, 0.2, 0.9]],
            }

    class FakeEmbedding:
        def encode(self, *_args, **_kwargs):
            class Vector:
                def tolist(self):
                    return [[0.1, 0.2]]
            return Vector()

    monkeypatch.setattr(retriever, "_get_collection", lambda _project: FakeCollection())
    monkeypatch.setattr(retriever, "_get_model", lambda: FakeEmbedding())

    hits = retriever.retrieve("login user", ".", n=2, mode="hybrid")
    assert hits[0].uid == "auth"
    assert hits[0].vector_rank == 2
    assert hits[0].bm25_rank == 1


def test_adaptive_hybrid_consumes_router_plan_and_adds_safe_metadata(monkeypatch):
    vector = [
        _hit("vector.py:1-10", file="vector.py"),
        _hit("shared.py:1-10", file="shared.py"),
    ]
    keyword = [
        _hit("keyword.py:1-10", file="keyword.py"),
        _hit("shared.py:1-10", file="shared.py"),
    ]
    calls = []

    def fake_dual(*_args):
        calls.append(True)
        return vector, keyword

    monkeypatch.setattr(retriever, "_dual_rankings", fake_dual)
    features = extract_query_features("中文 mixed query across modules")
    hits = retriever._hybrid_candidates(
        "中文 mixed query across modules", object(), [], 2, False, 1.5, 0.75,
        features,
    )
    assert calls == [True]
    assert hits
    assert all(hit.metadata["adaptive_routing"] is True for hit in hits)
    assert hits[0].metadata["retrieval_router_version"] == "rule_router_v1"
    assert "query" not in hits[0].metadata
    assert "retrieval_reason_codes" in hits[0].metadata


def test_adaptive_failure_reuses_rankings_for_fixed_fallback(monkeypatch):
    vector = [_hit("vector.py:1-10", file="vector.py")]
    keyword = [_hit("keyword.py:1-10", file="keyword.py")]
    calls = []

    def fake_dual(*_args):
        calls.append(True)
        return vector, keyword

    monkeypatch.setattr(retriever, "_dual_rankings", fake_dual)
    monkeypatch.setattr(
        retriever,
        "route_retrieval",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("router failed")),
    )
    with pytest.warns(RuntimeWarning, match="falling back to fixed RRF"):
        hits = retriever._hybrid_candidates(
            "query", object(), [], 2, False, 1.5, 0.75,
            extract_query_features("query"),
        )
    assert calls == [True]
    assert hits
    assert all(hit.metadata["adaptive_routing_fallback"] is True for hit in hits)


def test_retrieve_passes_features_only_when_adaptive_is_enabled(monkeypatch):
    observed = []
    monkeypatch.setattr(retriever, "_get_collection", lambda _project: object())
    monkeypatch.setattr(retriever, "_collection_documents", lambda *_args: [])

    def fake_candidates(*args):
        observed.append(args[-1])
        return []

    monkeypatch.setattr(retriever, "_hybrid_candidates", fake_candidates)
    original_get = config.get

    def adaptive_get(key, default=None):
        if key == "rag.adaptive_routing.enabled":
            return True
        return original_get(key, default)

    monkeypatch.setattr(config, "get", adaptive_get)
    retriever.retrieve("README documentation", ".", n=2, mode="hybrid")
    assert observed and observed[0] is not None
    assert observed[0].requests_documentation is True


def test_default_hybrid_keeps_fixed_rrf_without_adaptive_metadata(monkeypatch):
    vector = [_hit("vector.py:1-10", file="vector.py")]
    keyword = [_hit("keyword.py:1-10", file="keyword.py")]
    monkeypatch.setattr(
        retriever, "_dual_rankings", lambda *_args: (vector, keyword)
    )
    hits = retriever._hybrid_candidates(
        "query", object(), [], 2, False, 1.5, 0.75, None
    )
    assert hits
    assert all("adaptive_routing" not in hit.metadata for hit in hits)


def test_adaptive_switch_does_not_change_pure_vector_mode(monkeypatch):
    observed = []
    monkeypatch.setattr(retriever, "_get_collection", lambda _project: object())
    monkeypatch.setattr(
        retriever,
        "_vector_rank",
        lambda _query, _collection, _n, include_docs: observed.append(include_docs) or [],
    )
    monkeypatch.setattr(
        retriever,
        "extract_query_features",
        lambda _query: (_ for _ in ()).throw(AssertionError("must not route vector")),
    )
    original_get = config.get

    def adaptive_get(key, default=None):
        if key == "rag.adaptive_routing.enabled":
            return True
        return original_get(key, default)

    monkeypatch.setattr(config, "get", adaptive_get)
    assert retriever.retrieve("README documentation", ".", mode="vector") == []
    assert observed == [False]


def test_rerank_uses_rrf_top_30_then_truncates_to_requested_k(monkeypatch):
    candidates = [_hit(f"chunk-{i}") for i in range(30)]
    observed = {}
    monkeypatch.setattr(retriever, "_get_collection", lambda _project: object())
    monkeypatch.setattr(retriever, "_collection_documents", lambda *_args: [])

    def fake_candidates(*_args):
        observed["candidate_limit"] = _args[3]
        return candidates

    def fake_rerank(_query, hits, limit):
        observed["rerank_count"] = len(hits)
        observed["final_limit"] = limit
        return hits[:limit]

    monkeypatch.setattr(retriever, "_hybrid_candidates", fake_candidates)
    monkeypatch.setattr("rag.reranker.rerank", fake_rerank)
    hits = retriever.retrieve("query", ".", n=10, mode="rerank")
    assert len(hits) == 10
    assert observed == {
        "candidate_limit": 30,
        "rerank_count": 30,
        "final_limit": 10,
    }
