"""GRAPH-006 structural scoring, deduplication, and budget tests."""

from dataclasses import FrozenInstanceError, replace
import json

import pytest

import rag.code_graph_context as context
from rag.code_graph import GraphEdge, GraphEdgeKind, GraphNode, GraphNodeKind
from rag.code_graph_context import (
    DEFAULT_GRAPH_SCORING_POLICY,
    GRAPH_CONTEXT_SCHEMA_VERSION,
    MAX_CHUNK_TOKEN_COUNT,
    MAX_CONTEXT_CHUNKS,
    MAX_CONTEXT_TOKEN_BUDGET,
    GraphStructureScoringPolicy,
    score_graph_candidate,
    select_graph_context,
)
from rag.code_graph_expansion import (
    GraphChunkRef,
    GraphExpansionCandidate,
    GraphTraversalDirection,
    expand_graph_one_hop,
)


def _candidate(
    uid: str,
    *,
    edge_kind: GraphEdgeKind = GraphEdgeKind.CALLS,
    direction: GraphTraversalDirection = GraphTraversalDirection.OUTGOING,
    seed_rank: int = 1,
    file: str | None = None,
    start: int = 10,
    end: int = 20,
    evidence: str = "a",
) -> GraphExpansionCandidate:
    return GraphExpansionCandidate(
        seed_uid=f"seed-{seed_rank}-{evidence}",
        seed_rank=seed_rank,
        seed_node_id=f"seed-node-{evidence}",
        neighbor_node_id=f"neighbor-node-{evidence}",
        edge_id=f"edge-{evidence}",
        edge_kind=edge_kind,
        traversal_direction=direction,
        chunk=GraphChunkRef(
            uid=uid,
            file=file or f"pkg/{uid}.py",
            start_line=start,
            end_line=end,
        ),
    )


def test_default_policy_is_frozen_transparent_and_untuned():
    policy = DEFAULT_GRAPH_SCORING_POLICY
    assert policy.calls_weight == 1.0
    assert policy.inherits_weight == 0.9
    assert policy.tests_weight == 0.8
    assert policy.imports_weight == 0.65
    assert policy.contains_weight == 0.5
    assert policy.outgoing_weight == 1.0
    assert policy.incoming_weight == 0.9
    assert policy.seed_rank_k == 10
    assert set(policy.to_dict()) == {
        "calls_weight",
        "inherits_weight",
        "tests_weight",
        "imports_weight",
        "contains_weight",
        "outgoing_weight",
        "incoming_weight",
        "seed_rank_k",
    }
    with pytest.raises(FrozenInstanceError):
        policy.calls_weight = 2.0  # type: ignore[misc]


@pytest.mark.parametrize(
    "value", [0, -1, float("nan"), float("inf"), 101, True, "1"]
)
def test_policy_rejects_invalid_weights(value):
    with pytest.raises((TypeError, ValueError)):
        GraphStructureScoringPolicy(calls_weight=value)


@pytest.mark.parametrize("value", [0, 10_001, True, 1.5, "10"])
def test_policy_rejects_invalid_seed_rank_k(value):
    with pytest.raises((TypeError, ValueError)):
        GraphStructureScoringPolicy(seed_rank_k=value)


def test_score_has_explainable_multiplicative_components():
    candidate = _candidate("callee")
    score = score_graph_candidate(candidate)
    assert score.edge_weight == 1.0
    assert score.direction_weight == 1.0
    assert score.seed_rank_weight == 1.0
    assert score.total == 1.0
    assert score.to_dict() == {
        "edge_weight": 1.0,
        "direction_weight": 1.0,
        "seed_rank_weight": 1.0,
        "total": 1.0,
    }


def test_score_penalizes_lower_seed_rank_and_incoming_direction():
    incoming = _candidate(
        "callee",
        direction=GraphTraversalDirection.INCOMING,
        seed_rank=2,
    )
    score = score_graph_candidate(incoming)
    assert score.direction_weight == 0.9
    assert score.seed_rank_weight == pytest.approx(11 / 12)
    assert score.total == pytest.approx(0.825)


def test_edge_weights_order_direct_symbols_before_broad_file_relations():
    scores = {
        kind: score_graph_candidate(_candidate(kind.value, edge_kind=kind)).total
        for kind in GraphEdgeKind
    }
    assert scores[GraphEdgeKind.CALLS] > scores[GraphEdgeKind.INHERITS]
    assert scores[GraphEdgeKind.INHERITS] > scores[GraphEdgeKind.TESTS]
    assert scores[GraphEdgeKind.TESTS] > scores[GraphEdgeKind.IMPORTS]
    assert scores[GraphEdgeKind.IMPORTS] > scores[GraphEdgeKind.CONTAINS]


def test_candidate_direction_both_is_rejected_as_not_an_actual_path():
    with pytest.raises(ValueError, match="outgoing or incoming"):
        score_graph_candidate(
            _candidate("bad", direction=GraphTraversalDirection.BOTH)
        )


