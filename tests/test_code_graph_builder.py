"""GRAPH-002 Python contains/imports graph builder tests."""

import json

import pytest

from rag.code_graph import GraphEdge, GraphEdgeKind, GraphNodeKind
from rag.code_graph_builder import (
    MAX_SOURCE_CHARS_PER_FILE,
    GraphBuildIssueCode,
    build_python_code_graph,
)


def _node(result, file, qualified_name):
    return next(
        node
        for node in result.nodes
        if node.file == file and node.qualified_name == qualified_name
    )


def _edge_pairs(result, kind):
    return {
        (edge.source_id, edge.target_id)
        for edge in result.edges
        if edge.kind is kind
    }


def test_builds_file_class_method_and_nested_function_contains_edges():
    result = build_python_code_graph({
        "pkg/service.py": """
class Service:
    async def run(self):
        def local():
            return 1
        return local()
""".lstrip()
    })
    file = _node(result, "pkg/service.py", "pkg/service.py")
    cls = _node(result, "pkg/service.py", "Service")
    method = _node(result, "pkg/service.py", "Service.run")
    local = _node(result, "pkg/service.py", "Service.run.local")
    pairs = _edge_pairs(result, GraphEdgeKind.CONTAINS)
    assert (file.node_id, cls.node_id) in pairs
    assert (cls.node_id, method.node_id) in pairs
    assert (method.node_id, local.node_id) in pairs
    assert method.kind is GraphNodeKind.FUNCTION


def test_definition_inside_control_flow_keeps_lexical_parent():
    result = build_python_code_graph({
        "conditional.py": """
if True:
    def selected():
        return 1
""".lstrip()
    })
    file = _node(result, "conditional.py", "conditional.py")
    selected = _node(result, "conditional.py", "selected")
    assert (file.node_id, selected.node_id) in _edge_pairs(
        result, GraphEdgeKind.CONTAINS
    )


def test_absolute_imports_resolve_only_repository_modules():
    result = build_python_code_graph({
        "pkg/__init__.py": "",
        "pkg/helper.py": "def help():\n    return 1\n",
        "app.py": "import pkg.helper\nfrom pkg import helper\nimport external\n",
    })
    app = _node(result, "app.py", "app.py")
    helper = _node(result, "pkg/helper.py", "pkg/helper.py")
    imports = _edge_pairs(result, GraphEdgeKind.IMPORTS)
    assert (app.node_id, helper.node_id) in imports
    assert len(imports) == 1
    unresolved = [
        issue
        for issue in result.issues
        if issue.code is GraphBuildIssueCode.UNRESOLVED_IMPORT
    ]
    assert [(issue.file, issue.reference) for issue in unresolved] == [
        ("app.py", "external")
    ]


def test_from_module_symbol_resolves_to_defining_module_file():
    result = build_python_code_graph({
        "pkg/mod.py": "class Target:\n    pass\n",
        "consumer.py": "from pkg.mod import Target\n",
    })
    consumer = _node(result, "consumer.py", "consumer.py")
    module = _node(result, "pkg/mod.py", "pkg/mod.py")
    assert (consumer.node_id, module.node_id) in _edge_pairs(
        result, GraphEdgeKind.IMPORTS
    )
    assert not result.issues


def test_relative_imports_resolve_sibling_and_parent_package():
    result = build_python_code_graph({
        "pkg/__init__.py": "",
        "pkg/helper.py": "VALUE = 1\n",
        "pkg/sub/__init__.py": "",
        "pkg/sub/mod.py": "from .. import helper\nfrom . import local\n",
        "pkg/sub/local.py": "VALUE = 2\n",
    })
    source = _node(result, "pkg/sub/mod.py", "pkg/sub/mod.py")
    helper = _node(result, "pkg/helper.py", "pkg/helper.py")
    local = _node(result, "pkg/sub/local.py", "pkg/sub/local.py")
    imports = _edge_pairs(result, GraphEdgeKind.IMPORTS)
    assert (source.node_id, helper.node_id) in imports
    assert (source.node_id, local.node_id) in imports
    assert not result.issues


def test_relative_import_beyond_package_is_unresolved():
    result = build_python_code_graph({
        "pkg/mod.py": "from ...outside import value\n"
    })
    assert not _edge_pairs(result, GraphEdgeKind.IMPORTS)
    assert result.issues[0].code is GraphBuildIssueCode.UNRESOLVED_IMPORT
    assert result.issues[0].reference == "...outside"


def test_relative_import_cannot_escape_a_top_level_package():
    result = build_python_code_graph({
        "outside.py": "VALUE = 1\n",
        "pkg/mod.py": "from .. import outside\n",
    })
    assert not _edge_pairs(result, GraphEdgeKind.IMPORTS)
    assert result.issues[0].code is GraphBuildIssueCode.UNRESOLVED_IMPORT
    assert result.issues[0].reference == ".."


def test_import_inside_function_is_file_dependency():
    result = build_python_code_graph({
        "helper.py": "VALUE = 1\n",
        "app.py": "def run():\n    import helper\n    return helper.VALUE\n",
    })
    app = _node(result, "app.py", "app.py")
    helper = _node(result, "helper.py", "helper.py")
    assert (app.node_id, helper.node_id) in _edge_pairs(
        result, GraphEdgeKind.IMPORTS
    )


