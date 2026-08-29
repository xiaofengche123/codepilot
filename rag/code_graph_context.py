"""Deterministic scoring, deduplication, and budgeting for graph candidates.

This pure layer consumes GRAPH-005 structural candidates plus caller-supplied
token costs.  It does not inspect document text, tokenize content, read an
index, or alter Retriever behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping

from rag.code_graph import GraphEdgeKind
from rag.code_graph_expansion import (
    MAX_CHUNK_UID_CHARS,
    MAX_EXPANSION_CANDIDATES,
    GraphChunkRef,
    GraphExpansionCandidate,
    GraphTraversalDirection,
)


GRAPH_CONTEXT_SCHEMA_VERSION = 1
MAX_CONTEXT_TOKEN_BUDGET = 1_000_000
MAX_CONTEXT_CHUNKS = 1_000
MAX_CHUNK_TOKEN_COUNT = 1_000_000
MAX_COST_ENTRIES = 250_000
MAX_STRUCTURE_WEIGHT = 100.0


def _bounded_integer(
    name: str, value: object, *, minimum: int, maximum: int
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _weight(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if not 0.0 < normalized <= MAX_STRUCTURE_WEIGHT:
        raise ValueError(
            f"{name} must be positive and at most {MAX_STRUCTURE_WEIGHT}"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class GraphStructureScoringPolicy:
    """Untuned v1 weights for transparent structural relevance scoring."""

    calls_weight: float = 1.0
    inherits_weight: float = 0.9
    tests_weight: float = 0.8
    imports_weight: float = 0.65
    contains_weight: float = 0.5
    outgoing_weight: float = 1.0
    incoming_weight: float = 0.9
    seed_rank_k: int = 10

    def __post_init__(self) -> None:
        for name in (
            "calls_weight",
            "inherits_weight",
            "tests_weight",
            "imports_weight",
            "contains_weight",
            "outgoing_weight",
            "incoming_weight",
        ):
            object.__setattr__(self, name, _weight(name, getattr(self, name)))
        _bounded_integer("seed_rank_k", self.seed_rank_k, minimum=1, maximum=10_000)

    def edge_weight(self, kind: GraphEdgeKind) -> float:
        if not isinstance(kind, GraphEdgeKind):
            raise TypeError("kind must be GraphEdgeKind")
        return {
            GraphEdgeKind.CALLS: self.calls_weight,
            GraphEdgeKind.INHERITS: self.inherits_weight,
            GraphEdgeKind.TESTS: self.tests_weight,
            GraphEdgeKind.IMPORTS: self.imports_weight,
            GraphEdgeKind.CONTAINS: self.contains_weight,
        }[kind]

    def direction_weight(self, direction: GraphTraversalDirection) -> float:
        if direction is GraphTraversalDirection.OUTGOING:
            return self.outgoing_weight
        if direction is GraphTraversalDirection.INCOMING:
            return self.incoming_weight
        raise ValueError("candidate direction must be outgoing or incoming")

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls_weight": self.calls_weight,
            "inherits_weight": self.inherits_weight,
            "tests_weight": self.tests_weight,
            "imports_weight": self.imports_weight,
            "contains_weight": self.contains_weight,
            "outgoing_weight": self.outgoing_weight,
            "incoming_weight": self.incoming_weight,
            "seed_rank_k": self.seed_rank_k,
        }


DEFAULT_GRAPH_SCORING_POLICY = GraphStructureScoringPolicy()


@dataclass(frozen=True, slots=True)
class GraphStructureScore:
    edge_weight: float
    direction_weight: float
    seed_rank_weight: float
    total: float

    def to_dict(self) -> dict[str, float]:
        return {
            "edge_weight": self.edge_weight,
            "direction_weight": self.direction_weight,
            "seed_rank_weight": self.seed_rank_weight,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class GraphContextChunk:
    """A unique selected chunk with its best structural evidence."""

    chunk: GraphChunkRef
    token_count: int
    score: GraphStructureScore
    best_evidence: GraphExpansionCandidate
    evidence_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk": self.chunk.to_dict(),
            "token_count": self.token_count,
            "score": self.score.to_dict(),
            "best_evidence": self.best_evidence.to_dict(),
            "evidence_count": self.evidence_count,
        }


@dataclass(frozen=True, slots=True)
class GraphContextResult:
    selected: tuple[GraphContextChunk, ...]
    token_budget: int
    reserved_tokens: int
    selected_token_count: int
    input_candidate_count: int
    unique_candidate_count: int
    duplicate_candidate_count: int
    omitted_for_budget_count: int
    omitted_for_chunk_limit_count: int
    max_chunks: int
    policy: GraphStructureScoringPolicy
    schema_version: int = GRAPH_CONTEXT_SCHEMA_VERSION

    @property
    def available_token_budget(self) -> int:
        return self.token_budget - self.reserved_tokens

    @property
    def total_token_count(self) -> int:
        return self.reserved_tokens + self.selected_token_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "selected": [item.to_dict() for item in self.selected],
            "token_budget": self.token_budget,
            "reserved_tokens": self.reserved_tokens,
            "available_token_budget": self.available_token_budget,
            "selected_token_count": self.selected_token_count,
            "total_token_count": self.total_token_count,
            "input_candidate_count": self.input_candidate_count,
            "unique_candidate_count": self.unique_candidate_count,
            "duplicate_candidate_count": self.duplicate_candidate_count,
            "omitted_for_budget_count": self.omitted_for_budget_count,
            "omitted_for_chunk_limit_count": self.omitted_for_chunk_limit_count,
            "max_chunks": self.max_chunks,
            "policy": self.policy.to_dict(),
        }


def _validate_candidate(candidate: object) -> GraphExpansionCandidate:
    if not isinstance(candidate, GraphExpansionCandidate):
        raise TypeError("candidates must contain GraphExpansionCandidate values")
    _bounded_integer(
        "candidate seed_rank",
        candidate.seed_rank,
        minimum=1,
        maximum=1_000_000,
    )
    if not isinstance(candidate.chunk, GraphChunkRef):
        raise TypeError("candidate chunk must be GraphChunkRef")
    if not isinstance(candidate.edge_kind, GraphEdgeKind):
        raise TypeError("candidate edge_kind must be GraphEdgeKind")
    if candidate.traversal_direction not in {
        GraphTraversalDirection.OUTGOING,
        GraphTraversalDirection.INCOMING,
    }:
        raise ValueError("candidate direction must be outgoing or incoming")
    return candidate


def score_graph_candidate(
    candidate: GraphExpansionCandidate,
    policy: GraphStructureScoringPolicy = DEFAULT_GRAPH_SCORING_POLICY,
) -> GraphStructureScore:
    """Return a transparent fixed-policy score for one structural path."""
    candidate = _validate_candidate(candidate)
    if not isinstance(policy, GraphStructureScoringPolicy):
        raise TypeError("policy must be GraphStructureScoringPolicy")
    edge_weight = policy.edge_weight(candidate.edge_kind)
    direction_weight = policy.direction_weight(candidate.traversal_direction)
    seed_rank_weight = (policy.seed_rank_k + 1) / (
        policy.seed_rank_k + candidate.seed_rank
    )
    return GraphStructureScore(
        edge_weight=edge_weight,
        direction_weight=direction_weight,
        seed_rank_weight=seed_rank_weight,
        total=edge_weight * direction_weight * seed_rank_weight,
    )


def _validated_costs(costs: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(costs, Mapping):
        raise TypeError("chunk_token_costs must be a mapping")
    if len(costs) > MAX_COST_ENTRIES:
        raise ValueError("chunk_token_costs exceeds the entry limit")
    normalized: dict[str, int] = {}
    for uid, token_count in costs.items():
        if not isinstance(uid, str):
            raise TypeError("chunk_token_cost keys must be strings")
        if not uid or len(uid) > MAX_CHUNK_UID_CHARS:
            raise ValueError("chunk_token_cost UID must be non-empty and bounded")
        if any(character in uid for character in ("\n", "\r", "\0")):
            raise ValueError("chunk_token_cost UID must be single-line text")
        normalized[uid] = _bounded_integer(
            "chunk token count",
            token_count,
            minimum=1,
            maximum=MAX_CHUNK_TOKEN_COUNT,
        )
    return normalized


def _evidence_order(
    candidate: GraphExpansionCandidate,
    score: GraphStructureScore,
) -> tuple[Any, ...]:
    return (
        -score.total,
        candidate.seed_rank,
        candidate.edge_kind.value,
        candidate.traversal_direction.value,
        candidate.seed_uid,
        candidate.edge_id,
        candidate.seed_node_id,
        candidate.neighbor_node_id,
    )


def select_graph_context(
    candidates: Iterable[GraphExpansionCandidate],
    chunk_token_costs: Mapping[str, int],
    *,
    token_budget: int,
    max_chunks: int,
    reserved_tokens: int = 0,
    policy: GraphStructureScoringPolicy = DEFAULT_GRAPH_SCORING_POLICY,
) -> GraphContextResult:
    """Deduplicate, rank, and greedily fit graph chunks into strict limits.

    Deduplication is by stable chunk UID.  Repeated evidence never sums into a
    larger score: the best path determines rank and the number of paths remains
    available as ``evidence_count``.  Equal-score chunks prefer lower token
    cost, then lower seed rank and stable UID, making selection deterministic
    and budget-efficient.
    """
    materialized = tuple(_validate_candidate(item) for item in candidates)
    if len(materialized) > MAX_EXPANSION_CANDIDATES:
        raise ValueError("candidate count exceeds the context limit")
    if not isinstance(policy, GraphStructureScoringPolicy):
        raise TypeError("policy must be GraphStructureScoringPolicy")
    token_budget = _bounded_integer(
        "token_budget",
        token_budget,
        minimum=0,
        maximum=MAX_CONTEXT_TOKEN_BUDGET,
    )
    reserved_tokens = _bounded_integer(
        "reserved_tokens",
        reserved_tokens,
        minimum=0,
        maximum=MAX_CONTEXT_TOKEN_BUDGET,
    )
    if reserved_tokens > token_budget:
        raise ValueError("reserved_tokens must not exceed token_budget")
    max_chunks = _bounded_integer(
        "max_chunks", max_chunks, minimum=0, maximum=MAX_CONTEXT_CHUNKS
    )
    costs = _validated_costs(chunk_token_costs)

    grouped: dict[str, list[GraphExpansionCandidate]] = {}
    chunk_by_uid: dict[str, GraphChunkRef] = {}
    for candidate in materialized:
        uid = candidate.chunk.uid
        if uid not in costs:
            raise ValueError(f"missing token cost for candidate chunk: {uid}")
        previous = chunk_by_uid.setdefault(uid, candidate.chunk)
        if previous != candidate.chunk:
            raise ValueError(f"candidate chunk UID has conflicting metadata: {uid}")
        grouped.setdefault(uid, []).append(candidate)

    ranked: list[GraphContextChunk] = []
    for uid, evidence in grouped.items():
        scored = [
            (candidate, score_graph_candidate(candidate, policy))
            for candidate in evidence
        ]
        best_candidate, best_score = min(
            scored, key=lambda item: _evidence_order(item[0], item[1])
        )
        ranked.append(
            GraphContextChunk(
                chunk=chunk_by_uid[uid],
                token_count=costs[uid],
                score=best_score,
                best_evidence=best_candidate,
                evidence_count=len(evidence),
            )
        )
    ranked.sort(
        key=lambda item: (
            -item.score.total,
            item.token_count,
            item.best_evidence.seed_rank,
            item.chunk.uid,
        )
    )

    available_budget = token_budget - reserved_tokens
    selected: list[GraphContextChunk] = []
    selected_tokens = 0
    omitted_for_budget = 0
    omitted_for_chunk_limit = 0
    for index, item in enumerate(ranked):
        if len(selected) >= max_chunks:
            omitted_for_chunk_limit += len(ranked) - index
            break
        if selected_tokens + item.token_count > available_budget:
            omitted_for_budget += 1
            continue
        selected.append(item)
        selected_tokens += item.token_count

    unique_count = len(grouped)
    return GraphContextResult(
        selected=tuple(selected),
        token_budget=token_budget,
        reserved_tokens=reserved_tokens,
        selected_token_count=selected_tokens,
        input_candidate_count=len(materialized),
        unique_candidate_count=unique_count,
        duplicate_candidate_count=len(materialized) - unique_count,
        omitted_for_budget_count=omitted_for_budget,
        omitted_for_chunk_limit_count=omitted_for_chunk_limit,
        max_chunks=max_chunks,
        policy=policy,
    )
