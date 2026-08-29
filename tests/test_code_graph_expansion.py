"""GRAPH-005 seed Chunk to one-hop graph-neighbor expansion tests."""

from dataclasses import FrozenInstanceError
import json

import pytest

import rag.code_graph_expansion as expansion
from rag.code_graph import GraphEdge, GraphEdgeKind, GraphNode, GraphNodeKind
from rag.code_graph_expansion import (
    GRAPH_EXPANSION_SCHEMA_VERSION,
    GraphChunkRef,
    GraphExpansionIssueCode,
    GraphTraversalDirection,
    expand_graph_one_hop,
)


def _file(path: str, end: int = 200) -> GraphNode:
    return GraphNode.file_node(path, end_line=end)


def _function(
    file: GraphNode,
    name: str,
    start: int,
    end: int,
    *,
    parent: GraphNode | None = None,
) -> GraphNode:
    qualified_name = f"{parent.name}.{name}" if parent else name
    return GraphNode.symbol_node(
        GraphNodeKind.FUNCTION,
        file.file,
        name,
        qualified_name,
        start_line=start,
        end_line=end,
        parent_id=(parent or file).node_id,
    )


def _class(file: GraphNode, name: str, start: int, end: int) -> GraphNode:
    return GraphNode.symbol_node(
        GraphNodeKind.CLASS,
        file.file,
        name,
        name,
        start_line=start,
        end_line=end,
        parent_id=file.node_id,
    )


def _chunk(uid: str, file: str, start: int, end: int) -> GraphChunkRef:
    return GraphChunkRef(uid=uid, file=file, start_line=start, end_line=end)


def _call_fixture():
    file = _file("pkg/service.py")
    caller = _function(file, "caller", 10, 20)
    callee = _function(file, "callee", 30, 40)
    edge = GraphEdge.create(GraphEdgeKind.CALLS, caller.node_id, callee.node_id)
    caller_chunk = _chunk("caller", file.file, 10, 20)
    callee_chunk = _chunk("callee", file.file, 30, 40)
    return file, caller, callee, edge, caller_chunk, callee_chunk


def test_chunk_ref_normalizes_index_metadata_and_serializes_without_content():
    chunk = GraphChunkRef.from_metadata(
        "pkg/service.py:10-20",
        {"file": r"pkg\service.py", "start_line": 10, "end_line": 20},
    )
    assert chunk.file == "pkg/service.py"
    assert chunk.to_dict() == {
        "uid": "pkg/service.py:10-20",
        "file": "pkg/service.py",
        "start_line": 10,
        "end_line": 20,
    }
    assert not ({"document", "content", "source"} & set(chunk.to_dict()))
    with pytest.raises(FrozenInstanceError):
        chunk.uid = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"file": "a.py", "start_line": 1},
        {"file": "../a.py", "start_line": 1, "end_line": 2},
        {"file": "a.py", "start_line": True, "end_line": 2},
        {"file": "a.py", "start_line": 3, "end_line": 2},
    ],
)
def test_chunk_ref_rejects_incomplete_or_unsafe_metadata(metadata):
    with pytest.raises((TypeError, ValueError)):
        GraphChunkRef.from_metadata("chunk", metadata)


def test_outgoing_call_expands_to_the_callee_chunk():
    file, caller, callee, edge, caller_chunk, callee_chunk = _call_fixture()
    result = expand_graph_one_hop(
        [caller_chunk],
        [caller_chunk, callee_chunk],
        [file, caller, callee],
        [edge],
        direction=GraphTraversalDirection.OUTGOING,
    )
    assert [candidate.chunk.uid for candidate in result.candidates] == ["callee"]
    candidate = result.candidates[0]
    assert candidate.seed_rank == 1
    assert candidate.edge_kind is GraphEdgeKind.CALLS
    assert candidate.traversal_direction is GraphTraversalDirection.OUTGOING
    assert candidate.seed_node_id == caller.node_id
    assert candidate.neighbor_node_id == callee.node_id
    assert result.issues == ()