def test_dedup_uses_best_path_without_summing_duplicate_evidence():
    weak = _candidate(
        "shared",
        edge_kind=GraphEdgeKind.CONTAINS,
        seed_rank=5,
        evidence="weak",
    )
    best = _candidate("shared", evidence="best")
    result = select_graph_context(
        [weak, best], {"shared": 20}, token_budget=100, max_chunks=10
    )
    assert len(result.selected) == 1
    selected = result.selected[0]
    assert selected.best_evidence is best
    assert selected.score.total == 1.0
    assert selected.evidence_count == 2
    assert result.input_candidate_count == 2
    assert result.unique_candidate_count == 1
    assert result.duplicate_candidate_count == 1


def test_same_uid_with_conflicting_chunk_metadata_is_rejected():
    first = _candidate("shared", file="a.py", evidence="a")
    second = _candidate("shared", file="b.py", evidence="b")
    with pytest.raises(ValueError, match="conflicting metadata"):
        select_graph_context(
            [first, second], {"shared": 10}, token_budget=100, max_chunks=10
        )


def test_exact_token_budget_is_never_exceeded():
    candidates = [_candidate("a"), _candidate("b", evidence="b")]
    result = select_graph_context(
        candidates, {"a": 60, "b": 40}, token_budget=100, max_chunks=10
    )
    assert [item.chunk.uid for item in result.selected] == ["b", "a"]
    assert result.selected_token_count == 100
    assert result.total_token_count == 100
    assert result.omitted_for_budget_count == 0


def test_reserved_tokens_reduce_available_graph_budget():
    candidate = _candidate("a")
    result = select_graph_context(
        [candidate],
        {"a": 41},
        token_budget=100,
        reserved_tokens=60,
        max_chunks=10,
    )
    assert result.available_token_budget == 40
    assert result.selected == ()
    assert result.selected_token_count == 0
    assert result.total_token_count == 60
    assert result.omitted_for_budget_count == 1


def test_oversized_high_score_is_skipped_so_smaller_candidate_can_fit():
    expensive = _candidate("expensive")
    smaller = _candidate(
        "smaller", edge_kind=GraphEdgeKind.INHERITS, evidence="small"
    )
    result = select_graph_context(
        [expensive, smaller],
        {"expensive": 101, "smaller": 40},
        token_budget=100,
        max_chunks=10,
    )
    assert [item.chunk.uid for item in result.selected] == ["smaller"]
    assert result.omitted_for_budget_count == 1


def test_chunk_limit_is_strict_and_reports_remaining_unique_chunks():
    candidates = [
        _candidate("a", evidence="a"),
        _candidate("b", edge_kind=GraphEdgeKind.INHERITS, evidence="b"),
        _candidate("c", edge_kind=GraphEdgeKind.TESTS, evidence="c"),
    ]
    result = select_graph_context(
        candidates,
        {"a": 1, "b": 1, "c": 1},
        token_budget=100,
        max_chunks=1,
    )
    assert [item.chunk.uid for item in result.selected] == ["a"]
    assert result.omitted_for_chunk_limit_count == 2
    assert result.omitted_for_budget_count == 0


def test_equal_scores_prefer_lower_cost_then_stable_uid():
    candidates = [
        _candidate("z", evidence="z"),
        _candidate("b", evidence="b"),
        _candidate("a", evidence="a"),
    ]
    result = select_graph_context(
        candidates,
        {"z": 5, "b": 3, "a": 3},
        token_budget=100,
        max_chunks=10,
    )
    assert [item.chunk.uid for item in result.selected] == ["a", "b", "z"]


def test_selection_is_deterministic_across_candidate_input_order():
    candidates = [
        _candidate("a", evidence="a"),
        _candidate("b", edge_kind=GraphEdgeKind.TESTS, evidence="b"),
        _candidate("c", edge_kind=GraphEdgeKind.IMPORTS, evidence="c"),
    ]
    costs = {"a": 30, "b": 20, "c": 10}
    forward = select_graph_context(
        candidates, costs, token_budget=40, max_chunks=10
    ).to_dict()
    reverse = select_graph_context(
        reversed(candidates), costs, token_budget=40, max_chunks=10
    ).to_dict()
    assert forward == reverse


def test_custom_policy_can_change_ranking_without_query_or_content():
    calls = _candidate("calls")
    imports = _candidate("imports", edge_kind=GraphEdgeKind.IMPORTS)
    policy = GraphStructureScoringPolicy(calls_weight=0.5, imports_weight=2.0)
    result = select_graph_context(
        [calls, imports],
        {"calls": 1, "imports": 1},
        token_budget=2,
        max_chunks=2,
        policy=policy,
    )
    assert [item.chunk.uid for item in result.selected] == ["imports", "calls"]


def test_missing_candidate_cost_is_rejected_instead_of_estimated():
    with pytest.raises(ValueError, match="missing token cost"):
        select_graph_context(
            [_candidate("a")], {}, token_budget=100, max_chunks=10
        )


