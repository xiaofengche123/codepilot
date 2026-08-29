"""GRAPH-004 pytest function-to-production tests edge mapping."""

import json

import pytest

import rag.code_graph_builder as graph_builder
from rag.code_graph import GraphEdgeKind
from rag.code_graph_builder import build_python_code_graph


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


def test_maps_direct_pytest_function_call_to_production_symbol():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "tests/test_app.py": (
            "from app import run\n"
            "def test_run():\n    assert run() == 1\n"
        ),
    })
    test = _node(result, "tests/test_app.py", "test_run")
    run = _node(result, "app.py", "run")
    assert (test.node_id, run.node_id) in _pairs(
        result, GraphEdgeKind.TESTS
    )


def test_tests_edge_has_test_to_production_direction():
    result = build_python_code_graph({
        "app.py": "class Service:\n    pass\n",
        "test_service.py": (
            "from app import Service\n"
            "def test_service():\n    Service()\n"
        ),
    })
    test = _node(result, "test_service.py", "test_service")
    service = _node(result, "app.py", "Service")
    tests = _pairs(result, GraphEdgeKind.TESTS)
    assert (test.node_id, service.node_id) in tests
    assert (service.node_id, test.node_id) not in tests


def test_maps_test_method_in_top_level_class():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "tests/test_app.py": (
            "from app import run\n"
            "class TestScenario:\n"
            "    def test_run(self):\n        run()\n"
        ),
    })
    test = _node(result, "tests/test_app.py", "TestScenario.test_run")
    run = _node(result, "app.py", "run")
    assert (test.node_id, run.node_id) in _pairs(
        result, GraphEdgeKind.TESTS
    )


def test_maps_async_test_and_suffix_test_file():
    result = build_python_code_graph({
        "worker.py": "async def execute():\n    return 1\n",
        "worker_test.py": (
            "from worker import execute\n"
            "async def test_execute():\n    await execute()\n"
        ),
    })
    test = _node(result, "worker_test.py", "test_execute")
    execute = _node(result, "worker.py", "execute")
    assert (test.node_id, execute.node_id) in _pairs(
        result, GraphEdgeKind.TESTS
    )


def test_test_named_function_in_non_test_file_is_not_collected():
    result = build_python_code_graph({
        "target.py": "def run():\n    return 1\n",
        "checks.py": (
            "from target import run\n"
            "def test_run():\n    run()\n"
        ),
    })
    assert not _pairs(result, GraphEdgeKind.TESTS)


def test_non_collected_nested_test_is_not_a_mapping_source():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "test_app.py": (
            "from app import run\n"
            "def factory():\n"
            "    def test_nested():\n        run()\n"
            "    return test_nested\n"
        ),
    })
    assert not _pairs(result, GraphEdgeKind.TESTS)


def test_test_named_method_in_non_test_class_is_not_collected():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "test_app.py": (
            "from app import run\n"
            "class Scenario:\n"
            "    def test_run(self):\n        run()\n"
        ),
    })
    assert not _pairs(result, GraphEdgeKind.TESTS)


def test_import_without_call_does_not_create_tests_edge():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "test_app.py": (
            "from app import run\n"
            "def test_import_only():\n    assert run is not None\n"
        ),
    })
    assert not _pairs(result, GraphEdgeKind.TESTS)


def test_dynamic_object_call_does_not_guess_test_target():
    result = build_python_code_graph({
        "app.py": "class Service:\n    def run(self):\n        return 1\n",
        "test_app.py": (
            "def test_dynamic(service):\n    service.run()\n"
        ),
    })
    assert not _pairs(result, GraphEdgeKind.TESTS)


def test_same_file_pytest_fixture_maps_test_to_fixture_target():
    result = build_python_code_graph({
        "app.py": "class Service:\n    pass\n",
        "test_app.py": (
            "import pytest\n"
            "from app import Service\n"
            "@pytest.fixture\n"
            "def service():\n    return Service()\n"
            "def test_service(service):\n    assert service is not None\n"
        ),
    })
    test = _node(result, "test_app.py", "test_service")
    service = _node(result, "app.py", "Service")
    assert (test.node_id, service.node_id) in _pairs(
        result, GraphEdgeKind.TESTS
    )


