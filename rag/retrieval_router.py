"""Deterministic v1 rules mapping query/ranking signals to retrieval plans.

The constants are conservative ROUTE-006 development-set selections.  The
frozen test set and formal results were not read during selection.  This module
still does not execute plans or enable reranking.
"""

from __future__ import annotations

from rag.query_features import QueryFeatures
from rag.retrieval_confidence import RetrievalConfidenceSignals
from rag.retrieval_plan import RetrievalPlan


ROUTER_VERSION = "rule_router_v1"

BASELINE_BM25_WEIGHT = 2.0
BASELINE_VECTOR_WEIGHT = 0.25
EXACT_BM25_WEIGHT = 2.5
EXACT_VECTOR_WEIGHT = 0.25
NATURAL_LANGUAGE_BM25_WEIGHT = 2.5
NATURAL_LANGUAGE_VECTOR_WEIGHT = 0.25
MIXED_LANGUAGE_BM25_WEIGHT = 1.5
MIXED_LANGUAGE_VECTOR_WEIGHT = 0.5
CROSS_MODULE_BM25_WEIGHT = 2.5
CROSS_MODULE_VECTOR_WEIGHT = 0.25
DISAGREEMENT_BM25_WEIGHT = 2.0
DISAGREEMENT_VECTOR_WEIGHT = 0.5

DEFAULT_RRF_K = 10
DEFAULT_CANDIDATE_COUNT = 30
MIXED_CANDIDATE_COUNT = 30
CROSS_MODULE_CANDIDATE_COUNT = 30
DISAGREEMENT_CANDIDATE_COUNT = 40

IDENTIFIER_DOMINANT_RATIO = 0.5
NATURAL_LANGUAGE_DOMINANT_RATIO = 0.75
MAX_NATURAL_LANGUAGE_IDENTIFIER_RATIO = 0.25
LOW_RANKING_OVERLAP_RATIO = 0.2


_REASON_TEXT = {
    "query_empty": "empty query uses compatibility defaults",
    "query_baseline": "ambiguous query uses compatibility defaults",
    "query_exact_code": "exact code evidence favors BM25",
    "query_natural_language": "natural-language query uses the development-tuned code-search prior",
    "query_mixed_language": "mixed-language query adds bounded vector weight",
    "query_cross_module": "cross-module query uses the conservative code-search prior",
    "ranking_disagreement": "low-overlap Top-1 disagreement adds bounded vector weight and candidates",
    "ranking_agreement": "both retrievers agree on Top-1",
    "identifiers_fully_covered": "candidate union covers all query identifiers",
    "bm25_only_available": "only BM25 results are available",
    "vector_only_available": "only vector results are available",
    "no_candidates": "no candidate evidence keeps compatibility defaults",
    "documentation_requested": "query explicitly requests documentation",
    "candidate_pool_expanded": "complex evidence expands the candidate pool",
    "rerank_deferred_to_policy": "reranking remains disabled until ROUTE-005 policy",
}


