"""GRAPH-003 conservative calls/inherits relation tests."""

import json

from rag.code_graph import GraphEdgeKind
from rag.code_graph_builder import GraphBuildIssueCode, build_python_code_graph


def _node(result, file, qualified_name):
    return next(
        node
        for node in result.nodes
        if node.file == file and node.qualified_name == qualified_name
    )


def _pairs(result, kind):
    return {
        (edge.source_id, edge.target_id)
        for edge in result.edges
        if edge.kind is kind
    }


def _issues(result, code):
    return [issue for issue in result.issues if issue.code is code]


def test_resolves_same_module_function_call():
    result = build_python_code_graph({
        "app.py": (
            "def helper():\n    return 1\n"
            "def run():\n    return helper()\n"
        )
    })
    run = _node(result, "app.py", "run")
    helper = _node(result, "app.py", "helper")
    assert (run.node_id, helper.node_id) in _pairs(
        result, GraphEdgeKind.CALLS
    )
    assert not _issues(result, GraphBuildIssueCode.UNRESOLVED_CALL)


def test_recursive_call_is_a_valid_calls_self_edge():
    result = build_python_code_graph({
        "app.py": "def recurse():\n    return recurse()\n"
    })
    recurse = _node(result, "app.py", "recurse")
    assert (recurse.node_id, recurse.node_id) in _pairs(
        result, GraphEdgeKind.CALLS
    )


def test_resolves_lexically_nested_function_call():
    result = build_python_code_graph({
        "app.py": (
            "def outer():\n"
            "    def inner():\n        return 1\n"
            "    return inner()\n"
        )
    })
    outer = _node(result, "app.py", "outer")
    inner = _node(result, "app.py", "outer.inner")
    assert (outer.node_id, inner.node_id) in _pairs(
        result, GraphEdgeKind.CALLS
    )


def test_resolves_self_and_cls_method_calls():
    result = build_python_code_graph({
        "service.py": (
            "class Service:\n"
            "    def helper(self):\n        return 1\n"
            "    @classmethod\n"
            "    def make(cls):\n        return cls()\n"
            "    def run(self):\n        return self.helper()\n"
        )
    })
    service = _node(result, "service.py", "Service")
    make = _node(result, "service.py", "Service.make")
    run = _node(result, "service.py", "Service.run")
    helper = _node(result, "service.py", "Service.helper")
    calls = _pairs(result, GraphEdgeKind.CALLS)
    assert (make.node_id, service.node_id) in calls
    assert (run.node_id, helper.node_id) in calls


def test_resolves_direct_imported_symbol_alias():
    result = build_python_code_graph({
        "pkg/mod.py": "def helper():\n    return 1\n",
        "app.py": "from pkg.mod import helper as h\ndef run():\n    h()\n",
    })
    run = _node(result, "app.py", "run")
    helper = _node(result, "pkg/mod.py", "helper")
    assert (run.node_id, helper.node_id) in _pairs(
        result, GraphEdgeKind.CALLS
    )


def test_resolves_module_alias_attribute_call():
    result = build_python_code_graph({
        "pkg/mod.py": "def helper():\n    return 1\n",
        "app.py": "import pkg.mod as mod\ndef run():\n    mod.helper()\n",
    })
    run = _node(result, "app.py", "run")
    helper = _node(result, "pkg/mod.py", "helper")
    assert (run.node_id, helper.node_id) in _pairs(
        result, GraphEdgeKind.CALLS
    )


def test_resolves_unaliased_dotted_module_call():
    result = build_python_code_graph({
        "pkg/mod.py": "def helper():\n    return 1\n",
        "app.py": "import pkg.mod\ndef run():\n    pkg.mod.helper()\n",
    })
    run = _node(result, "app.py", "run")
    helper = _node(result, "pkg/mod.py", "helper")
    assert (run.node_id, helper.node_id) in _pairs(
        result, GraphEdgeKind.CALLS
    )


def test_resolves_imported_class_constructor_and_static_method():
    result = build_python_code_graph({
        "pkg/service.py": (
            "class Service:\n"
            "    def run(self):\n        return 1\n"
        ),
        "app.py": (
            "from pkg.service import Service\n"
            "def build():\n    Service()\n    Service.run()\n"
        ),
    })
    build = _node(result, "app.py", "build")
    service = _node(result, "pkg/service.py", "Service")
    run = _node(result, "pkg/service.py", "Service.run")
    calls = _pairs(result, GraphEdgeKind.CALLS)
    assert (build.node_id, service.node_id) in calls
    assert (build.node_id, run.node_id) in calls


