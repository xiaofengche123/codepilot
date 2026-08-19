"""Deterministic rule router tests for ROUTE-004."""

from dataclasses import dataclass

import pytest

from rag.query_features import extract_query_features
from rag.retrieval_confidence import calculate_retrieval_confidence
from rag.retrieval_router import (
    BALANCED_BM25_WEIGHT,
    BALANCED_VECTOR_WEIGHT,
    BASELINE_BM25_WEIGHT,
    BASELINE_VECTOR_WEIGHT,
    DEFAULT_CANDIDATE_COUNT,
    EXPANDED_CANDIDATE_COUNT,
    EXACT_BM25_WEIGHT,
    EXACT_VECTOR_WEIGHT,
    MIXED_CANDIDATE_COUNT,
    NATURAL_LANGUAGE_BM25_WEIGHT,
    NATURAL_LANGUAGE_VECTOR_WEIGHT,
    route_retrieval,
)


@dataclass
class FakeHit:
    uid: str
    document: str = ""
    metadata: dict | None = None
    score: float = 0.0

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


def _hit(uid, *, document="", file=None, score=0.0):
    metadata = {} if file is None else {"file": file}
    return FakeHit(uid, document, metadata, score)


def _route(query, confidence=None):
    return route_retrieval(extract_query_features(query), confidence)


def test_empty_query_uses_fixed_compatibility_plan():
    plan = _route("")
    assert plan.bm25_weight == BASELINE_BM25_WEIGHT
    assert plan.vector_weight == BASELINE_VECTOR_WEIGHT
    assert plan.candidate_count == DEFAULT_CANDIDATE_COUNT
    assert "query_empty" in plan.reason_codes


@pytest.mark.parametrize(
    "query",
    [
        "AgentSession.run",
        "rag/retriever.py",
        "config.rag.reranker.enabled",
        "ModuleNotFoundError: No module named chromadb",
        'File "agent.py", line 42, in run',
    ],
)
def test_exact_code_queries_favor_bm25(query):
    plan = _route(query)
    assert plan.bm25_weight == EXACT_BM25_WEIGHT
    assert plan.vector_weight == EXACT_VECTOR_WEIGHT
    assert "query_exact_code" in plan.reason_codes


@pytest.mark.parametrize(
    "query",
    ["where is authentication handled", "登录认证逻辑在哪里"],
)
def test_natural_language_queries_favor_vector_recall(query):
    plan = _route(query)
    assert plan.bm25_weight == NATURAL_LANGUAGE_BM25_WEIGHT
    assert plan.vector_weight == NATURAL_LANGUAGE_VECTOR_WEIGHT
    assert "query_natural_language" in plan.reason_codes


def test_mixed_language_query_uses_balanced_fusion():
    plan = _route("登录流程 authentication handler")
    assert plan.bm25_weight == BALANCED_BM25_WEIGHT
    assert plan.vector_weight == BALANCED_VECTOR_WEIGHT
    assert plan.candidate_count == MIXED_CANDIDATE_COUNT
    assert "query_mixed_language" in plan.reason_codes


@pytest.mark.parametrize(
    "query",
    [
        "查找 server 到 task_queue 的调用关系",
        "compare agent.py and execution_state.py",
    ],
)
def test_cross_module_query_has_highest_query_rule_precedence(query):
    plan = _route(query)
    assert plan.bm25_weight == BALANCED_BM25_WEIGHT
    assert plan.vector_weight == BALANCED_VECTOR_WEIGHT
    assert plan.candidate_count == EXPANDED_CANDIDATE_COUNT
    assert "query_cross_module" in plan.reason_codes
    assert "candidate_pool_expanded" in plan.reason_codes


@pytest.mark.parametrize(
    "query",
    [
        "where is the API documentation",
        "README usage guide",
        "查看配置使用说明文档",
    ],
)
def test_explicit_documentation_intent_enables_docs(query):
    features = extract_query_features(query)
    plan = route_retrieval(features)
    assert features.requests_documentation is True
    assert "documentation_intent" in features.reason_codes
    assert plan.include_docs is True
    assert "documentation_requested" in plan.reason_codes


def test_regular_explanation_query_does_not_enable_docs():
    plan = _route("explain how authentication works")
    assert plan.include_docs is False


