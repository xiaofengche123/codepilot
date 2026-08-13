"""BM25、RRF 与检索评估测试。"""

from rag.evaluate import calculate_metrics
import rag.retriever as retriever
from rag.retriever import SearchHit, bm25_rank, reciprocal_rank_fusion, tokenize_code


def _hit(uid: str, content: str = "", file: str = "test.py") -> SearchHit:
    return SearchHit(uid=uid, document=content, metadata={"file": file, "start_line": 1})


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