@pytest.mark.parametrize(
    "costs",
    [
        [],
        {1: 10},
        {"": 10},
        {"bad\nuid": 10},
        {"a": 0},
        {"a": True},
        {"a": MAX_CHUNK_TOKEN_COUNT + 1},
    ],
)
def test_token_cost_mapping_is_strict_and_bounded(costs):
    with pytest.raises((TypeError, ValueError)):
        select_graph_context([], costs, token_budget=100, max_chunks=10)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("token_budget", -1),
        ("token_budget", MAX_CONTEXT_TOKEN_BUDGET + 1),
        ("token_budget", True),
        ("max_chunks", -1),
        ("max_chunks", MAX_CONTEXT_CHUNKS + 1),
        ("max_chunks", 1.5),
        ("reserved_tokens", -1),
        ("reserved_tokens", True),
    ],
)
def test_budget_parameters_are_strict_and_bounded(name, value):
    arguments = {
        "token_budget": 100,
        "max_chunks": 10,
        "reserved_tokens": 0,
    }
    arguments[name] = value
    with pytest.raises((TypeError, ValueError)):
        select_graph_context([], {}, **arguments)


def test_reserved_tokens_cannot_exceed_total_budget():
    with pytest.raises(ValueError, match="must not exceed"):
        select_graph_context(
            [], {}, token_budget=10, reserved_tokens=11, max_chunks=1
        )


def test_zero_budget_and_zero_chunk_limit_are_valid_no_ops():
    result = select_graph_context(
        [_candidate("a")], {"a": 1}, token_budget=0, max_chunks=0
    )
    assert result.selected == ()
    assert result.omitted_for_chunk_limit_count == 1


def test_candidate_count_has_a_hard_limit(monkeypatch):
    monkeypatch.setattr(context, "MAX_EXPANSION_CANDIDATES", 0)
    with pytest.raises(ValueError, match="candidate count"):
        select_graph_context(
            [_candidate("a")], {"a": 1}, token_budget=1, max_chunks=1
        )


def test_result_is_frozen_json_ready_and_content_free():
    result = select_graph_context(
        [_candidate("a")], {"a": 7}, token_budget=10, max_chunks=1
    )
    payload = result.to_dict()
    assert payload["schema_version"] == GRAPH_CONTEXT_SCHEMA_VERSION
    assert payload["selected_token_count"] == 7
    assert payload["selected"][0]["chunk"]["uid"] == "a"
    serialized = json.dumps(payload, sort_keys=True)
    assert "document" not in serialized
    assert "source" not in serialized
    assert "query" not in serialized
    with pytest.raises(FrozenInstanceError):
        result.token_budget = 20  # type: ignore[misc]


def test_rejects_wrong_candidate_or_policy_types():
    with pytest.raises(TypeError, match="GraphExpansionCandidate"):
        select_graph_context([object()], {}, token_budget=0, max_chunks=0)
    with pytest.raises(TypeError, match="policy"):
        score_graph_candidate(_candidate("a"), policy=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="policy"):
        select_graph_context(
            [], {}, token_budget=0, max_chunks=0, policy=object()  # type: ignore[arg-type]
        )


def test_best_evidence_tie_break_is_stable_across_input_order():
    first = _candidate("shared", evidence="z")
    second = replace(
        _candidate("shared", evidence="a"),
        seed_uid="seed-a",
        edge_id="edge-a",
    )
    forward = select_graph_context(
        [first, second], {"shared": 1}, token_budget=1, max_chunks=1
    )
    reverse = select_graph_context(
        [second, first], {"shared": 1}, token_budget=1, max_chunks=1
    )
    assert forward.to_dict() == reverse.to_dict()
    assert forward.selected[0].best_evidence is first


def test_empty_candidate_set_reports_zero_counts():
    result = select_graph_context([], {}, token_budget=100, max_chunks=10)
    assert result.selected == ()
    assert result.input_candidate_count == 0
    assert result.unique_candidate_count == 0
    assert result.duplicate_candidate_count == 0
    assert result.selected_token_count == 0


def test_graph_005_output_flows_directly_into_graph_006_selection():
    file = GraphNode.file_node("pkg/service.py", end_line=100)
    caller = GraphNode.symbol_node(
        GraphNodeKind.FUNCTION,
        file.file,
        "caller",
        "caller",
        start_line=10,
        end_line=20,
        parent_id=file.node_id,
    )
    callee = GraphNode.symbol_node(
        GraphNodeKind.FUNCTION,
        file.file,
        "callee",
        "callee",
        start_line=30,
        end_line=40,
        parent_id=file.node_id,
    )
    edge = GraphEdge.create(GraphEdgeKind.CALLS, caller.node_id, callee.node_id)
    seed = GraphChunkRef("caller", file.file, 10, 20)
    neighbor = GraphChunkRef("callee", file.file, 30, 40)
    expanded = expand_graph_one_hop(
        [seed],
        [seed, neighbor],
        [file, caller, callee],
        [edge],
        direction=GraphTraversalDirection.OUTGOING,
    )
    selected = select_graph_context(
        expanded.candidates,
        {"callee": 25},
        token_budget=25,
        max_chunks=1,
    )
    assert [item.chunk.uid for item in selected.selected] == ["callee"]
    assert selected.selected_token_count == 25
