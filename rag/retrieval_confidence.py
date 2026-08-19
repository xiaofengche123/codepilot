"""Pure, explainable confidence signals over two retrieval rankings.

The signals are descriptive heuristics, not probabilities.  This module neither
executes retrieval nor chooses a :class:`~rag.retrieval_plan.RetrievalPlan`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
import re
from typing import Any, Protocol

from rag.query_features import extract_query_identifiers


MAX_SIGNAL_TOP_K = 100
MAX_CANDIDATE_TEXT_CHARS = 20_000
RETRIEVAL_CONFIDENCE_SCHEMA_VERSION = 1


class ConfidenceHit(Protocol):
    uid: str
    document: str
    metadata: dict
    score: float


@dataclass(frozen=True)
class RetrievalConfidenceSignals:
    """Bounded ranking agreement, coverage, margin, and diversity signals."""

    top_k: int
    vector_result_count: int
    bm25_result_count: int
    overlap_count: int
    overlap_ratio: float
    top1_agreement: bool | None
    query_identifier_count: int
    matched_identifier_count: int
    identifier_coverage: float
    vector_top_score_margin: float | None
    candidate_count: int
    candidates_with_file_count: int
    unique_file_count: int
    file_diversity_ratio: float
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RETRIEVAL_CONFIDENCE_SCHEMA_VERSION,
            "top_k": self.top_k,
            "vector_result_count": self.vector_result_count,
            "bm25_result_count": self.bm25_result_count,
            "overlap_count": self.overlap_count,
            "overlap_ratio": self.overlap_ratio,
            "top1_agreement": self.top1_agreement,
            "query_identifier_count": self.query_identifier_count,
            "matched_identifier_count": self.matched_identifier_count,
            "identifier_coverage": self.identifier_coverage,
            "vector_top_score_margin": self.vector_top_score_margin,
            "candidate_count": self.candidate_count,
            "candidates_with_file_count": self.candidates_with_file_count,
            "unique_file_count": self.unique_file_count,
            "file_diversity_ratio": self.file_diversity_ratio,
            "reason_codes": list(self.reason_codes),
        }


def calculate_retrieval_confidence(
    query: str,
    vector_hits: Sequence[ConfidenceHit],
    bm25_hits: Sequence[ConfidenceHit],
    *,
    top_k: int = 10,
) -> RetrievalConfidenceSignals:
    """Calculate bounded signals without mutating hits or retaining ``query``.

    Ranking overlap uses a fixed-K denominator.  Vector margin is the raw
    ``top1.score - top2.score`` on the current higher-is-better score scale; it is
    intentionally not normalized or described as a probability.
    """
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        raise TypeError("top_k must be an integer")
    if not 1 <= top_k <= MAX_SIGNAL_TOP_K:
        raise ValueError(f"top_k must be between 1 and {MAX_SIGNAL_TOP_K}")

    vector = _unique_top_hits(vector_hits, top_k, "vector_hits")
    bm25 = _unique_top_hits(bm25_hits, top_k, "bm25_hits")
    vector_uids = tuple(uid for uid, _ in vector)
    bm25_uids = tuple(uid for uid, _ in bm25)
    overlap_count = len(set(vector_uids) & set(bm25_uids))
    top1_agreement = (
        vector_uids[0] == bm25_uids[0]
        if vector_uids and bm25_uids
        else None
    )

    candidates_by_uid: dict[str, ConfidenceHit] = {}
    for uid, hit in (*vector, *bm25):
        candidates_by_uid.setdefault(uid, hit)
    candidates = tuple(candidates_by_uid.values())

    query_identifiers = extract_query_identifiers(query)
    candidate_tokens: set[str] = set()
    for hit in candidates:
        candidate_tokens.update(_candidate_tokens(hit))
    matched_identifiers = sum(
        identifier in candidate_tokens for identifier in query_identifiers
    )

    files = [
        normalized
        for hit in candidates
        if (normalized := _normalized_file(hit)) is not None
    ]
    unique_files = set(files)
    vector_margin, vector_order_inconsistent = _vector_margin(vector)

    reasons: list[str] = []
    if not candidates:
        reasons.append("no_candidates")
    if vector and bm25:
        reasons.append(
            "top1_agreement" if top1_agreement else "top1_disagreement"
        )
        reasons.append(
            "ranking_overlap" if overlap_count else "no_ranking_overlap"
        )
    else:
        if not vector:
            reasons.append("vector_results_missing")
        if not bm25:
            reasons.append("bm25_results_missing")
    if query_identifiers:
        if matched_identifiers == len(query_identifiers):
            reasons.append("identifiers_fully_covered")
        elif matched_identifiers:
            reasons.append("identifiers_partially_covered")
        else:
            reasons.append("identifiers_not_covered")
    else:
        reasons.append("query_identifiers_absent")
    if vector_margin is None:
        reasons.append("vector_margin_unavailable")
    else:
        reasons.append("vector_margin_available")
    if vector_order_inconsistent:
        reasons.append("vector_score_order_inconsistent")
    if len(unique_files) > 1:
        reasons.append("multiple_candidate_files")
    if len(files) < len(candidates):
        reasons.append("candidate_file_missing")

    return RetrievalConfidenceSignals(
        top_k=top_k,
        vector_result_count=len(vector),
        bm25_result_count=len(bm25),
        overlap_count=overlap_count,
        overlap_ratio=_ratio(overlap_count, top_k),
        top1_agreement=top1_agreement,
        query_identifier_count=len(query_identifiers),
        matched_identifier_count=matched_identifiers,
        identifier_coverage=_ratio(
            matched_identifiers, len(query_identifiers)
        ),
        vector_top_score_margin=vector_margin,
        candidate_count=len(candidates),
        candidates_with_file_count=len(files),
        unique_file_count=len(unique_files),
        file_diversity_ratio=_ratio(len(unique_files), len(candidates)),
        reason_codes=tuple(reasons),
    )


def _unique_top_hits(
    hits: Sequence[ConfidenceHit], top_k: int, name: str
) -> list[tuple[str, ConfidenceHit]]:
    if isinstance(hits, (str, bytes)) or not isinstance(hits, Sequence):
        raise TypeError(f"{name} must be a sequence")
    unique: list[tuple[str, ConfidenceHit]] = []
    seen: set[str] = set()
    for hit in hits[:top_k]:
        uid = getattr(hit, "uid", None)
        if not isinstance(uid, str) or not uid:
            raise TypeError(f"{name} entries must have a non-empty string uid")
        normalized = uid.replace("\\", "/").casefold()
        if normalized in seen:
            continue
        unique.append((normalized, hit))
        seen.add(normalized)
    return unique


def _candidate_tokens(hit: ConfidenceHit) -> set[str]:
    metadata = getattr(hit, "metadata", {})
    file_value = metadata.get("file", "") if isinstance(metadata, dict) else ""
    document = getattr(hit, "document", "")
    text = " ".join(
        value[:MAX_CANDIDATE_TEXT_CHARS]
        for value in (file_value, document)
        if isinstance(value, str)
    )
    return {
        match.group(0).casefold()
        for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", text)
    }


def _normalized_file(hit: ConfidenceHit) -> str | None:
    metadata = getattr(hit, "metadata", {})
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("file")
    if not isinstance(value, str) or not value.strip():
        return None
    return value[:MAX_CANDIDATE_TEXT_CHARS].replace("\\", "/").casefold()


def _vector_margin(
    vector: Sequence[tuple[str, ConfidenceHit]],
) -> tuple[float | None, bool]:
    if len(vector) < 2:
        return None, False
    first = getattr(vector[0][1], "score", None)
    second = getattr(vector[1][1], "score", None)
    if (
        isinstance(first, bool)
        or isinstance(second, bool)
        or not isinstance(first, (int, float))
        or not isinstance(second, (int, float))
    ):
        return None, False
    try:
        first_value, second_value = float(first), float(second)
    except OverflowError:
        return None, False
    if not math.isfinite(first_value) or not math.isfinite(second_value):
        return None, False
    raw_margin = first_value - second_value
    return round(max(0.0, raw_margin), 6), raw_margin < 0.0


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return min(1.0, max(0.0, round(numerator / denominator, 6)))
