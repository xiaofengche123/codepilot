"""Development-only grid search for adaptive retrieval routing parameters.

The loader deliberately accepts only ``codepilot-dev.json``.  This guard keeps
ROUTE-006 tooling from being pointed at the frozen test set or formal results by
accident.  Queries and rankings are held only in memory; CLI output is aggregate.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Iterable

from rag.evaluate import calculate_metrics
from rag.query_features import extract_query_features
from rag.retrieval_confidence import calculate_retrieval_confidence
from rag.retrieval_router import route_retrieval
from rag.retriever import SearchHit, reciprocal_rank_fusion


DEVELOPMENT_DATASET_NAME = "codepilot-dev.json"
DEFAULT_K = 10
MAX_TUNING_RANK = 100


@dataclass(frozen=True)
class FusionSetting:
    bm25_weight: float
    vector_weight: float
    rrf_k: int
    candidate_count: int

    def __post_init__(self) -> None:
        weights = []
        for name, value in (
            ("bm25_weight", self.bm25_weight),
            ("vector_weight", self.vector_weight),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number")
            try:
                normalized = float(value)
            except OverflowError as exc:
                raise ValueError(f"{name} must be finite and non-negative") from exc
            if not math.isfinite(normalized) or normalized < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, normalized)
            weights.append(normalized)
        if not any(weights):
            raise ValueError("at least one retrieval weight must be positive")
        for name, value, maximum in (
            ("rrf_k", self.rrf_k, 10_000),
            ("candidate_count", self.candidate_count, 100),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if not 1 <= value <= maximum:
                raise ValueError(f"{name} must be between 1 and {maximum}")

    def to_dict(self) -> dict[str, float | int]:
        return {
            "bm25_weight": self.bm25_weight,
            "vector_weight": self.vector_weight,
            "rrf_k": self.rrf_k,
            "candidate_count": self.candidate_count,
        }


@dataclass(frozen=True)
class DevelopmentRankingCase:
    case_id: str
    family: str
    relevant: tuple[str, ...]
    query: str = field(repr=False)
    vector_hits: tuple[SearchHit, ...] = field(repr=False)
    bm25_hits: tuple[SearchHit, ...] = field(repr=False)


@dataclass(frozen=True)
class TuningScore:
    family: str
    query_count: int
    recall_at_k: float
    mrr_at_k: float
    setting: FusionSetting

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "query_count": self.query_count,
            "recall_at_k": self.recall_at_k,
            "mrr_at_k": self.mrr_at_k,
            "setting": self.setting.to_dict(),
        }


WEIGHT_CANDIDATES = (
    (2.5, 0.25),
    (2.0, 0.25),
    (2.0, 0.5),
    (1.5, 0.5),
    (1.0, 1.0),
    (0.75, 1.5),
)
RRF_K_CANDIDATES = (10, 30, 60)
CANDIDATE_COUNT_CANDIDATES = (30, 40, 50)


def candidate_settings() -> tuple[FusionSetting, ...]:
    """Return the bounded, reviewable ROUTE-006 search space."""
    return tuple(
        FusionSetting(bm25, vector, rrf_k, count)
        for rrf_k in RRF_K_CANDIDATES
        for bm25, vector in WEIGHT_CANDIDATES
        for count in CANDIDATE_COUNT_CANDIDATES
    )


def load_development_items(path: str | Path) -> list[dict]:
    """Load only the designated development set, never a frozen dataset."""
    dataset_path = Path(path)
    if dataset_path.name.casefold() != DEVELOPMENT_DATASET_NAME:
        raise ValueError(
            f"ROUTE-006 accepts only {DEVELOPMENT_DATASET_NAME}; "
            "frozen test sets and result files are prohibited"
        )
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("development dataset must be a non-empty JSON array")
    return data


def collect_development_rankings(
    items: Iterable[dict], project_dir: str
) -> tuple[DevelopmentRankingCase, ...]:
    """Collect raw local rankings once for deterministic in-memory grid search."""
    from rag.indexer import _get_collection
    from rag.retriever import _collection_documents, _vector_rank, bm25_rank

    collection = _get_collection(project_dir)
    if collection is None:
        raise LookupError("project must be indexed before ROUTE-006 tuning")
    documents = _collection_documents(collection, include_docs=False)
    cases = []
    for item in items:
        query = str(item.get("query", "")).strip()
        labels = item.get("required", item.get("relevant", []))
        relevant = tuple(str(label) for label in labels if str(label).strip())
        if not query or not relevant:
            continue
        vector = tuple(
            _vector_rank(query, collection, MAX_TUNING_RANK, include_docs=False)
        )
        bm25 = tuple(bm25_rank(query, documents, MAX_TUNING_RANK))
        confidence = calculate_retrieval_confidence(
            query, vector, bm25, top_k=DEFAULT_K
        )
        plan = route_retrieval(extract_query_features(query), confidence)
        family = _routed_family(plan.reason_codes)
        cases.append(
            DevelopmentRankingCase(
                case_id=str(item.get("id", "")),
                family=family,
                relevant=relevant,
                query=query,
                vector_hits=vector,
                bm25_hits=bm25,
            )
        )
    if not cases:
        raise ValueError("development dataset has no valid query/relevance cases")
    return tuple(cases)


def tune_family_settings(
    cases: Iterable[DevelopmentRankingCase],
    *,
    settings: Iterable[FusionSetting] | None = None,
    k: int = DEFAULT_K,
) -> tuple[TuningScore, ...]:
    """Select each routed family's setting by Recall, then MRR and lower cost."""
    if isinstance(k, bool) or not isinstance(k, int):
        raise TypeError("k must be an integer")
    if k <= 0:
        raise ValueError("k must be positive")
    options = tuple(settings or candidate_settings())
    if not options:
        raise ValueError("settings must not be empty")
    grouped: defaultdict[str, list[DevelopmentRankingCase]] = defaultdict(list)
    for case in cases:
        if not isinstance(case, DevelopmentRankingCase):
            raise TypeError("cases must contain DevelopmentRankingCase values")
        grouped[case.family].append(case)
    if not grouped:
        raise ValueError("cases must not be empty")

    selected = []
    for family, family_cases in sorted(grouped.items()):
        scored = [
            _score_setting(family, family_cases, setting, k)
            for setting in options
        ]
        # Selection is deterministic: quality first, then smaller candidate
        # pools/RRF constants, then earlier reviewable search-space order.
        order = {setting: index for index, setting in enumerate(options)}
        selected.append(
            max(
                scored,
                key=lambda score: (
                    score.recall_at_k,
                    score.mrr_at_k,
                    -score.setting.candidate_count,
                    -score.setting.rrf_k,
                    -order[score.setting],
                ),
            )
        )
    return tuple(selected)