def test_resolves_local_and_imported_inheritance():
    result = build_python_code_graph({
        "pkg/base.py": "class RemoteBase:\n    pass\n",
        "models.py": (
            "from pkg.base import RemoteBase as RB\n"
            "class LocalBase:\n    pass\n"
            "class Child(LocalBase, RB):\n    pass\n"
        ),
    })
    child = _node(result, "models.py", "Child")
    local = _node(result, "models.py", "LocalBase")
    remote = _node(result, "pkg/base.py", "RemoteBase")
    inherits = _pairs(result, GraphEdgeKind.INHERITS)
    assert (child.node_id, local.node_id) in inherits
    assert (child.node_id, remote.node_id) in inherits


def test_resolves_module_qualified_generic_base():
    result = build_python_code_graph({
        "pkg/base.py": "class Base:\n    pass\n",
        "models.py": (
            "import pkg.base as base\n"
            "class Child(base.Base[int]):\n    pass\n"
        ),
    })
    child = _node(result, "models.py", "Child")
    base = _node(result, "pkg/base.py", "Base")
    assert (child.node_id, base.node_id) in _pairs(
        result, GraphEdgeKind.INHERITS
    )


def test_relative_import_resolves_call_and_inheritance_targets():
    result = build_python_code_graph({
        "pkg/base.py": (
            "class Base:\n    pass\n"
            "def helper():\n    return 1\n"
        ),
        "pkg/sub/models.py": (
            "from ..base import Base, helper\n"
            "class Child(Base):\n"
            "    def run(self):\n        return helper()\n"
        ),
    })
    child = _node(result, "pkg/sub/models.py", "Child")
    run = _node(result, "pkg/sub/models.py", "Child.run")
    base = _node(result, "pkg/base.py", "Base")
    helper = _node(result, "pkg/base.py", "helper")
    assert (child.node_id, base.node_id) in _pairs(
        result, GraphEdgeKind.INHERITS
    )
    assert (run.node_id, helper.node_id) in _pairs(
        result, GraphEdgeKind.CALLS
    )


def test_parameter_shadowing_prevents_false_global_call_edge():
    result = build_python_code_graph({
        "app.py": (
            "def helper():\n    return 1\n"
            "def run(helper):\n    return helper()\n"
        )
    })
    assert not _pairs(result, GraphEdgeKind.CALLS)
    assert _issues(result, GraphBuildIssueCode.UNRESOLVED_CALL)[0].reference == (
        "helper"
    )


def test_assignment_and_external_import_prevent_false_symbol_edges():
    result = build_python_code_graph({
        "app.py": (
            "def helper():\n    return 1\n"
            "def assigned():\n"
            "    helper = lambda: 2\n"
            "    return helper()\n"
        ),
        "consumer.py": (
            "from app import helper\n"
            "from external import helper\n"
            "helper()\n"
        ),
    })
    assert not _pairs(result, GraphEdgeKind.CALLS)
    references = {
        issue.reference
        for issue in _issues(result, GraphBuildIssueCode.UNRESOLVED_CALL)
    }
    assert references == {"helper"}


def test_global_rebinding_is_not_mistaken_for_original_function():
    result = build_python_code_graph({
        "app.py": (
            "def helper():\n    return 1\n"
            "def run():\n"
            "    global helper\n"
            "    helper = lambda: 2\n"
            "    return helper()\n"
        )
    })
    assert not _pairs(result, GraphEdgeKind.CALLS)
    issue = _issues(result, GraphBuildIssueCode.UNRESOLVED_CALL)[0]
    assert issue.reference == "helper"


def test_function_local_import_binding_does_not_leak_to_sibling_scope():
    result = build_python_code_graph({
        "helper.py": "def target():\n    return 1\n",
        "app.py": (
            "def first():\n"
            "    from helper import target\n"
            "    target()\n"
            "def second():\n    target()\n"
        ),
    })
    first = _node(result, "app.py", "first")
    second = _node(result, "app.py", "second")
    target = _node(result, "helper.py", "target")
    calls = _pairs(result, GraphEdgeKind.CALLS)
    assert (first.node_id, target.node_id) in calls
    assert all(source != second.node_id for source, _ in calls)
    unresolved = _issues(result, GraphBuildIssueCode.UNRESOLVED_CALL)
    assert [(issue.file, issue.reference) for issue in unresolved] == [
        ("app.py", "target")
    ]