def test_duplicate_import_statements_deduplicate_edge():
    result = build_python_code_graph({
        "helper.py": "VALUE = 1\n",
        "app.py": "import helper\nimport helper as again\n",
    })
    assert len(_edge_pairs(result, GraphEdgeKind.IMPORTS)) == 1
    assert not result.issues


def test_self_import_is_issue_not_self_edge():
    result = build_python_code_graph({"app.py": "import app\n"})
    assert not _edge_pairs(result, GraphEdgeKind.IMPORTS)
    assert result.issues[0].code is GraphBuildIssueCode.SELF_IMPORT


def test_syntax_error_keeps_file_node_and_records_content_free_issue():
    result = build_python_code_graph({
        "bad.py": "def broken(:\n    secret = 'do not copy'\n"
    })
    assert len(result.nodes) == 1
    assert result.nodes[0].kind is GraphNodeKind.FILE
    assert result.edges == ()
    assert result.issues[0].to_dict() == {
        "code": "syntax_error",
        "file": "bad.py",
        "line": 1,
        "reference": "invalid_python_syntax",
    }


def test_duplicate_symbol_is_reported_and_not_duplicated():
    result = build_python_code_graph({
        "duplicate.py": "def target():\n    pass\ndef target():\n    pass\n"
    })
    symbols = [node for node in result.nodes if node.kind is GraphNodeKind.FUNCTION]
    assert len(symbols) == 1
    assert result.issues[0].code is GraphBuildIssueCode.DUPLICATE_SYMBOL
    assert result.issues[0].line == 3


def test_imports_in_duplicate_symbol_bodies_are_not_lost():
    result = build_python_code_graph({
        "first.py": "VALUE = 1\n",
        "second.py": "VALUE = 2\n",
        "app.py": (
            "def target():\n    import first\n"
            "def target():\n    import second\n"
        ),
    })
    app = _node(result, "app.py", "app.py")
    first = _node(result, "first.py", "first.py")
    second = _node(result, "second.py", "second.py")
    imports = _edge_pairs(result, GraphEdgeKind.IMPORTS)
    assert (app.node_id, first.node_id) in imports
    assert (app.node_id, second.node_id) in imports


def test_input_order_and_windows_separators_do_not_change_output():
    first = build_python_code_graph({
        r"pkg\b.py": "from pkg import a\n",
        "pkg/a.py": "def target():\n    pass\n",
    })
    second = build_python_code_graph({
        "pkg/a.py": "def target():\n    pass\n",
        "pkg/b.py": "from pkg import a\n",
    })
    assert first.to_dict() == second.to_dict()


def test_output_is_json_ready_and_contains_no_source_or_ast():
    result = build_python_code_graph({
        "app.py": "def target():\n    return 'private source'\n"
    })
    payload = result.to_dict()
    rendered = json.dumps(payload)
    assert "private source" not in rendered
    assert not ({"source", "content", "ast"} & set(payload))


@pytest.mark.parametrize("path", ["app.js", "README.md", "config.yaml"])
def test_builder_rejects_non_python_sources(path):
    with pytest.raises(ValueError, match="only Python"):
        build_python_code_graph({path: "content"})


def test_builder_rejects_invalid_source_mapping_and_values():
    with pytest.raises(TypeError, match="mapping"):
        build_python_code_graph([])
    with pytest.raises(TypeError, match="must be a string"):
        build_python_code_graph({"app.py": b"bytes"})
    with pytest.raises(ValueError, match="size limit"):
        build_python_code_graph({
            "app.py": "x" * (MAX_SOURCE_CHARS_PER_FILE + 1)
        })


def test_builder_rejects_duplicate_normalized_paths():
    with pytest.raises(ValueError, match="duplicate normalized"):
        build_python_code_graph({"pkg/app.py": "", r"pkg\app.py": ""})


def test_edge_contract_is_stable_validated_and_content_free():
    result = build_python_code_graph({
        "app.py": "def target():\n    pass\n"
    })
    edge = result.edges[0]
    recreated = GraphEdge.create(edge.kind, edge.source_id, edge.target_id)
    assert recreated == edge
    assert edge.to_dict()["kind"] == "contains"
    with pytest.raises(ValueError, match="self-referential"):
        GraphEdge.create(GraphEdgeKind.IMPORTS, edge.source_id, edge.source_id)


def test_builder_does_not_guess_unresolved_relation_edges():
    result = build_python_code_graph({
        "app.py": "class Child(Base):\n    def run(self):\n        return helper()\n"
    })
    assert {edge.kind for edge in result.edges} == {GraphEdgeKind.CONTAINS}
    assert {kind.value for kind in GraphEdgeKind} == {
        "calls",
        "contains",
        "imports",
        "inherits",
        "tests",
    }


def test_empty_mapping_produces_empty_deterministic_graph():
    result = build_python_code_graph({})
    assert result.nodes == result.edges == result.issues == ()
