"""Pure, bounded one-hop expansion from indexed chunks to code-graph neighbors.

The expansion layer deliberately returns structural candidates only.  It does
not read an index, copy document text, score candidates, enforce a context
budget, deduplicate chunks, or alter Retriever behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from rag.code_graph import (
    MAX_SOURCE_LINE,
    GraphEdge,
    GraphEdgeKind,
    GraphNode,
    GraphNodeKind,
    normalize_graph_path,
)


GRAPH_EXPANSION_SCHEMA_VERSION = 1
MAX_CHUNK_UID_CHARS = 2_048
MAX_EXPANSION_SEEDS = 1_000
MAX_EXPANSION_CHUNKS = 250_000
MAX_EXPANSION_CANDIDATES = 250_000


class GraphTraversalDirection(str, Enum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BOTH = "both"


class GraphExpansionIssueCode(str, Enum):
    UNMAPPED_SEED = "unmapped_seed"
    AMBIGUOUS_SEED = "ambiguous_seed"
    UNMAPPED_NEIGHBOR = "unmapped_neighbor"


def _line_number(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 1 <= value <= MAX_SOURCE_LINE:
        raise ValueError(f"{name} is outside the supported range")
    return value


@dataclass(frozen=True, slots=True)
class GraphChunkRef:
    """Content-free identity and source range for an indexed chunk."""

    uid: str
    file: str
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if not isinstance(self.uid, str):
            raise TypeError("uid must be a string")
        if not self.uid or len(self.uid) > MAX_CHUNK_UID_CHARS:
            raise ValueError("uid must be non-empty and bounded")
        if any(character in self.uid for character in ("\n", "\r", "\0")):
            raise ValueError("uid must be single-line text")
        file = normalize_graph_path(self.file)
        start_line = _line_number("start_line", self.start_line)
        end_line = _line_number("end_line", self.end_line)
        if end_line < start_line:
            raise ValueError("end_line must not precede start_line")
        object.__setattr__(self, "file", file)

    @classmethod
    def from_metadata(
        cls, uid: str, metadata: Mapping[str, object]
    ) -> "GraphChunkRef":
        if not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        try:
            file = metadata["file"]
            start_line = metadata["start_line"]
            end_line = metadata["end_line"]
        except KeyError as exc:
            raise ValueError(f"metadata is missing {exc.args[0]}") from exc
        return cls(
            uid=uid,
            file=file,  # type: ignore[arg-type]
            start_line=start_line,  # type: ignore[arg-type]
            end_line=end_line,  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass(frozen=True, slots=True)
class GraphExpansionCandidate:
    """A raw seed-edge-neighbor path and the chunk representing its neighbor."""

    seed_uid: str
    seed_rank: int
    seed_node_id: str
    neighbor_node_id: str
    edge_id: str
    edge_kind: GraphEdgeKind
    traversal_direction: GraphTraversalDirection
    chunk: GraphChunkRef

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_uid": self.seed_uid,
            "seed_rank": self.seed_rank,
            "seed_node_id": self.seed_node_id,
            "neighbor_node_id": self.neighbor_node_id,
            "edge_id": self.edge_id,
            "edge_kind": self.edge_kind.value,
            "traversal_direction": self.traversal_direction.value,
            "chunk": self.chunk.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class GraphExpansionIssue:
    code: GraphExpansionIssueCode
    seed_uid: str
    node_id: str | None = None
    edge_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "seed_uid": self.seed_uid,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
        }


@dataclass(frozen=True, slots=True)
class GraphExpansionResult:
    candidates: tuple[GraphExpansionCandidate, ...]
    issues: tuple[GraphExpansionIssue, ...]
    schema_version: int = GRAPH_EXPANSION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _unique_by_id(items: Iterable[Any], attribute: str, label: str) -> tuple[Any, ...]:
    materialized = tuple(items)
    seen: set[str] = set()
    for item in materialized:
        identity = getattr(item, attribute, None)
        if not isinstance(identity, str):
            raise TypeError(f"{label} must expose a string {attribute}")
        if identity in seen:
            raise ValueError(f"duplicate {label} {attribute}: {identity}")
        seen.add(identity)
    return materialized


def _seed_node(
    chunk: GraphChunkRef, nodes: tuple[GraphNode, ...]
) -> tuple[GraphNode | None, GraphExpansionIssueCode | None]:
    file_nodes = [node for node in nodes if node.kind is GraphNodeKind.FILE]
    symbols = [node for node in nodes if node.kind is not GraphNodeKind.FILE]
    exact = [
        node
        for node in symbols
        if node.start_line == chunk.start_line and node.end_line == chunk.end_line
    ]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, GraphExpansionIssueCode.AMBIGUOUS_SEED

    containing = [
        node
        for node in symbols
        if node.start_line <= chunk.start_line and node.end_line >= chunk.end_line
    ]
    if containing:
        smallest_span = min(node.end_line - node.start_line for node in containing)
        smallest = [
            node
            for node in containing
            if node.end_line - node.start_line == smallest_span
        ]
        if len(smallest) == 1:
            return smallest[0], None
        return None, GraphExpansionIssueCode.AMBIGUOUS_SEED
    if len(file_nodes) == 1:
        return file_nodes[0], None
    if len(file_nodes) > 1:
        return None, GraphExpansionIssueCode.AMBIGUOUS_SEED
    return None, GraphExpansionIssueCode.UNMAPPED_SEED


def _neighbor_chunks(
    node: GraphNode, chunks: tuple[GraphChunkRef, ...]
) -> tuple[GraphChunkRef, ...]:
    if node.kind is GraphNodeKind.FILE:
        return chunks

    exact = tuple(
        chunk
        for chunk in chunks
        if chunk.start_line == node.start_line and chunk.end_line == node.end_line
    )
    if exact:
        return exact

    containers = tuple(
        chunk
        for chunk in chunks
        if chunk.start_line <= node.start_line and chunk.end_line >= node.end_line
    )
    if containers:
        smallest_span = min(
            chunk.end_line - chunk.start_line for chunk in containers
        )
        return tuple(
            chunk
            for chunk in containers
            if chunk.end_line - chunk.start_line == smallest_span
        )

    # Future indexers may split a large symbol into multiple chunks.
    return tuple(
        chunk
        for chunk in chunks
        if node.start_line <= chunk.start_line and node.end_line >= chunk.end_line
    )


def expand_graph_one_hop(
    seed_chunks: Iterable[GraphChunkRef],
    available_chunks: Iterable[GraphChunkRef],
    nodes: Iterable[GraphNode],
    edges: Iterable[GraphEdge],
    *,
    edge_kinds: Iterable[GraphEdgeKind] | None = None,
    direction: GraphTraversalDirection = GraphTraversalDirection.BOTH,
) -> GraphExpansionResult:
    """Map ranked seed chunks to graph nodes and emit raw one-hop candidates.

    Seed order is significant and becomes ``seed_rank``.  Repeated neighbor
    chunks reached through different seeds or edges are intentionally retained
    for GRAPH-006 to score, budget, and deduplicate.
    """
    seeds = _unique_by_id(seed_chunks, "uid", "seed chunk")
    chunks = _unique_by_id(available_chunks, "uid", "available chunk")
    graph_nodes = _unique_by_id(nodes, "node_id", "graph node")
    graph_edges = _unique_by_id(edges, "edge_id", "graph edge")
    if len(seeds) > MAX_EXPANSION_SEEDS:
        raise ValueError("seed chunk count exceeds the expansion limit")
    if len(chunks) > MAX_EXPANSION_CHUNKS:
        raise ValueError("available chunk count exceeds the expansion limit")
    if not isinstance(direction, GraphTraversalDirection):
        raise TypeError("direction must be GraphTraversalDirection")

    if edge_kinds is None:
        selected_kinds = frozenset(GraphEdgeKind)
    else:
        selected_kinds = frozenset(edge_kinds)
        if any(not isinstance(kind, GraphEdgeKind) for kind in selected_kinds):
            raise TypeError("edge_kinds must contain GraphEdgeKind values")

    node_by_id = {node.node_id: node for node in graph_nodes}
    for edge in graph_edges:
        if edge.source_id not in node_by_id or edge.target_id not in node_by_id:
            raise ValueError(f"graph edge has an unknown endpoint: {edge.edge_id}")

    nodes_by_file: dict[str, list[GraphNode]] = {}
    chunks_by_file: dict[str, list[GraphChunkRef]] = {}
    for node in graph_nodes:
        nodes_by_file.setdefault(node.file, []).append(node)
    for chunk in chunks:
        chunks_by_file.setdefault(chunk.file, []).append(chunk)
    for values in nodes_by_file.values():
        values.sort(key=lambda item: (item.start_line, item.end_line, item.node_id))
    for values in chunks_by_file.values():
        values.sort(key=lambda item: (item.start_line, item.end_line, item.uid))

    adjacency: dict[str, list[tuple[GraphEdge, GraphTraversalDirection, str]]] = {}
    for edge in sorted(graph_edges, key=lambda item: (item.kind.value, item.edge_id)):
        if edge.kind not in selected_kinds:
            continue
        if direction in {GraphTraversalDirection.OUTGOING, GraphTraversalDirection.BOTH}:
            adjacency.setdefault(edge.source_id, []).append(
                (edge, GraphTraversalDirection.OUTGOING, edge.target_id)
            )
        if (
            direction
            in {GraphTraversalDirection.INCOMING, GraphTraversalDirection.BOTH}
            and (
                edge.source_id != edge.target_id
                or direction is GraphTraversalDirection.INCOMING
            )
        ):
            adjacency.setdefault(edge.target_id, []).append(
                (edge, GraphTraversalDirection.INCOMING, edge.source_id)
            )

    candidates: list[GraphExpansionCandidate] = []
    issues: list[GraphExpansionIssue] = []
    for seed_rank, seed in enumerate(seeds, start=1):
        seed_node, problem = _seed_node(
            seed, tuple(nodes_by_file.get(seed.file, ()))
        )
        if seed_node is None:
            assert problem is not None
            issues.append(GraphExpansionIssue(code=problem, seed_uid=seed.uid))
            continue
        for edge, traversal, neighbor_id in adjacency.get(seed_node.node_id, ()):
            neighbor = node_by_id[neighbor_id]
            matches = _neighbor_chunks(
                neighbor, tuple(chunks_by_file.get(neighbor.file, ()))
            )
            if not matches:
                issues.append(
                    GraphExpansionIssue(
                        code=GraphExpansionIssueCode.UNMAPPED_NEIGHBOR,
                        seed_uid=seed.uid,
                        node_id=neighbor_id,
                        edge_id=edge.edge_id,
                    )
                )
                continue
            if len(candidates) + len(matches) > MAX_EXPANSION_CANDIDATES:
                raise ValueError("candidate count exceeds the expansion limit")
            candidates.extend(
                GraphExpansionCandidate(
                    seed_uid=seed.uid,
                    seed_rank=seed_rank,
                    seed_node_id=seed_node.node_id,
                    neighbor_node_id=neighbor_id,
                    edge_id=edge.edge_id,
                    edge_kind=edge.kind,
                    traversal_direction=traversal,
                    chunk=match,
                )
                for match in matches
            )

    return GraphExpansionResult(candidates=tuple(candidates), issues=tuple(issues))
