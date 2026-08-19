"""Deterministic RerankPolicy and latency-budget tests for ROUTE-005."""

from dataclasses import FrozenInstanceError, replace
import json

import pytest

from rag.query_features import extract_query_features
from rag.rerank_policy import (
    LatencyBudget,
    MAX_RERANK_CANDIDATE_COUNT,
    RerankCostEstimate,
    decide_rerank,
)
from rag.retrieval_confidence import RetrievalConfidenceSignals
from rag.retrieval_router import route_retrieval


def _confidence(**overrides):
    values = {
        "top_k": 10,
        "vector_result_count": 10,
        "bm25_result_count": 10,
        "overlap_count": 1,
        "overlap_ratio": 0.1,
        "top1_agreement": False,
        "query_identifier_count": 2,
        "matched_identifier_count": 1,
        "identifier_coverage": 0.5,
        "vector_top_score_margin": 0.1,
        "candidate_count": 19,
        "candidates_with_file_count": 19,
        "unique_file_count": 5,
        "file_diversity_ratio": 0.263158,
        "reason_codes": ("top1_disagreement", "ranking_overlap"),
    }
    values.update(overrides)
    return RetrievalConfidenceSignals(**values)


def _inputs(query="compare agent.py and execution_state.py", confidence=None):
    features = extract_query_features(query)
    evidence = confidence or _confidence()
    return route_retrieval(features, evidence), features, evidence


def _decide(query="compare agent.py and execution_state.py", confidence=None, **kwargs):
    plan, features, evidence = _inputs(query, confidence)
    defaults = {
        "budget": LatencyBudget(400, elapsed_ms=100, reserve_ms=50),
        "cost_estimate": RerankCostEstimate(20, 5),
        "allowed": True,
        "model_available": True,
    }
    defaults.update(kwargs)
    return decide_rerank(plan, features, evidence, **defaults)


def test_enables_cross_module_disagreement_within_budget():
    decision = _decide()
    assert decision.enabled is True
    assert decision.plan.rerank is True
    assert decision.estimated_latency_ms == 170.0
    assert decision.remaining_budget_ms == 250.0
    assert "rerank_enabled_cross_module_disagreement" in decision.reason_codes


def test_exact_budget_boundary_is_allowed():
    decision = _decide(
        budget=LatencyBudget(170),
        cost_estimate=RerankCostEstimate(20, 5),
    )
    assert decision.enabled is True


def test_enabled_plan_caps_candidate_count_without_mutating_source():
    plan, features, evidence = _inputs()
    assert plan.candidate_count == 50
    decision = decide_rerank(
        plan,
        features,
        evidence,
        LatencyBudget(1000),
        RerankCostEstimate(0, 1),
        allowed=True,
        model_available=True,
    )
    assert decision.plan.candidate_count == MAX_RERANK_CANDIDATE_COUNT
    assert decision.selected_candidate_count == MAX_RERANK_CANDIDATE_COUNT
    assert plan.candidate_count == 50 and plan.rerank is False
    assert "rerank_candidate_cap_applied" in decision.reason_codes
    assert "rerank_deferred_to_policy" not in decision.plan.reason_codes


def test_budget_exceeded_keeps_original_plan_shape_and_disables():
    plan, features, evidence = _inputs()
    decision = decide_rerank(
        plan,
        features,
        evidence,
        LatencyBudget(100, elapsed_ms=40, reserve_ms=10),
        RerankCostEstimate(20, 2),
        allowed=True,
        model_available=True,
    )
    assert decision.enabled is False
    assert decision.plan.rerank is False
    assert decision.plan.candidate_count == plan.candidate_count
    assert decision.remaining_budget_ms == 50.0
    assert decision.estimated_latency_ms == 80.0
    assert "rerank_latency_budget_exceeded" in decision.reason_codes


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"allowed": False}, "rerank_disabled_by_caller"),
        ({"model_available": False}, "rerank_model_unavailable"),
        ({"budget": None}, "rerank_latency_data_missing"),
        ({"cost_estimate": None}, "rerank_latency_data_missing"),
    ],
)
def test_explicit_safety_gates_deny_rerank(kwargs, code):
    assert code in _decide(**kwargs).reason_codes


def test_missing_confidence_is_denied():
    plan, features, _ = _inputs()
    decision = decide_rerank(
        plan, features, None, allowed=True, model_available=True
    )
    assert decision.enabled is False
    assert "rerank_confidence_missing" in decision.reason_codes


def test_non_cross_module_query_is_denied():
    decision = _decide("where is authentication handled")
    assert decision.enabled is False
    assert "rerank_query_not_cross_module" in decision.reason_codes


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"vector_result_count": 0, "top1_agreement": None}, "rerank_dual_ranking_required"),
        ({"bm25_result_count": 0, "top1_agreement": None}, "rerank_dual_ranking_required"),
        ({"top1_agreement": True}, "rerank_disagreement_required"),
        ({"overlap_ratio": 0.200001}, "rerank_disagreement_required"),
    ],
)
def test_ranking_evidence_gates(overrides, code):
    assert code in _decide(confidence=_confidence(**overrides)).reason_codes


