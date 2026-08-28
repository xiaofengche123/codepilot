"""GRAPH-001 bounded file/class/function node contract tests."""

from dataclasses import FrozenInstanceError
import json

import pytest

from rag.code_graph import (
    GRAPH_NODE_SCHEMA_VERSION,
    GraphNode,
    GraphNodeKind,
    normalize_graph_path,
    stable_graph_node_id,
)


def _file():
    return GraphNode.file_node(r"rag\indexer.py", end_line=300)


def test_file_node_normalizes_path_and_has_stable_identity():
    first = _file()
    second = GraphNode.file_node("rag/indexer.py", end_line=350)
    assert first.node_id == second.node_id
    assert first.file == "rag/indexer.py"
    assert first.name == "indexer.py"
    assert first.qualified_name == "rag/indexer.py"
    assert first.parent_id is None


def test_class_and_function_nodes_use_parent_and_qualified_name():
    file = _file()
    class_node = GraphNode.symbol_node(
        GraphNodeKind.CLASS,
        file.file,
        "Indexer",
        "Indexer",
        start_line=10,
        end_line=80,
        parent_id=file.node_id,
    )
    method = GraphNode.symbol_node(
        GraphNodeKind.FUNCTION,
        file.file,
        "build",
        "Indexer.build",
        start_line=20,
        end_line=40,
        parent_id=class_node.node_id,
    )
    assert class_node.parent_id == file.node_id
    assert method.parent_id == class_node.node_id
    assert method.kind is GraphNodeKind.FUNCTION


def test_node_identity_survives_line_movement_but_not_symbol_change():
    file = _file()
    first = GraphNode.symbol_node(
        GraphNodeKind.FUNCTION, file.file, "build", "build",
        start_line=10, end_line=20, parent_id=file.node_id,
    )
    moved = GraphNode.symbol_node(
        GraphNodeKind.FUNCTION, file.file, "build", "build",
        start_line=100, end_line=120, parent_id=file.node_id,
    )
    renamed = GraphNode.symbol_node(
        GraphNodeKind.FUNCTION, file.file, "rebuild", "rebuild",
        start_line=100, end_line=120, parent_id=file.node_id,
    )
    assert first.node_id == moved.node_id
    assert first.node_id != renamed.node_id


def test_identity_separates_kinds_and_files():
    function_id = stable_graph_node_id(
        GraphNodeKind.FUNCTION, "a.py", "Target"
    )
    class_id = stable_graph_node_id(GraphNodeKind.CLASS, "a.py", "Target")
    other_file_id = stable_graph_node_id(
        GraphNodeKind.FUNCTION, "b.py", "Target"
    )
    assert len({function_id, class_id, other_file_id}) == 3


def test_to_dict_is_json_ready_and_contains_no_source_content():
    node = _file()
    payload = node.to_dict()
    assert payload["schema_version"] == GRAPH_NODE_SCHEMA_VERSION
    assert payload["kind"] == "file"
    assert set(payload) == {
        "schema_version", "node_id", "kind", "name", "qualified_name",
        "file", "start_line", "end_line", "parent_id", "language",
    }
    json.dumps(payload)
    assert not ({"source", "content", "docstring", "decorators"} & set(payload))


@pytest.mark.parametrize(
    "path",
    ["", "/absolute.py", "C:/absolute.py", "../escape.py", "a/../b.py", "a//b.py", "./a.py", "a.py\nsecret"],
)
def test_path_rejects_absolute_traversal_empty_and_multiline(path):
    with pytest.raises(ValueError):
        normalize_graph_path(path)


def test_path_requires_string_and_is_bounded():
    with pytest.raises(TypeError):
        normalize_graph_path(None)
    with pytest.raises(ValueError):
        normalize_graph_path("a" * 1_025 + ".py")


@pytest.mark.parametrize(
    "start,end,error",
    [
        (True, 2, TypeError),
        (1, False, TypeError),
        (0, 2, ValueError),
        (2, 1, ValueError),
        (1, 10_000_001, ValueError),
    ],
)
def test_source_range_is_strict_and_bounded(start, end, error):
    file = _file()
    with pytest.raises(error):
        GraphNode.symbol_node(
            GraphNodeKind.FUNCTION,
            file.file,
            "target",
            "target",
            start_line=start,
            end_line=end,
            parent_id=file.node_id,
        )