def test_method_unqualified_name_skips_class_namespace():
    result = build_python_code_graph({
        "app.py": (
            "def helper():\n    return 1\n"
            "class Service:\n"
            "    def helper(self):\n        return 2\n"
            "    def run(self):\n        return helper()\n"
        )
    })
    run = _node(result, "app.py", "Service.run")
    module_helper = _node(result, "app.py", "helper")
    class_helper = _node(result, "app.py", "Service.helper")
    calls = _pairs(result, GraphEdgeKind.CALLS)
    assert (run.node_id, module_helper.node_id) in calls
    assert (run.node_id, class_helper.node_id) not in calls


def test_self_attribute_without_a_method_receiver_is_not_guessed():
    result = build_python_code_graph({
        "app.py": (
            "class Service:\n"
            "    def helper(self):\n        return 1\n"
            "    @staticmethod\n"
            "    def run():\n        return self.helper()\n"
        )
    })
    assert not _pairs(result, GraphEdgeKind.CALLS)
    issue = _issues(result, GraphBuildIssueCode.UNRESOLVED_CALL)[0]
    assert issue.reference == "self.helper"


def test_decorator_call_belongs_to_enclosing_file_scope():
    result = build_python_code_graph({
        "app.py": (
            "def decorate():\n    return lambda value: value\n"
            "@decorate()\n"
            "def target():\n    return 1\n"
        )
    })
    file = _node(result, "app.py", "app.py")
    decorate = _node(result, "app.py", "decorate")
    target = _node(result, "app.py", "target")
    calls = _pairs(result, GraphEdgeKind.CALLS)
    assert (file.node_id, decorate.node_id) in calls
    assert (target.node_id, decorate.node_id) not in calls


def test_repeated_calls_deduplicate_structural_edge():
    result = build_python_code_graph({
        "app.py": (
            "def helper():\n    return 1\n"
            "def run():\n    helper()\n    helper()\n"
        )
    })
    assert len(_pairs(result, GraphEdgeKind.CALLS)) == 1


def test_unresolved_dynamic_call_and_base_are_issues_not_edges():
    result = build_python_code_graph({
        "app.py": (
            "class Child(factory()):\n    pass\n"
            "def run(client):\n    client.execute()\n"
        )
    })
    assert not _pairs(result, GraphEdgeKind.INHERITS)
    assert not _pairs(result, GraphEdgeKind.CALLS)
    assert _issues(result, GraphBuildIssueCode.UNRESOLVED_BASE)[0].reference == (
        "dynamic_base"
    )
    call_references = {
        issue.reference
        for issue in _issues(result, GraphBuildIssueCode.UNRESOLVED_CALL)
    }
    assert call_references == {"client.execute", "factory"}


def test_self_inheritance_is_an_issue_not_an_invalid_edge():
    result = build_python_code_graph({"app.py": "class Item(Item):\n    pass\n"})
    assert not _pairs(result, GraphEdgeKind.INHERITS)
    issue = _issues(result, GraphBuildIssueCode.SELF_INHERITANCE)[0]
    assert issue.reference == "Item"


def test_lambda_and_comprehension_calls_are_outside_simple_scope():
    result = build_python_code_graph({
        "app.py": (
            "def helper(value=0):\n    return value\n"
            "callback = lambda: helper()\n"
            "values = [helper(item) for item in range(2)]\n"
        )
    })
    assert not _pairs(result, GraphEdgeKind.CALLS)
    assert not _issues(result, GraphBuildIssueCode.UNRESOLVED_CALL)


def test_relation_serialization_is_content_free_and_deterministic():
    sources = {
        "base.py": "class Base:\n    pass\n",
        "app.py": (
            "from base import Base\n"
            "class Child(Base):\n"
            "    def run(self):\n        return Child()\n"
        ),
    }
    first = build_python_code_graph(sources)
    second = build_python_code_graph(dict(reversed(tuple(sources.items()))))
    assert first.to_dict() == second.to_dict()
    rendered = json.dumps(first.to_dict())
    assert "return Child" not in rendered