def route_retrieval(
    features: QueryFeatures,
    confidence: RetrievalConfidenceSignals | None = None,
) -> RetrievalPlan:
    """Return a validated plan using fixed, explainable v1 rules.

    This pure function never executes retrieval.  In particular, every returned
    plan has ``rerank=False``; conditional reranking and latency budgets belong to
    ROUTE-005.
    """
    if not isinstance(features, QueryFeatures):
        raise TypeError("features must be QueryFeatures")
    if confidence is not None and not isinstance(
        confidence, RetrievalConfidenceSignals
    ):
        raise TypeError("confidence must be RetrievalConfidenceSignals or None")

    bm25_weight = BASELINE_BM25_WEIGHT
    vector_weight = BASELINE_VECTOR_WEIGHT
    candidate_count = DEFAULT_CANDIDATE_COUNT
    reasons = [ROUTER_VERSION]

    if features.token_count == 0:
        reasons.append("query_empty")
    elif features.has_cross_module_intent:
        bm25_weight = CROSS_MODULE_BM25_WEIGHT
        vector_weight = CROSS_MODULE_VECTOR_WEIGHT
        candidate_count = CROSS_MODULE_CANDIDATE_COUNT
        reasons.append("query_cross_module")
    elif features.is_mixed_language:
        bm25_weight = MIXED_LANGUAGE_BM25_WEIGHT
        vector_weight = MIXED_LANGUAGE_VECTOR_WEIGHT
        candidate_count = MIXED_CANDIDATE_COUNT
        reasons.append("query_mixed_language")
    elif _is_natural_language(features):
        bm25_weight = NATURAL_LANGUAGE_BM25_WEIGHT
        vector_weight = NATURAL_LANGUAGE_VECTOR_WEIGHT
        reasons.append("query_natural_language")
    elif _is_exact_code_query(features):
        bm25_weight = EXACT_BM25_WEIGHT
        vector_weight = EXACT_VECTOR_WEIGHT
        reasons.append("query_exact_code")
    else:
        reasons.append("query_baseline")

    if confidence is not None:
        if (
            confidence.vector_result_count == 0
            and confidence.bm25_result_count > 0
        ):
            bm25_weight, vector_weight = 1.0, 0.0
            reasons.append("bm25_only_available")
        elif (
            confidence.bm25_result_count == 0
            and confidence.vector_result_count > 0
        ):
            bm25_weight, vector_weight = 0.0, 1.0
            reasons.append("vector_only_available")
        elif confidence.candidate_count == 0:
            bm25_weight = BASELINE_BM25_WEIGHT
            vector_weight = BASELINE_VECTOR_WEIGHT
            candidate_count = DEFAULT_CANDIDATE_COUNT
            reasons.append("no_candidates")
        else:
            if (
                confidence.top1_agreement is False
                and confidence.overlap_ratio <= LOW_RANKING_OVERLAP_RATIO
            ):
                bm25_weight = DISAGREEMENT_BM25_WEIGHT
                vector_weight = DISAGREEMENT_VECTOR_WEIGHT
                candidate_count = max(
                    candidate_count, DISAGREEMENT_CANDIDATE_COUNT
                )
                reasons.append("ranking_disagreement")
                if "candidate_pool_expanded" not in reasons:
                    reasons.append("candidate_pool_expanded")
            elif confidence.top1_agreement is True:
                reasons.append("ranking_agreement")
            if (
                confidence.query_identifier_count > 0
                and confidence.identifier_coverage == 1.0
            ):
                reasons.append("identifiers_fully_covered")

    if features.requests_documentation:
        reasons.append("documentation_requested")
    reasons.append("rerank_deferred_to_policy")
    reason_codes = tuple(dict.fromkeys(reasons))
    explanation = "Rule router v1: " + "; ".join(
        _REASON_TEXT[code]
        for code in reason_codes
        if code != ROUTER_VERSION
    )
    return RetrievalPlan(
        bm25_weight=bm25_weight,
        vector_weight=vector_weight,
        rrf_k=DEFAULT_RRF_K,
        candidate_count=candidate_count,
        include_docs=features.requests_documentation,
        rerank=False,
        reason=explanation,
        reason_codes=reason_codes,
    )


def _is_natural_language(features: QueryFeatures) -> bool:
    return (
        features.natural_language_ratio >= NATURAL_LANGUAGE_DOMINANT_RATIO
        and features.identifier_ratio <= MAX_NATURAL_LANGUAGE_IDENTIFIER_RATIO
        and not _has_exact_marker(features)
    )


def _is_exact_code_query(features: QueryFeatures) -> bool:
    return (
        features.identifier_ratio >= IDENTIFIER_DOMINANT_RATIO
        or _has_exact_marker(features)
    )


def _has_exact_marker(features: QueryFeatures) -> bool:
    return any((
        features.contains_path,
        features.contains_config_key,
        features.contains_error_text,
        features.contains_stack_trace,
    ))