def test_symbol_requires_valid_non_self_parent():
    node_id = stable_graph_node_id(
        GraphNodeKind.FUNCTION, "a.py", "target"
    )
    with pytest.raises(ValueError, match="parent_id"):
        GraphNode(
            node_id=node_id,
            kind=GraphNodeKind.FUNCTION,
            name="target",
            qualified_name="target",
            file="a.py",
            start_line=1,
            end_line=2,
            parent_id=None,
        )
    with pytest.raises(ValueError, match="own parent"):
        GraphNode(
            node_id=node_id,
            kind=GraphNodeKind.FUNCTION,
            name="target",
            qualified_name="target",
            file="a.py",
            start_line=1,
            end_line=2,
            parent_id=node_id,
        )


def test_file_node_rejects_parent_and_spoofed_identity():
    file = _file()
    with pytest.raises(ValueError, match="file nodes"):
        GraphNode(
            node_id=file.node_id,
            kind=file.kind,
            name=file.name,
            qualified_name=file.qualified_name,
            file=file.file,
            start_line=1,
            end_line=2,
            parent_id=file.node_id,
        )
    with pytest.raises(ValueError, match="does not match"):
        GraphNode(
            node_id=stable_graph_node_id(GraphNodeKind.FILE, "other.py", "other.py"),
            kind=GraphNodeKind.FILE,
            name="indexer.py",
            qualified_name="rag/indexer.py",
            file="rag/indexer.py",
            start_line=1,
            end_line=2,
            parent_id=None,
        )


def test_names_are_bounded_single_line_strings():
    file = _file()
    for value in ("", "bad\nname", "x" * 257):
        with pytest.raises(ValueError):
            GraphNode.symbol_node(
                GraphNodeKind.CLASS,
                file.file,
                value,
                "Target",
                start_line=1,
                end_line=2,
                parent_id=file.node_id,
            )
    with pytest.raises(TypeError):
        GraphNode.symbol_node(
            GraphNodeKind.CLASS,
            file.file,
            123,
            "Target",
            start_line=1,
            end_line=2,
            parent_id=file.node_id,
        )


@pytest.mark.parametrize(
    "name,qualified_name",
    [
        ("bad name", "bad name"),
        ("123target", "123target"),
        ("target", "Owner.bad name"),
        ("target", "Owner.other"),
    ],
)
def test_symbol_names_follow_python_qualified_name_contract(
    name, qualified_name
):
    file = _file()
    with pytest.raises(ValueError):
        GraphNode.symbol_node(
            GraphNodeKind.FUNCTION,
            file.file,
            name,
            qualified_name,
            start_line=1,
            end_line=2,
            parent_id=file.node_id,
        )


def test_symbol_names_support_unicode_python_identifiers():
    file = _file()
    node = GraphNode.symbol_node(
        GraphNodeKind.FUNCTION,
        file.file,
        "处理",
        "服务.处理",
        start_line=1,
        end_line=2,
        parent_id=file.node_id,
    )
    assert node.qualified_name == "服务.处理"


def test_node_is_immutable_and_slots_prevent_content_attachment():
    node = _file()
    with pytest.raises(FrozenInstanceError):
        node.end_line = 999
    with pytest.raises((AttributeError, TypeError)):
        node.source = "secret"


def test_symbol_factory_rejects_file_kind_and_direct_kind_strings():
    file = _file()
    with pytest.raises(ValueError, match="class or function"):
        GraphNode.symbol_node(
            GraphNodeKind.FILE,
            file.file,
            "x",
            "x",
            start_line=1,
            end_line=2,
            parent_id=file.node_id,
        )
    with pytest.raises(TypeError, match="GraphNodeKind"):
        stable_graph_node_id("function", "a.py", "target")


def test_module_has_no_ast_io_or_retrieval_dependencies():
    import rag.code_graph as module

    assert "ast" not in module.__dict__
    assert "Path" not in module.__dict__
    assert "retrieve" not in module.__dict__