def test_high_confidence_exact_match_skips_even_with_cross_module_intent():
    confidence = _confidence(
        top1_agreement=True,
        query_identifier_count=2,
        matched_identifier_count=2,
        identifier_coverage=1.0,
    )
    decision = _decide(confidence=confidence)
    assert decision.enabled is False
    assert "rerank_skipped_high_confidence_exact" in decision.reason_codes


def test_budget_remaining_is_clamped_and_accounts_for_reserve():
    assert LatencyBudget(100, elapsed_ms=80, reserve_ms=30).remaining_ms == 0.0
    assert LatencyBudget(100, elapsed_ms=20, reserve_ms=30).remaining_ms == 50.0


@pytest.mark.parametrize("field", ["total_ms", "elapsed_ms", "reserve_ms"])
@pytest.mark.parametrize("value", [True, "1", None])
def test_latency_budget_rejects_non_numeric_values(field, value):
    values = {"total_ms": 100, "elapsed_ms": 0, "reserve_ms": 0}
    values[field] = value
    with pytest.raises(TypeError):
        LatencyBudget(**values)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_latency_budget_rejects_non_finite_or_negative_values(value):
    with pytest.raises(ValueError):
        LatencyBudget(100, elapsed_ms=value)


def test_latency_budget_requires_positive_total():
    with pytest.raises(ValueError):
        LatencyBudget(0)


@pytest.mark.parametrize("field", ["fixed_ms", "per_candidate_ms"])
@pytest.mark.parametrize("value", [True, "1", float("nan"), float("inf"), -1])
def test_cost_estimate_validates_inputs(field, value):
    values = {"fixed_ms": 0, "per_candidate_ms": 1}
    values[field] = value
    exception = TypeError if isinstance(value, (bool, str)) else ValueError
    with pytest.raises(exception):
        RerankCostEstimate(**values)


def test_cost_formula_and_candidate_bounds():
    estimate = RerankCostEstimate(10, 2.5)
    assert estimate.estimate_ms(30) == 85.0
    with pytest.raises(TypeError):
        estimate.estimate_ms(True)
    with pytest.raises(ValueError):
        estimate.estimate_ms(31)


def test_decision_is_frozen_json_ready_bounded_and_query_free():
    secret_query = "compare secret_alpha.py and secret_beta.py"
    decision = _decide(secret_query)
    payload = decision.to_dict()
    json.dumps(payload)
    assert secret_query not in repr(decision)
    assert secret_query not in json.dumps(payload)
    assert len(decision.plan.reason) <= 500
    with pytest.raises(FrozenInstanceError):
        decision.enabled = False


def test_repeated_calls_are_equal():
    assert _decide() == _decide()


def test_policy_can_reconsider_an_existing_policy_plan():
    first = _decide()
    features = extract_query_features("compare agent.py and execution_state.py")
    second = decide_rerank(
        first.plan,
        features,
        _confidence(),
        LatencyBudget(1),
        RerankCostEstimate(20, 5),
        allowed=True,
        model_available=True,
    )
    assert second.enabled is False
    assert second.reason_codes == (
        "rerank_policy_v1",
        "rerank_latency_budget_exceeded",
    )
    assert len(second.plan.reason_codes) == len(set(second.plan.reason_codes))


@pytest.mark.parametrize(
    ("position", "value"),
    [(0, object()), (1, object()), (2, object()), (3, object()), (4, object())],
)
def test_policy_rejects_invalid_contract_objects(position, value):
    plan, features, evidence = _inputs()
    args = [plan, features, evidence, LatencyBudget(400), RerankCostEstimate(1, 1)]
    args[position] = value
    with pytest.raises(TypeError):
        decide_rerank(*args)


@pytest.mark.parametrize("keyword", ["allowed", "model_available"])
def test_policy_requires_strict_boolean_flags(keyword):
    plan, features, evidence = _inputs()
    with pytest.raises(TypeError):
        decide_rerank(plan, features, evidence, **{keyword: 1})


def test_module_has_no_runtime_or_model_dependencies():
    import rag.rerank_policy as module

    names = set(module.__dict__)
    assert "Retriever" not in names
    assert "CrossEncoderReranker" not in names
    assert "config" not in names


def test_policy_preserves_non_rerank_plan_parameters():
    plan, features, evidence = _inputs()
    decision = decide_rerank(
        plan,
        features,
        evidence,
        LatencyBudget(1000),
        RerankCostEstimate(0, 1),
        allowed=True,
        model_available=True,
    )
    assert replace(decision.plan, candidate_count=plan.candidate_count, rerank=False,
                   reason=plan.reason, reason_codes=plan.reason_codes) == plan
