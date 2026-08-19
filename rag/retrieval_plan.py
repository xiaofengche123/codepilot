"""Validated, serializable retrieval plans for adaptive retrieval.

This module defines data only.  It does not analyze queries, inspect rankings,
load configuration, or execute retrieval.  Routing and runtime integration are
separate milestones so creating a plan cannot silently change search behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any


RETRIEVAL_PLAN_SCHEMA_VERSION = 1
MAX_RRF_K = 10_000
MAX_CANDIDATE_COUNT = 100
MAX_REASON_CHARS = 500
MAX_REASON_CODES = 16
MAX_REASON_CODE_CHARS = 64

_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class RetrievalPlan:
    """An immutable, explainable set of retrieval execution parameters.

    ``candidate_count`` is the bounded candidate pool available to optional
    reranking.  It remains populated when ``rerank`` is false so every plan has
    the same stable shape.  ``reason`` and ``reason_codes`` explain why a future
    router selected the values; they must not contain the original query.
    """

    bm25_weight: float
    vector_weight: float
    rrf_k: int
    candidate_count: int
    include_docs: bool
    rerank: bool
    reason: str
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        bm25_weight = _finite_nonnegative_weight("bm25_weight", self.bm25_weight)
        vector_weight = _finite_nonnegative_weight(
            "vector_weight", self.vector_weight
        )
        if bm25_weight == 0.0 and vector_weight == 0.0:
            raise ValueError("at least one retrieval weight must be positive")

        _bounded_integer("rrf_k", self.rrf_k, minimum=1, maximum=MAX_RRF_K)
        _bounded_integer(
            "candidate_count",
            self.candidate_count,
            minimum=1,
            maximum=MAX_CANDIDATE_COUNT,
        )
        if type(self.include_docs) is not bool:
            raise TypeError("include_docs must be a boolean")
        if type(self.rerank) is not bool:
            raise TypeError("rerank must be a boolean")

        reason = _validated_reason(self.reason)
        reason_codes = _validated_reason_codes(self.reason_codes)

        # Normalize accepted integers to floats and surrounding reason whitespace
        # once at construction, preserving stable field types thereafter.
        object.__setattr__(self, "bm25_weight", bm25_weight)
        object.__setattr__(self, "vector_weight", vector_weight)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "reason_codes", reason_codes)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready representation with an explicit schema version."""
        return {
            "schema_version": RETRIEVAL_PLAN_SCHEMA_VERSION,
            "bm25_weight": self.bm25_weight,
            "vector_weight": self.vector_weight,
            "rrf_k": self.rrf_k,
            "candidate_count": self.candidate_count,
            "include_docs": self.include_docs,
            "rerank": self.rerank,
            "reason": self.reason,
            "reason_codes": list(self.reason_codes),
        }


def _finite_nonnegative_weight(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if normalized < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return normalized


def _bounded_integer(
    name: str,
    value: object,
    *,
    minimum: int,
    maximum: int | None = None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" and at most {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} must be at least {minimum}{upper}")


def _validated_reason(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("reason must be a string")
    if len(value) > MAX_REASON_CHARS:
        raise ValueError(f"reason must not exceed {MAX_REASON_CHARS} characters")
    normalized = value.strip()
    if not normalized:
        raise ValueError("reason must not be empty")
    if "\r" in normalized or "\n" in normalized:
        raise ValueError("reason must be a single line")
    return normalized


def _validated_reason_codes(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError("reason_codes must be a tuple")
    if len(value) > MAX_REASON_CODES:
        raise ValueError(f"reason_codes must not exceed {MAX_REASON_CODES} items")
    if any(not isinstance(code, str) for code in value):
        raise TypeError("reason_codes must contain only strings")
    if any(
        len(code) > MAX_REASON_CODE_CHARS or not _REASON_CODE_RE.fullmatch(code)
        for code in value
    ):
        raise ValueError("reason_codes must use bounded lowercase identifiers")
    if len(set(value)) != len(value):
        raise ValueError("reason_codes must be unique")
    return value


# Compatibility snapshot of the repository's current product defaults.  It is
# intentionally not imported by the retriever in ROUTE-002; runtime behavior stays
# controlled by existing configuration until routing is explicitly integrated.
BASELINE_RETRIEVAL_PLAN = RetrievalPlan(
    bm25_weight=2.0,
    vector_weight=0.25,
    rrf_k=10,
    candidate_count=30,
    include_docs=False,
    rerank=False,
    reason="Preserve the current fixed weighted RRF behavior.",
    reason_codes=("fixed_rrf_compatibility",),
)


def baseline_retrieval_plan() -> RetrievalPlan:
    """Return the immutable plan matching current repository defaults."""
    return BASELINE_RETRIEVAL_PLAN