def test_low_overlap_top1_disagreement_balances_and_expands():
    confidence = calculate_retrieval_confidence(
        "AgentSession.run",
        [_hit("vector-a", document="AgentSession"), _hit("shared")],
        [_hit("bm25-a", document="run"), _hit("shared")],
        top_k=10,
    )
    plan = _route("AgentSession.run", confidence)
    assert confidence.top1_agreement is False
    assert confidence.overlap_ratio == 0.1
    assert plan.bm25_weight == BALANCED_BM25_WEIGHT
    assert plan.vector_weight == BALANCED_VECTOR_WEIGHT
    assert plan.candidate_count == EXPANDED_CANDIDATE_COUNT
    assert "ranking_disagreement" in plan.reason_codes


def test_top1_agreement_is_recorded_without_overriding_query_family():
    confidence = calculate_retrieval_confidence(
        "where is authentication handled",
        [_hit("shared", score=0.9), _hit("v2", score=0.5)],
        [_hit("shared"), _hit("b2")],
    )
    plan = _route("where is authentication handled", confidence)
    assert plan.bm25_weight == NATURAL_LANGUAGE_BM25_WEIGHT
    assert plan.vector_weight == NATURAL_LANGUAGE_VECTOR_WEIGHT
    assert "ranking_agreement" in plan.reason_codes


def test_full_identifier_coverage_is_explained_but_not_a_probability():
    confidence = calculate_retrieval_confidence(
        "AgentSession.run",
        [_hit("shared", document="AgentSession run", score=0.9),
         _hit("v2", score=0.5)],
        [_hit("shared")],
    )
    plan = _route("AgentSession.run", confidence)
    assert confidence.identifier_coverage == 1.0
    assert "identifiers_fully_covered" in plan.reason_codes
    assert "probability" not in plan.reason.casefold()


def test_missing_vector_results_routes_to_bm25_only():
    confidence = calculate_retrieval_confidence(
        "where is authentication handled", [], [_hit("b")]
    )
    plan = _route("where is authentication handled", confidence)
    assert (plan.bm25_weight, plan.vector_weight) == (1.0, 0.0)
    assert "bm25_only_available" in plan.reason_codes


def test_missing_bm25_results_routes_to_vector_only():
    confidence = calculate_retrieval_confidence(
        "AgentSession.run", [_hit("v")], []
    )
    plan = _route("AgentSession.run", confidence)
    assert (plan.bm25_weight, plan.vector_weight) == (0.0, 1.0)
    assert "vector_only_available" in plan.reason_codes


def test_no_candidates_returns_to_compatibility_defaults():
    confidence = calculate_retrieval_confidence("AgentSession.run", [], [])
    plan = _route("AgentSession.run", confidence)
    assert plan.bm25_weight == BASELINE_BM25_WEIGHT
    assert plan.vector_weight == BASELINE_VECTOR_WEIGHT
    assert plan.candidate_count == DEFAULT_CANDIDATE_COUNT
    assert "no_candidates" in plan.reason_codes


@pytest.mark.parametrize(
    "query",
    [
        "",
        "AgentSession.run",
        "where is authentication handled",
        "登录流程 authentication handler",
        "compare agent.py and execution_state.py",
    ],
)
def test_route_004_never_enables_rerank(query):
    plan = _route(query)
    assert plan.rerank is False
    assert "rerank_deferred_to_policy" in plan.reason_codes


def test_reason_is_fixed_bounded_and_does_not_copy_query():
    query = "SensitiveClass.private_handler"
    plan = _route(query)
    assert query not in plan.reason
    assert len(plan.reason) <= 500
    assert len(plan.reason_codes) <= 16


def test_router_is_deterministic_and_does_not_mutate_inputs():
    features = extract_query_features("AgentSession.run")
    confidence = calculate_retrieval_confidence(
        "AgentSession.run", [_hit("a", document="AgentSession")], []
    )
    before = confidence.to_dict()
    assert route_retrieval(features, confidence) == route_retrieval(
        features, confidence
    )
    assert confidence.to_dict() == before


def test_router_requires_typed_contracts():
    features = extract_query_features("query")
    with pytest.raises(TypeError, match="features"):
        route_retrieval("query")
    with pytest.raises(TypeError, match="confidence"):
        route_retrieval(features, {})


def test_router_does_not_import_models_config_or_retriever(monkeypatch):
    import builtins

    original_import = builtins.__import__
    forbidden = {
        "chromadb",
        "sentence_transformers",
        "config",
        "rag.indexer",
        "rag.reranker",
        "rag.retriever",
    }

    def guarded_import(name, *args, **kwargs):
        if name in forbidden or any(name.startswith(item + ".") for item in forbidden):
            raise AssertionError(f"unexpected runtime import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert _route("where is authentication handled").rerank is False