def test_incoming_call_expands_to_the_caller_chunk():
    file, caller, callee, edge, caller_chunk, callee_chunk = _call_fixture()
    result = expand_graph_one_hop(
        [callee_chunk],
        [caller_chunk, callee_chunk],
        [file, caller, callee],
        [edge],
        direction=GraphTraversalDirection.INCOMING,
    )
    assert [candidate.chunk.uid for candidate in result.candidates] == ["caller"]
    assert result.candidates[0].traversal_direction is GraphTraversalDirection.INCOMING


def test_both_direction_finds_outgoing_and_incoming_neighbors():
    file = _file("flow.py")
    first = _function(file, "first", 10, 20)
    middle = _function(file, "middle", 30, 40)
    last = _function(file, "last", 50, 60)
    edges = [
        GraphEdge.create(GraphEdgeKind.CALLS, first.node_id, middle.node_id),
        GraphEdge.create(GraphEdgeKind.CALLS, middle.node_id, last.node_id),
    ]
    chunks = [
        _chunk("first", file.file, 10, 20),
        _chunk("middle", file.file, 30, 40),
        _chunk("last", file.file, 50, 60),
    ]
    result = expand_graph_one_hop(
        [chunks[1]], chunks, [file, first, middle, last], edges
    )
    assert {(item.chunk.uid, item.traversal_direction) for item in result.candidates} == {
        ("first", GraphTraversalDirection.INCOMING),
        ("last", GraphTraversalDirection.OUTGOING),
    }


def test_edge_kind_filter_is_explicit_and_can_be_empty():
    file, caller, callee, edge, caller_chunk, callee_chunk = _call_fixture()
    nodes = [file, caller, callee]
    chunks = [caller_chunk, callee_chunk]
    assert not expand_graph_one_hop(
        [caller_chunk], chunks, nodes, [edge], edge_kinds=[]
    ).candidates
    assert not expand_graph_one_hop(
        [caller_chunk],
        chunks,
        nodes,
        [edge],
        edge_kinds=[GraphEdgeKind.INHERITS],
    ).candidates


def test_contains_edges_can_expand_between_file_and_symbol_chunks():
    file = _file("module.py")
    function = _function(file, "run", 10, 20)
    edge = GraphEdge.create(GraphEdgeKind.CONTAINS, file.node_id, function.node_id)
    module_chunk = _chunk("module", file.file, 1, 5)
    function_chunk = _chunk("run", file.file, 10, 20)
    outgoing = expand_graph_one_hop(
        [module_chunk], [module_chunk, function_chunk], [file, function], [edge]
    )
    incoming = expand_graph_one_hop(
        [function_chunk], [module_chunk, function_chunk], [file, function], [edge]
    )
    assert [item.chunk.uid for item in outgoing.candidates] == ["run"]
    assert [item.chunk.uid for item in incoming.candidates] == ["module", "run"]


def test_import_file_neighbor_maps_to_all_of_its_indexed_chunks():
    source = _file("app.py")
    target = _file("pkg/lib.py")
    imported = GraphEdge.create(GraphEdgeKind.IMPORTS, source.node_id, target.node_id)
    seed = _chunk("app-header", source.file, 1, 5)
    available = [
        seed,
        _chunk("lib-a", target.file, 10, 20),
        _chunk("lib-b", target.file, 30, 40),
    ]
    result = expand_graph_one_hop(
        [seed], available, [source, target], [imported],
        direction=GraphTraversalDirection.OUTGOING,
    )
    assert [item.chunk.uid for item in result.candidates] == ["lib-a", "lib-b"]


def test_incoming_tests_edge_reaches_test_from_production_seed():
    production = _file("pkg/service.py")
    tests = _file("tests/test_service.py")
    target = _function(production, "run", 10, 20)
    test = _function(tests, "test_run", 10, 20)
    edge = GraphEdge.create(GraphEdgeKind.TESTS, test.node_id, target.node_id)
    target_chunk = _chunk("target", production.file, 10, 20)
    test_chunk = _chunk("test", tests.file, 10, 20)
    result = expand_graph_one_hop(
        [target_chunk],
        [target_chunk, test_chunk],
        [production, tests, target, test],
        [edge],
        edge_kinds=[GraphEdgeKind.TESTS],
    )
    assert [item.chunk.uid for item in result.candidates] == ["test"]
    assert result.candidates[0].traversal_direction is GraphTraversalDirection.INCOMING