def evaluate_family_profile(
    cases: Iterable[DevelopmentRankingCase],
    settings_by_family: dict[str, FusionSetting],
    *,
    k: int = DEFAULT_K,
) -> dict[str, float | int]:
    """Evaluate one setting per routed family without retaining per-query output."""
    case_values = tuple(cases)
    if not case_values:
        raise ValueError("cases must not be empty")
    recalls = []
    reciprocal_ranks = []
    for case in case_values:
        if case.family not in settings_by_family:
            raise ValueError(f"missing setting for routed family: {case.family}")
        score = _score_setting(case.family, [case], settings_by_family[case.family], k)
        recalls.append(score.recall_at_k)
        reciprocal_ranks.append(score.mrr_at_k)
    return {
        "query_count": len(case_values),
        "recall_at_k": round(sum(recalls) / len(recalls), 6),
        "mrr_at_k": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 6),
    }


def _score_setting(
    family: str,
    cases: list[DevelopmentRankingCase],
    setting: FusionSetting,
    k: int,
) -> TuningScore:
    recalls = []
    reciprocal_ranks = []
    for case in cases:
        count = setting.candidate_count
        hits = reciprocal_rank_fusion(
            case.vector_hits[:count],
            case.bm25_hits[:count],
            k,
            rrf_k=setting.rrf_k,
            vector_weight=setting.vector_weight,
            bm25_weight=setting.bm25_weight,
        )
        recall, mrr = calculate_metrics(hits, set(case.relevant))
        recalls.append(recall)
        reciprocal_ranks.append(mrr)
    size = len(cases)
    return TuningScore(
        family=family,
        query_count=size,
        recall_at_k=round(sum(recalls) / size, 6),
        mrr_at_k=round(sum(reciprocal_ranks) / size, 6),
        setting=setting,
    )


def _routed_family(reason_codes: tuple[str, ...]) -> str:
    if "ranking_disagreement" in reason_codes:
        return "ranking_disagreement"
    return next(
        (code for code in reason_codes if code.startswith("query_")),
        "query_baseline",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tune adaptive routing on codepilot-dev.json only"
    )
    parser.add_argument(
        "dataset", nargs="?", default=".rag-eval/codepilot-dev.json"
    )
    parser.add_argument("--project", default=".")
    args = parser.parse_args()
    items = load_development_items(args.dataset)
    cases = collect_development_rankings(items, args.project)
    selected = tune_family_settings(cases)
    selected_by_family = {score.family: score.setting for score in selected}
    fixed = FusionSetting(2.0, 0.25, 10, 30)
    fixed_by_family = {case.family: fixed for case in cases}
    payload = {
        "dataset_role": "development_only",
        "query_count": len(cases),
        "k": DEFAULT_K,
        "selected": [score.to_dict() for score in selected],
        "overall_selected": evaluate_family_profile(cases, selected_by_family),
        "overall_fixed_rrf": evaluate_family_profile(cases, fixed_by_family),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