def test_fixture_dependency_chain_is_traversed():
    result = build_python_code_graph({
        "app.py": "def connect():\n    return 1\n",
        "test_app.py": (
            "import pytest\n"
            "from app import connect\n"
            "@pytest.fixture\n"
            "def client():\n    return connect()\n"
            "@pytest.fixture\n"
            "def session(client):\n    return client\n"
            "def test_session(session):\n    assert session\n"
        ),
    })
    test = _node(result, "test_app.py", "test_session")
    connect = _node(result, "app.py", "connect")
    assert (test.node_id, connect.node_id) in _pairs(
        result, GraphEdgeKind.TESTS
    )


def test_autouse_fixture_maps_all_tests_in_same_file():
    result = build_python_code_graph({
        "app.py": "def prepare():\n    return 1\n",
        "test_app.py": (
            "import pytest\n"
            "from app import prepare\n"
            "@pytest.fixture(autouse=True)\n"
            "def setup():\n    prepare()\n"
            "def test_ready():\n    assert True\n"
        ),
    })
    test = _node(result, "test_app.py", "test_ready")
    prepare = _node(result, "app.py", "prepare")
    assert (test.node_id, prepare.node_id) in _pairs(
        result, GraphEdgeKind.TESTS
    )


def test_explicit_fixture_import_alias_is_supported():
    result = build_python_code_graph({
        "app.py": "def prepare():\n    return 1\n",
        "test_app.py": (
            "from pytest import fixture as fx\n"
            "from app import prepare\n"
            "@fx\n"
            "def setup():\n    prepare()\n"
            "def test_ready(setup):\n    assert setup is None\n"
        ),
    })
    test = _node(result, "test_app.py", "test_ready")
    prepare = _node(result, "app.py", "prepare")
    assert (test.node_id, prepare.node_id) in _pairs(
        result, GraphEdgeKind.TESTS
    )


def test_plain_parameter_name_does_not_guess_an_undecorated_helper():
    result = build_python_code_graph({
        "app.py": "def prepare():\n    return 1\n",
        "test_app.py": (
            "from app import prepare\n"
            "def setup():\n    prepare()\n"
            "def test_ready(setup):\n    assert setup\n"
        ),
    })
    assert not _pairs(result, GraphEdgeKind.TESTS)


def test_conftest_fixture_is_not_guessed_without_pytest_scope_resolution():
    result = build_python_code_graph({
        "app.py": "def prepare():\n    return 1\n",
        "tests/conftest.py": (
            "import pytest\n"
            "from app import prepare\n"
            "@pytest.fixture\n"
            "def shared():\n    return prepare()\n"
        ),
        "tests/test_app.py": (
            "def test_ready(shared):\n    assert shared\n"
        ),
    })
    assert not _pairs(result, GraphEdgeKind.TESTS)


def test_traverses_test_file_helper_to_production_target():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "tests/test_app.py": (
            "from app import run\n"
            "def invoke():\n    return run()\n"
            "def test_run():\n    assert invoke() == 1\n"
        ),
    })
    test = _node(result, "tests/test_app.py", "test_run")
    run = _node(result, "app.py", "run")
    assert (test.node_id, run.node_id) in _pairs(
        result, GraphEdgeKind.TESTS
    )


def test_traverses_helper_chain_across_test_files():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "tests/test_helpers.py": (
            "from app import run\n"
            "def invoke():\n    return run()\n"
        ),
        "tests/test_app.py": (
            "from tests.test_helpers import invoke\n"
            "def test_run():\n    invoke()\n"
        ),
    })
    test = _node(result, "tests/test_app.py", "test_run")
    run = _node(result, "app.py", "run")
    assert (test.node_id, run.node_id) in _pairs(
        result, GraphEdgeKind.TESTS
    )


