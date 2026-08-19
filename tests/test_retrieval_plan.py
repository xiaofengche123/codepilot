"""Validation and serialization tests for the ROUTE-002 plan contract."""

from dataclasses import FrozenInstanceError, fields
import json

import pytest

from rag.retrieval_plan import (
    BASELINE_RETRIEVAL_PLAN,
    MAX_CANDIDATE_COUNT,
    MAX_REASON_CHARS,
    MAX_REASON_CODES,
    MAX_RRF_K,
    RETRIEVAL_PLAN_SCHEMA_VERSION,
    RetrievalPlan,
    baseline_retrieval_plan,
)


def _plan(**overrides) -> RetrievalPlan:
    values = {
        "bm25_weight": 2.0,
        "vector_weight": 0.25,
        "rrf_k": 10,
        "candidate_count": 30,
        "include_docs": False,
        "rerank": False,
        "reason": "Deterministic test plan.",
        "reason_codes": ("test_plan",),
    }
    values.update(overrides)
    return RetrievalPlan(**values)


def test_baseline_plan_matches_current_fixed_product_defaults():
    plan = baseline_retrieval_plan()
    assert plan is BASELINE_RETRIEVAL_PLAN
    assert plan.bm25_weight == 2.0
    assert plan.vector_weight == 0.25
    assert plan.rrf_k == 10
    assert plan.candidate_count == 30
    assert plan.include_docs is False
    assert plan.rerank is False
    assert plan.reason_codes == ("fixed_rrf_compatibility",)


def test_baseline_plan_stays_in_sync_with_config_defaults():
    from config import DEFAULTS

    rag_defaults = DEFAULTS["rag"]
    plan = baseline_retrieval_plan()
    assert plan.bm25_weight == rag_defaults["bm25_weight"]
    assert plan.vector_weight == rag_defaults["vector_weight"]
    assert plan.rrf_k == rag_defaults["rrf_k"]
    assert plan.candidate_count == rag_defaults["reranker"]["candidate_count"]
    assert plan.include_docs is rag_defaults["include_docs"]
    assert plan.rerank is rag_defaults["reranker"]["enabled"]


def test_plan_is_frozen_and_normalizes_numeric_types():
    plan = _plan(bm25_weight=2, vector_weight=1)
    assert type(plan.bm25_weight) is float
    assert type(plan.vector_weight) is float
    with pytest.raises(FrozenInstanceError):
        plan.rrf_k = 20


def test_to_dict_is_json_ready_and_versioned():
    payload = json.loads(json.dumps(_plan().to_dict()))
    assert payload["schema_version"] == RETRIEVAL_PLAN_SCHEMA_VERSION
    assert payload["reason_codes"] == ["test_plan"]
    assert payload["rerank"] is False


def test_plan_contract_does_not_store_query_or_results():
    names = {field.name for field in fields(RetrievalPlan)}
    assert "query" not in names
    assert "raw_query" not in names
    assert "results" not in names
    assert "hits" not in names


@pytest.mark.parametrize(
    ("bm25_weight", "vector_weight"),
    [(0.0, 1.0), (1.0, 0.0), (0.5, 0.5)],
)
def test_one_or_both_positive_weights_are_valid(bm25_weight, vector_weight):
    plan = _plan(bm25_weight=bm25_weight, vector_weight=vector_weight)
    assert plan.bm25_weight == bm25_weight
    assert plan.vector_weight == vector_weight


def test_both_zero_weights_are_rejected():
    with pytest.raises(ValueError, match="at least one retrieval weight"):
        _plan(bm25_weight=0.0, vector_weight=0.0)


@pytest.mark.parametrize("field", ["bm25_weight", "vector_weight"])
@pytest.mark.parametrize(
    "value",
    [
        -0.1,
        float("nan"),
        float("inf"),
        -float("inf"),
        pytest.param(10**10_000, id="overflowing_integer"),
    ],
)
def test_invalid_weights_are_rejected(field, value):
    with pytest.raises(ValueError):
        _plan(**{field: value})


@pytest.mark.parametrize("field", ["bm25_weight", "vector_weight"])
@pytest.mark.parametrize("value", [True, "1.0", None])
def test_weights_reject_boolean_and_non_numeric_types(field, value):
    with pytest.raises(TypeError):
        _plan(**{field: value})


@pytest.mark.parametrize("value", [0, -1, MAX_RRF_K + 1])
def test_rrf_k_must_be_within_safety_bounds(value):
    with pytest.raises(ValueError, match="rrf_k"):
        _plan(rrf_k=value)


@pytest.mark.parametrize("value", [True, 10.0, "10"])
def test_rrf_k_requires_an_integer(value):
    with pytest.raises(TypeError, match="rrf_k"):
        _plan(rrf_k=value)


@pytest.mark.parametrize("value", [0, -1, MAX_CANDIDATE_COUNT + 1])
def test_candidate_count_is_bounded(value):
    with pytest.raises(ValueError, match="candidate_count"):
        _plan(candidate_count=value)


def test_upper_safety_boundaries_are_accepted():
    plan = _plan(rrf_k=MAX_RRF_K, candidate_count=MAX_CANDIDATE_COUNT)
    assert plan.rrf_k == MAX_RRF_K
    assert plan.candidate_count == MAX_CANDIDATE_COUNT


@pytest.mark.parametrize("value", [True, 30.0, "30"])
def test_candidate_count_requires_an_integer(value):
    with pytest.raises(TypeError, match="candidate_count"):
        _plan(candidate_count=value)


@pytest.mark.parametrize("field", ["include_docs", "rerank"])
@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_boolean_flags_require_actual_booleans(field, value):
    with pytest.raises(TypeError):
        _plan(**{field: value})


def test_reason_is_trimmed_and_bounded():
    assert _plan(reason="  explainable plan  ").reason == "explainable plan"
    with pytest.raises(ValueError, match="must not be empty"):
        _plan(reason="   ")
    with pytest.raises(ValueError, match="must not exceed"):
        _plan(reason="x" * (MAX_REASON_CHARS + 1))
    with pytest.raises(ValueError, match="must not exceed"):
        _plan(reason=" " * (MAX_REASON_CHARS + 1))
    with pytest.raises(ValueError, match="single line"):
        _plan(reason="first\nsecond")


@pytest.mark.parametrize(
    "reason_codes",
    [
        ["not_a_tuple"],
        ("UPPERCASE",),
        ("has-hyphen",),
        ("1_starts_with_digit",),
        ("duplicate", "duplicate"),
        tuple(f"reason_{index}" for index in range(MAX_REASON_CODES + 1)),
    ],
)
def test_reason_codes_have_stable_bounded_format(reason_codes):
    with pytest.raises((TypeError, ValueError)):
        _plan(reason_codes=reason_codes)


def test_empty_reason_codes_are_allowed_for_manual_plans():
    assert _plan(reason_codes=()).reason_codes == ()


def test_rerank_plan_has_same_stable_shape_as_non_rerank_plan():
    rerank = _plan(rerank=True, candidate_count=50)
    hybrid = _plan(rerank=False, candidate_count=50)
    assert rerank.candidate_count == hybrid.candidate_count == 50
    assert rerank.to_dict().keys() == hybrid.to_dict().keys()


def test_creation_does_not_import_retriever_models_or_config(monkeypatch):
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
    assert _plan().rrf_k == 10