def test_exact_class_chunk_maps_to_class_instead_of_contained_method():
    file = _file("service.py")
    service = _class(file, "Service", 10, 80)
    method = _function(file, "run", 20, 30, parent=service)
    base = _class(file, "Base", 90, 110)
    edge = GraphEdge.create(GraphEdgeKind.INHERITS, service.node_id, base.node_id)
    class_chunk = _chunk("service", file.file, 10, 80)
    base_chunk = _chunk("base", file.file, 90, 110)
    result = expand_graph_one_hop(
        [class_chunk],
        [class_chunk, base_chunk],
        [file, service, method, base],
        [edge],
        direction=GraphTraversalDirection.OUTGOING,
    )
    assert [item.chunk.uid for item in result.candidates] == ["base"]
    assert result.candidates[0].seed_node_id == service.node_id


def test_method_neighbor_maps_back_to_the_smallest_containing_class_chunk():
    file = _file("service.py")
    caller = _function(file, "caller", 1, 5)
    service = _class(file, "Service", 10, 80)
    method = _function(file, "run", 20, 30, parent=service)
    edge = GraphEdge.create(GraphEdgeKind.CALLS, caller.node_id, method.node_id)
    caller_chunk = _chunk("caller", file.file, 1, 5)
    class_chunk = _chunk("service", file.file, 10, 80)
    result = expand_graph_one_hop(
        [caller_chunk],
        [caller_chunk, class_chunk],
        [file, caller, service, method],
        [edge],
        direction=GraphTraversalDirection.OUTGOING,
    )
    assert [item.chunk.uid for item in result.candidates] == ["service"]


def test_large_symbol_can_map_to_multiple_future_split_chunks():
    file = _file("large.py")
    caller = _function(file, "caller", 1, 5)
    large = _function(file, "large", 10, 100)
    edge = GraphEdge.create(GraphEdgeKind.CALLS, caller.node_id, large.node_id)
    seed = _chunk("caller", file.file, 1, 5)
    chunks = [seed, _chunk("part-1", file.file, 10, 50), _chunk("part-2", file.file, 51, 100)]
    result = expand_graph_one_hop(
        [seed], chunks, [file, caller, large], [edge],
        direction=GraphTraversalDirection.OUTGOING,
    )
    assert [item.chunk.uid for item in result.candidates] == ["part-1", "part-2"]


def test_unmapped_seed_and_neighbor_are_bounded_structured_issues():
    missing_seed = _chunk("missing", "missing.py", 1, 2)
    result = expand_graph_one_hop([missing_seed], [], [], [])
    assert result.candidates == ()
    assert result.issues[0].code is GraphExpansionIssueCode.UNMAPPED_SEED

    file, caller, callee, edge, caller_chunk, _ = _call_fixture()
    result = expand_graph_one_hop(
        [caller_chunk], [caller_chunk], [file, caller, callee], [edge]
    )
    assert result.issues[0].code is GraphExpansionIssueCode.UNMAPPED_NEIGHBOR
    assert result.issues[0].node_id == callee.node_id
    assert result.issues[0].edge_id == edge.edge_id


def test_ambiguous_exact_symbol_mapping_does_not_guess():
    file = _file("ambiguous.py")
    function = _function(file, "item", 10, 20)
    klass = _class(file, "Item", 10, 20)
    seed = _chunk("item", file.file, 10, 20)
    result = expand_graph_one_hop([seed], [seed], [file, function, klass], [])
    assert result.candidates == ()
    assert result.issues[0].code is GraphExpansionIssueCode.AMBIGUOUS_SEED


