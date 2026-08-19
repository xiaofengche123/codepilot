"""Deterministic, budget-aware policy for optional cross-encoder reranking.

The policy consumes already-computed query and ranking evidence.  It does not
measure time, load a model, execute retrieval, or call the reranker.  Latency
budgets and cost estimates are explicit caller inputs because machine-specific
measurements must not become hidden universal constants.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from rag.query_features import QueryFeatures
from rag.retrieval_confidence import RetrievalConfidenceSignals
from rag.retrieval_plan import RetrievalPlan


RERANK_POLICY_VERSION = "rerank_policy_v1"
RERANK_DECISION_SCHEMA_VERSION = 1
MAX_RERANK_CANDIDATE_COUNT = 30
LOW_RANKING_OVERLAP_RATIO = 0.2
IDENTIFIER_DOMINANT_RATIO = 0.5


@dataclass(frozen=True)
class LatencyBudget:
    """A caller-provided end-to-end latency budget in milliseconds."""

    total_ms: float
    elapsed_ms: float = 0.0
    reserve_ms: float = 0.0

    def __post_init__(self) -> None:
        total = _finite_number("total_ms", self.total_ms, positive=True)
        elapsed = _finite_number("elapsed_ms", self.elapsed_ms)
        reserve = _finite_number("reserve_ms", self.reserve_ms)
        object.__setattr__(self, "total_ms", total)
        object.__setattr__(self, "elapsed_ms", elapsed)
        object.__setattr__(self, "reserve_ms", reserve)

    @property
    def remaining_ms(self) -> float:
        return round(max(0.0, self.total_ms - self.elapsed_ms - self.reserve_ms), 6)

    def to_dict(self) -> dict[str, float]:
        return {
            "total_ms": self.total_ms,
            "elapsed_ms": self.elapsed_ms,
            "reserve_ms": self.reserve_ms,
            "remaining_ms": self.remaining_ms,
        }


@dataclass(frozen=True)
class RerankCostEstimate:
    """Caller-supplied fixed and per-candidate latency estimate."""

    fixed_ms: float
    per_candidate_ms: float

    def __post_init__(self) -> None:
        fixed = _finite_number("fixed_ms", self.fixed_ms)
        per_candidate = _finite_number("per_candidate_ms", self.per_candidate_ms)
        object.__setattr__(self, "fixed_ms", fixed)
        object.__setattr__(self, "per_candidate_ms", per_candidate)

    def estimate_ms(self, candidate_count: int) -> float:
        if isinstance(candidate_count, bool) or not isinstance(candidate_count, int):
            raise TypeError("candidate_count must be an integer")
        if candidate_count < 1 or candidate_count > MAX_RERANK_CANDIDATE_COUNT:
            raise ValueError(
                f"candidate_count must be between 1 and {MAX_RERANK_CANDIDATE_COUNT}"
            )
        estimate = self.fixed_ms + self.per_candidate_ms * candidate_count
        if not math.isfinite(estimate):
            raise ValueError("estimated rerank latency must be finite")
        return round(estimate, 6)

    def to_dict(self) -> dict[str, float]:
        return {
            "fixed_ms": self.fixed_ms,
            "per_candidate_ms": self.per_candidate_ms,
        }


@dataclass(frozen=True)
class RerankDecision:
    """An immutable policy outcome containing a validated replacement plan."""

    plan: RetrievalPlan
    enabled: bool
    selected_candidate_count: int
    remaining_budget_ms: float | None
    estimated_latency_ms: float | None
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RERANK_DECISION_SCHEMA_VERSION,
            "enabled": self.enabled,
            "selected_candidate_count": self.selected_candidate_count,
            "remaining_budget_ms": self.remaining_budget_ms,
            "estimated_latency_ms": self.estimated_latency_ms,
            "reason_codes": list(self.reason_codes),
            "plan": self.plan.to_dict(),
        }


def decide_rerank(
    plan: RetrievalPlan,
    features: QueryFeatures,
    confidence: RetrievalConfidenceSignals | None,
    budget: LatencyBudget | None = None,
    cost_estimate: RerankCostEstimate | None = None,
    *,
    allowed: bool = False,
    model_available: bool = False,
) -> RerankDecision:
    """Return an explainable rerank decision without executing the plan.

    Enabling requires explicit caller opt-in, an available local model,
    cross-module intent, two disagreeing low-overlap rankings, and a supplied
    estimate that fits the remaining budget.  These are conservative lexical
    and ranking heuristics, not semantic truth or confidence probabilities.
    """
    if not isinstance(plan, RetrievalPlan):
        raise TypeError("plan must be RetrievalPlan")
    if not isinstance(features, QueryFeatures):
        raise TypeError("features must be QueryFeatures")
    if confidence is not None and not isinstance(confidence, RetrievalConfidenceSignals):
        raise TypeError("confidence must be RetrievalConfidenceSignals or None")
    if budget is not None and not isinstance(budget, LatencyBudget):
        raise TypeError("budget must be LatencyBudget or None")
    if cost_estimate is not None and not isinstance(cost_estimate, RerankCostEstimate):
        raise TypeError("cost_estimate must be RerankCostEstimate or None")
    if type(allowed) is not bool:
        raise TypeError("allowed must be a boolean")
    if type(model_available) is not bool:
        raise TypeError("model_available must be a boolean")

    selected_count = min(plan.candidate_count, MAX_RERANK_CANDIDATE_COUNT)
    remaining = budget.remaining_ms if budget is not None else None
    estimated = (
        cost_estimate.estimate_ms(selected_count)
        if cost_estimate is not None
        else None
    )

    if not allowed:
        outcome = "rerank_disabled_by_caller"
    elif not model_available:
        outcome = "rerank_model_unavailable"
    elif confidence is None:
        outcome = "rerank_confidence_missing"
    elif _is_high_confidence_exact(features, confidence):
        outcome = "rerank_skipped_high_confidence_exact"
    elif not features.has_cross_module_intent:
        outcome = "rerank_query_not_cross_module"
    elif confidence.vector_result_count == 0 or confidence.bm25_result_count == 0:
        outcome = "rerank_dual_ranking_required"
    elif not (
        confidence.top1_agreement is False
        and confidence.overlap_ratio <= LOW_RANKING_OVERLAP_RATIO
    ):
        outcome = "rerank_disagreement_required"
    elif budget is None or cost_estimate is None:
        outcome = "rerank_latency_data_missing"
    elif estimated is None or remaining is None or estimated > remaining:
        outcome = "rerank_latency_budget_exceeded"
    else:
        outcome = "rerank_enabled_cross_module_disagreement"

    enabled = outcome == "rerank_enabled_cross_module_disagreement"
    policy_codes = [RERANK_POLICY_VERSION, outcome]
    if enabled and selected_count < plan.candidate_count:
        policy_codes.append("rerank_candidate_cap_applied")
    reason_codes = _combined_reason_codes(plan.reason_codes, policy_codes)
    replacement = RetrievalPlan(
        bm25_weight=plan.bm25_weight,
        vector_weight=plan.vector_weight,
        rrf_k=plan.rrf_k,
        candidate_count=selected_count if enabled else plan.candidate_count,
        include_docs=plan.include_docs,
        rerank=enabled,
        reason=f"Rerank policy v1: {outcome.replace('_', ' ')}.",
        reason_codes=reason_codes,
    )
    return RerankDecision(
        plan=replacement,
        enabled=enabled,
        selected_candidate_count=selected_count,
        remaining_budget_ms=remaining,
        estimated_latency_ms=estimated,
        reason_codes=tuple(policy_codes),
    )


def _is_high_confidence_exact(
    features: QueryFeatures, confidence: RetrievalConfidenceSignals
) -> bool:
    exact = features.identifier_ratio >= IDENTIFIER_DOMINANT_RATIO or any(
        (
            features.contains_path,
            features.contains_config_key,
            features.contains_error_text,
            features.contains_stack_trace,
        )
    )
    return (
        exact
        and confidence.top1_agreement is True
        and confidence.query_identifier_count > 0
        and confidence.identifier_coverage == 1.0
    )


def _combined_reason_codes(
    existing: tuple[str, ...], policy_codes: list[str]
) -> tuple[str, ...]:
    retained = [code for code in existing if not code.startswith("rerank_")]
    # RetrievalPlan has a hard schema bound.  Keep policy evidence even when an
    # upstream plan already used every available reason-code slot.
    available_existing = max(0, 16 - len(policy_codes))
    return tuple((*retained[:available_existing], *policy_codes))


def _finite_number(name: str, value: object, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if normalized < 0.0 or (positive and normalized == 0.0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be {qualifier}")
    return normalized