def test_traverses_non_collected_helper_module_under_tests_directory():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "tests/helpers.py": (
            "from app import run\n"
            "def invoke():\n    return run()\n"
        ),
        "tests/test_app.py": (
            "from tests.helpers import invoke\n"
            "def test_run():\n    invoke()\n"
        ),
    })
    test = _node(result, "tests/test_app.py", "test_run")
    run = _node(result, "app.py", "run")
    assert (test.node_id, run.node_id) in _pairs(
        result, GraphEdgeKind.TESTS
    )


def test_helper_cycle_terminates_and_keeps_reachable_target():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "test_app.py": (
            "from app import run\n"
            "def first():\n    second()\n"
            "def second():\n    first()\n    run()\n"
            "def test_run():\n    first()\n"
        ),
    })
    test = _node(result, "test_app.py", "test_run")
    run = _node(result, "app.py", "run")
    tests = _pairs(result, GraphEdgeKind.TESTS)
    assert tests == {(test.node_id, run.node_id)}


def test_helper_depth_is_bounded_and_does_not_overreach():
    helper_definitions = "".join(
        f"def helper_{index}():\n    helper_{index + 1}()\n"
        for index in range(9)
    )
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "test_app.py": (
            "from app import run\n"
            f"{helper_definitions}"
            "def helper_9():\n    run()\n"
            "def test_run():\n    helper_0()\n"
        ),
    })
    assert not _pairs(result, GraphEdgeKind.TESTS)


def test_mapping_work_limit_fails_closed(monkeypatch):
    monkeypatch.setattr(graph_builder, "MAX_TEST_MAPPING_STEPS", 0)
    with pytest.raises(ValueError, match="test mapping limit"):
        build_python_code_graph({
            "app.py": "def run():\n    return 1\n",
            "test_app.py": (
                "from app import run\n"
                "def test_run():\n    run()\n"
            ),
        })


def test_multiple_call_paths_deduplicate_tests_edge():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "test_app.py": (
            "from app import run\n"
            "def first():\n    run()\n"
            "def second():\n    run()\n"
            "def test_run():\n    first()\n    second()\n    run()\n"
        ),
    })
    assert len(_pairs(result, GraphEdgeKind.TESTS)) == 1


def test_each_collected_test_gets_its_own_mapping():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "test_app.py": (
            "from app import run\n"
            "def test_first():\n    run()\n"
            "def test_second():\n    test_first()\n"
        ),
    })
    first = _node(result, "test_app.py", "test_first")
    second = _node(result, "test_app.py", "test_second")
    run = _node(result, "app.py", "run")
    tests = _pairs(result, GraphEdgeKind.TESTS)
    assert (first.node_id, run.node_id) in tests
    assert (second.node_id, run.node_id) in tests


def test_windows_test_path_is_normalized_before_detection():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        r"tests\test_app.py": (
            "from app import run\n"
            "def test_run():\n    run()\n"
        ),
    })
    test = _node(result, "tests/test_app.py", "test_run")
    run = _node(result, "app.py", "run")
    assert (test.node_id, run.node_id) in _pairs(
        result, GraphEdgeKind.TESTS
    )


def test_plain_test_name_without_underscore_is_not_collected():
    result = build_python_code_graph({
        "app.py": "def run():\n    return 1\n",
        "test_app.py": (
            "from app import run\n"
            "def test():\n    run()\n"
        ),
    })
    assert not _pairs(result, GraphEdgeKind.TESTS)


def test_test_file_target_is_treated_as_helper_not_production():
    result = build_python_code_graph({
        "library_test.py": "def utility():\n    return 1\n",
        "test_app.py": (
            "from library_test import utility\n"
            "def test_utility():\n    utility()\n"
        ),
    })
    assert not _pairs(result, GraphEdgeKind.TESTS)


def test_tests_mapping_is_deterministic_and_content_free():
    sources = {
        "app.py": "def run():\n    return 'private value'\n",
        "test_app.py": (
            "from app import run\n"
            "def test_run():\n    run()\n"
        ),
    }
    first = build_python_code_graph(sources)
    second = build_python_code_graph(dict(reversed(tuple(sources.items()))))
    assert first.to_dict() == second.to_dict()
    rendered = json.dumps(first.to_dict())
    assert "private value" not in rendered