def test_seed_rank_and_raw_duplicates_are_preserved_for_graph_006():
    file, caller, callee, edge, caller_chunk, callee_chunk = _call_fixture()
    duplicate_range = _chunk("caller-copy", file.file, 10, 20)
    result = expand_graph_one_hop(
        [caller_chunk, duplicate_range],
        [caller_chunk, duplicate_range, callee_chunk],
        [file, caller, callee],
        [edge],
        direction=GraphTraversalDirection.OUTGOING,
    )
    assert [(item.seed_uid, item.seed_rank, item.chunk.uid) for item in result.candidates] == [
        ("caller", 1, "callee"),
        ("caller-copy", 2, "callee"),
    ]


def test_recursive_call_is_emitted_once_when_traversing_both_directions():
    file = _file("recursive.py")
    function = _function(file, "walk", 10, 20)
    edge = GraphEdge.create(GraphEdgeKind.CALLS, function.node_id, function.node_id)
    chunk = _chunk("walk", file.file, 10, 20)
    result = expand_graph_one_hop([chunk], [chunk], [file, function], [edge])
    assert len(result.candidates) == 1
    assert result.candidates[0].traversal_direction is GraphTraversalDirection.OUTGOING

    incoming = expand_graph_one_hop(
        [chunk], [chunk], [file, function], [edge],
        direction=GraphTraversalDirection.INCOMING,
    )
    assert len(incoming.candidates) == 1
    assert incoming.candidates[0].traversal_direction is GraphTraversalDirection.INCOMING


def test_rejects_duplicate_inputs_unknown_endpoints_and_wrong_options():
    file, caller, callee, edge, caller_chunk, callee_chunk = _call_fixture()
    with pytest.raises(ValueError, match="duplicate seed chunk"):
        expand_graph_one_hop([caller_chunk, caller_chunk], [], [], [])
    with pytest.raises(ValueError, match="duplicate available chunk"):
        expand_graph_one_hop([], [caller_chunk, caller_chunk], [], [])
    with pytest.raises(ValueError, match="duplicate graph node"):
        expand_graph_one_hop([], [], [file, file], [])
    with pytest.raises(ValueError, match="duplicate graph edge"):
        expand_graph_one_hop([], [], [file, caller, callee], [edge, edge])
    with pytest.raises(ValueError, match="unknown endpoint"):
        expand_graph_one_hop([], [], [file, caller], [edge])
    with pytest.raises(TypeError, match="direction"):
        expand_graph_one_hop([], [], [], [], direction="both")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="edge_kinds"):
        expand_graph_one_hop([], [], [], [], edge_kinds=["calls"])  # type: ignore[list-item]


def test_hard_safety_limits_raise_instead_of_silently_truncating(monkeypatch):
    file, caller, callee, edge, caller_chunk, callee_chunk = _call_fixture()
    monkeypatch.setattr(expansion, "MAX_EXPANSION_SEEDS", 0)
    with pytest.raises(ValueError, match="seed chunk count"):
        expand_graph_one_hop([caller_chunk], [], [], [])
    monkeypatch.setattr(expansion, "MAX_EXPANSION_SEEDS", 1)
    monkeypatch.setattr(expansion, "MAX_EXPANSION_CANDIDATES", 0)
    with pytest.raises(ValueError, match="candidate count"):
        expand_graph_one_hop(
            [caller_chunk],
            [caller_chunk, callee_chunk],
            [file, caller, callee],
            [edge],
        )


def test_result_serialization_is_stable_json_and_content_free():
    file, caller, callee, edge, caller_chunk, callee_chunk = _call_fixture()
    result = expand_graph_one_hop(
        [caller_chunk], [caller_chunk, callee_chunk], [file, caller, callee], [edge]
    )
    payload = result.to_dict()
    assert payload["schema_version"] == GRAPH_EXPANSION_SCHEMA_VERSION
    assert payload["candidates"][0]["chunk"]["uid"] == "callee"
    serialized = json.dumps(payload, sort_keys=True)
    assert "document" not in serialized
    assert "source" not in serialized


def test_empty_inputs_are_a_valid_no_op():
    assert expand_graph_one_hop([], [], [], []).to_dict() == {
        "schema_version": GRAPH_EXPANSION_SCHEMA_VERSION,
        "candidates": [],
        "issues": [],
    }
