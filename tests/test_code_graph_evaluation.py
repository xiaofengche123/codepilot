"""GRAPH-007 freeze, augmentation, metrics, and privacy tests."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

import rag.code_graph_evaluation as graph_eval
from rag.code_graph_builder import build_python_code_graph
from rag.code_graph_evaluation import (
    DATASET_NAME,
    MANIFEST_NAME,
    GraphChunkRef,
    augment_fixed_hits,
    check_cross_module_manifest,
    evaluation_profile,
    freeze_cross_module_dataset,
    profile_sha256,
    render_markdown,
    validate_cross_module_items,
)
from rag.indexer import _split_python
from rag.retriever import SearchHit


SOURCES = {
    "a.py": "from b import target\n\ndef source():\n    return target()\n",
    "b.py": "def target():\n    return 1\n",
}


def _chunks(root: Path, sources=SOURCES):
    result = []
    for file, source in sources.items():
        for chunk in _split_python(root / file, source, file):
            result.append(
                GraphChunkRef(
                    f"{file}:{chunk['start_line']}-{chunk['end_line']}",
                    file,
                    chunk["start_line"],
                    chunk["end_line"],
                )
            )
    return tuple(result)


def _item(query="source calls target", **overrides):
    values = {
        "id": "CGV1-C01",
        "category": "cross_module",
        "query": query,
        "seed": "a.py:3-4",
        "required": ["a.py:3-4", "b.py:1-2"],
        "supporting": [],
    }
    values.update(overrides)
    return values


def _patch_validation_world(monkeypatch, tmp_path):
    monkeypatch.setattr(graph_eval, "EXPECTED_QUERY_COUNT", 1)
    monkeypatch.setattr(graph_eval, "_tracked_python_sources", lambda root: SOURCES)
    monkeypatch.setattr(
        graph_eval, "_source_chunks", lambda root, sources: _chunks(tmp_path, sources)
    )


def _hit(uid, *, document="code", content_type="code"):
    file, lines = uid.rsplit(":", 1)
    start, end = (int(value) for value in lines.split("-"))
    return SearchHit(
        uid=uid,
        document=document,
        metadata={
            "file": file,
            "start_line": start,
            "end_line": end,
            "content_type": content_type,
        },
    )


def _graph():
    return build_python_code_graph(SOURCES)


def _baseline():
    return [
        _hit("a.py:3-4"),
        _hit("x1.py:1-2"),
        _hit("x2.py:1-2"),
        _hit("x3.py:1-2"),
        _hit("x4.py:1-2"),
        _hit("x5.py:1-2"),
        _hit("x6.py:1-2"),
        _hit("x7.py:1-2"),
        _hit("x8.py:1-2"),
        _hit("x9.py:1-2"),
    ]


def test_profile_freezes_every_graph_and_acceptance_choice():
    profile = evaluation_profile()
    assert profile["baseline"] == {
        "mode": "hybrid",
        "top_k": 10,
        "adaptive_routing": False,
        "rerank": False,
        "include_docs": False,
    }
    assert profile["graph"]["baseline_prefix_count"] == 5
    assert profile["graph"]["seed_count"] == 5
    assert profile["graph"]["max_chunks"] == 5
    assert profile["graph"]["token_budget"] == 2048
    assert profile["graph"]["edge_kinds"] == ["calls"]
    assert profile["graph"]["direction"] == "outgoing"
    assert profile["acceptance"]["minimum_recall_at_10_delta"] == 0.05


def test_profile_hash_is_stable_and_changes_with_policy():
    first = profile_sha256()
    second = profile_sha256(evaluation_profile())
    changed = evaluation_profile()
    changed["graph"]["max_chunks"] = 4
    assert first == second
    assert first != profile_sha256(changed)
    assert len(first) == 64


def test_validation_proves_exact_cross_file_one_hop_reachability(
    monkeypatch, tmp_path
):
    _patch_validation_world(monkeypatch, tmp_path)
    summary = validate_cross_module_items([_item()], tmp_path, [])
    assert summary["query_count"] == 1
    assert summary["required_labels"] == 2
    assert summary["reachable_target_labels"] == 1
    assert summary["development_query_overlap"] == 0


@pytest.mark.parametrize(
    "override",
    [
        {"id": "bad"},
        {"category": "natural_language"},
        {"seed": "b.py:1-2"},
        {"required": ["b.py:1-2"]},
        {"supporting": "not-a-list"},
        {"supporting": ["b.py:1-2"]},
        {"required": ["a.py:3-4", "missing.py:1-2"]},
    ],
)
def test_validation_rejects_bad_contracts(monkeypatch, tmp_path, override):
    _patch_validation_world(monkeypatch, tmp_path)
    with pytest.raises((TypeError, ValueError)):
        validate_cross_module_items([_item(**override)], tmp_path, [])


def test_validation_rejects_normalized_duplicates_and_dev_overlap(
    monkeypatch, tmp_path
):
    _patch_validation_world(monkeypatch, tmp_path)
    monkeypatch.setattr(graph_eval, "EXPECTED_QUERY_COUNT", 2)
    with pytest.raises(ValueError, match="unique"):
        validate_cross_module_items(
            [_item(), _item(query=" SOURCE   CALLS TARGET ", id="CGV1-C02")],
            tmp_path,
            [],
        )
    monkeypatch.setattr(graph_eval, "EXPECTED_QUERY_COUNT", 1)
    with pytest.raises(ValueError, match="overlap"):
        validate_cross_module_items(
            [_item()], tmp_path, [{"query": " Source  Calls TARGET "}]
        )


def test_validation_rejects_same_file_or_unreachable_target(monkeypatch, tmp_path):
    sources = {
        **SOURCES,
        "a.py": SOURCES["a.py"] + "\ndef local():\n    return 2\n",
        "c.py": "def unrelated():\n    return 3\n",
    }
    monkeypatch.setattr(graph_eval, "EXPECTED_QUERY_COUNT", 1)
    monkeypatch.setattr(graph_eval, "_tracked_python_sources", lambda root: sources)
    monkeypatch.setattr(
        graph_eval, "_source_chunks", lambda root, values: _chunks(tmp_path, values)
    )
    with pytest.raises(ValueError, match="cross-file"):
        validate_cross_module_items(
            [_item(required=["a.py:3-4", "a.py:6-7"])], tmp_path, []
        )
    with pytest.raises(ValueError, match="not one-hop"):
        validate_cross_module_items(
            [_item(required=["a.py:3-4", "c.py:1-2"])], tmp_path, []
        )


def test_validation_rejects_test_targets(monkeypatch, tmp_path):
    sources = {
        "a.py": "from tests.test_b import target\n\ndef source():\n    return target()\n",
        "tests/test_b.py": "def target():\n    return 1\n",
    }
    monkeypatch.setattr(graph_eval, "EXPECTED_QUERY_COUNT", 1)
    monkeypatch.setattr(graph_eval, "_tracked_python_sources", lambda root: sources)
    monkeypatch.setattr(
        graph_eval, "_source_chunks", lambda root, values: _chunks(tmp_path, values)
    )
    item = _item(required=["a.py:3-4", "tests/test_b.py:1-2"])
    with pytest.raises(ValueError, match="production Python"):
        validate_cross_module_items([item], tmp_path, [])


def test_freeze_writes_unscored_manifest_with_dataset_and_profile_hashes(
    monkeypatch, tmp_path
):
    dataset = tmp_path / DATASET_NAME
    manifest = tmp_path / MANIFEST_NAME
    dev = tmp_path / graph_eval.DEVELOPMENT_DATASET_NAME
    dataset.write_text(json.dumps([_item()]), encoding="utf-8")
    dev.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        graph_eval,
        "validate_cross_module_items",
        lambda items, root, development: {
            "query_count": 1,
            "categories": {"cross_module": 1},
            "required_labels": 2,
            "supporting_labels": 0,
            "reachable_target_labels": 1,
            "development_query_overlap": 0,
            "graph_build_issue_count": 0,
        },
    )
    monkeypatch.setattr(graph_eval, "corpus_sha256", lambda root: "corpus")
    monkeypatch.setattr(graph_eval, "_git_head", lambda root: "abc")
    monkeypatch.setattr(graph_eval, "_git_dirty", lambda root: True)
    frozen = freeze_cross_module_dataset(dataset, manifest, tmp_path, dev)
    assert frozen["status"] == "frozen_unscored"
    assert frozen["dataset_role"] == "cross_module_internal_validation"
    assert frozen["evaluation_profile_sha256"] == profile_sha256()
    assert frozen["policy"]["retrieval_not_run_before_freeze"] is True
    assert json.loads(manifest.read_text(encoding="utf-8")) == frozen


def test_freeze_and_check_accept_only_dedicated_filenames(monkeypatch, tmp_path):
    bad = tmp_path / "other.json"
    bad.write_text("[]", encoding="utf-8")
    dev = tmp_path / graph_eval.DEVELOPMENT_DATASET_NAME
    dev.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="accepts only"):
        freeze_cross_module_dataset(bad, tmp_path / MANIFEST_NAME, tmp_path, dev)
    with pytest.raises(ValueError, match="accepts only"):
        check_cross_module_manifest(bad, tmp_path / MANIFEST_NAME)


def test_manifest_check_rejects_dataset_or_profile_drift(monkeypatch, tmp_path):
    dataset = tmp_path / DATASET_NAME
    manifest = tmp_path / MANIFEST_NAME
    dataset.write_text("[]", encoding="utf-8")
    payload = {
        "dataset_role": "cross_module_internal_validation",
        "dataset_sha256": graph_eval.sha256_file(dataset),
        "evaluation_profile_sha256": profile_sha256(),
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert check_cross_module_manifest(dataset, manifest) == payload
    dataset.write_text("[{}]", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after freeze"):
        check_cross_module_manifest(dataset, manifest)
    dataset.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(graph_eval, "profile_sha256", lambda profile=None: "changed")
    with pytest.raises(ValueError, match="profile changed"):
        check_cross_module_manifest(dataset, manifest)


def test_augmentation_inserts_novel_target_after_five_baseline_hits():
    baseline = _baseline()
    target = _hit("b.py:1-2")
    augmented, context = augment_fixed_hits(
        baseline,
        [*baseline, target],
        _graph(),
        {hit.uid: 10 for hit in [*baseline, target]},
    )
    assert [hit.uid for hit in augmented[:7]] == [
        "a.py:3-4",
        "x1.py:1-2",
        "x2.py:1-2",
        "x3.py:1-2",
        "x4.py:1-2",
        "b.py:1-2",
        "x5.py:1-2",
    ]
    assert len(augmented) == 10
    assert augmented[5].metadata["graph_expanded"] is True
    assert augmented[5].metadata["graph_edge_kind"] == "calls"
    assert context.selected_token_count == 10


def test_augmentation_deduplicates_target_already_in_baseline():
    baseline = _baseline()
    baseline[8] = _hit("b.py:1-2")
    augmented, context = augment_fixed_hits(
        baseline,
        baseline,
        _graph(),
        {hit.uid: 10 for hit in baseline},
    )
    assert [hit.uid for hit in augmented].count("b.py:1-2") == 1
    assert context.selected == ()


def test_augmentation_obeys_token_budget_and_backfills_baseline():
    baseline = _baseline()
    target = _hit("b.py:1-2")
    costs = {hit.uid: 10 for hit in [*baseline, target]}
    costs[target.uid] = graph_eval.GRAPH_TOKEN_BUDGET + 1
    augmented, context = augment_fixed_hits(
        baseline, [*baseline, target], _graph(), costs
    )
    assert [hit.uid for hit in augmented] == [hit.uid for hit in baseline]
    assert context.omitted_for_budget_count == 1


def test_test_seed_and_target_are_not_used_for_graph_additions():
    sources = {
        "tests/test_a.py": "from b import target\n\ndef test_source():\n    target()\n",
        "b.py": SOURCES["b.py"],
    }
    graph = build_python_code_graph(sources)
    seed = _hit("tests/test_a.py:3-4")
    target = _hit("b.py:1-2")
    augmented, context = augment_fixed_hits(
        [seed], [seed, target], graph, {seed.uid: 1, target.uid: 1}
    )
    assert [hit.uid for hit in augmented] == [seed.uid]
    assert context.selected == ()


def test_non_python_and_document_targets_cannot_enter_graph_context():
    baseline = _baseline()
    document = _hit("README.md:1-2", content_type="document")
    augmented, _ = augment_fixed_hits(
        baseline,
        [*baseline, document],
        _graph(),
        {hit.uid: 1 for hit in [*baseline, document]},
    )
    assert document.uid not in {hit.uid for hit in augmented}


def _report():
    return {
        "overall": {
            "fixed_hybrid": {
                "recall_at_10": 0.5,
                "mrr_at_10": 0.4,
                "latency_ms": {"p95": 20.0},
            },
            "fixed_hybrid_plus_graph": {
                "recall_at_10": 0.7,
                "mrr_at_10": 0.5,
                "graph_overhead_ms": {"p95": 1.0},
            },
        },
        "paired_difference": {
            "recall_at_10": {
                "mean": 0.2,
                "ci95": [0.1, 0.3],
                "improved": 5,
                "degraded": 0,
                "tied": 15,
            }
        },
        "pollution": {
            "graph_added_count": 20,
            "graph_relevant_added_count": 15,
            "graph_irrelevant_added_count": 5,
            "irrelevant_additions_per_query_p95": 1.0,
            "test_additions": 0,
            "document_additions": 0,
        },
        "acceptance": {"passed": True},
    }


def test_markdown_is_metric_only_and_states_internal_limitations():
    markdown = render_markdown(_report())
    assert "0.700000" in markdown
    assert "通过" in markdown
    assert "内部编写" in markdown
    assert "不是外部泛化证据" in markdown
    assert "query" not in markdown.casefold()
    assert "a.py:3-4" not in markdown


def test_label_matching_accepts_exact_or_majority_overlap():
    hit = _hit("a.py:10-20")
    assert graph_eval._matches_label(hit, "a.py:10-20")
    assert graph_eval._matches_label(hit, "a.py:15-25")
    assert not graph_eval._matches_label(hit, "b.py:10-20")
    assert not graph_eval._matches_label(hit, "a.py:20-40")


def test_bootstrap_and_percentile_helpers_are_deterministic():
    first = graph_eval._bootstrap_ci([0.0, 0.5, 1.0], samples=100)
    second = graph_eval._bootstrap_ci([0.0, 0.5, 1.0], samples=100)
    assert first == second
    assert graph_eval._p95([3.0, 1.0, 2.0]) == 3.0
    assert graph_eval._p95([]) == 0.0


def test_tracked_source_loader_uses_git_list_not_recursive_workspace_scan(
    monkeypatch, tmp_path
):
    (tmp_path / "tracked.py").write_text("x = 1", encoding="utf-8")
    protected = tmp_path / "resume-output"
    protected.mkdir()
    (protected / "secret.py").write_text("secret = 1", encoding="utf-8")

    class Completed:
        stdout = "tracked.py\n"

    monkeypatch.setattr(graph_eval.subprocess, "run", lambda *args, **kwargs: Completed())
    assert graph_eval._tracked_python_sources(tmp_path) == {"tracked.py": "x = 1"}


def test_result_profile_contains_no_query_or_result_content():
    serialized = json.dumps(evaluation_profile(), sort_keys=True)
    assert "raw_query" not in serialized
    assert "top_results" not in serialized
    assert "document_text" not in serialized
    assert "source_text" not in serialized


def test_comparison_reports_paired_gain_pollution_and_acceptance(
    monkeypatch, tmp_path
):
    import config as config_module
    import rag.indexer as indexer_module
    import rag.retriever as retriever_module

    baseline = _baseline()
    target = _hit("b.py:1-2")
    available = [*baseline, target]
    settings = {
        "rag.adaptive_routing.enabled": False,
        "rag.reranker.enabled": False,
        "rag.include_docs": False,
    }
    monkeypatch.setattr(
        config_module.config, "get", lambda key, default=None: settings.get(key, default)
    )
    monkeypatch.setattr(indexer_module, "_get_collection", lambda project: object())
    monkeypatch.setattr(
        retriever_module,
        "_collection_documents",
        lambda collection, include_docs=False: available,
    )
    monkeypatch.setattr(
        retriever_module,
        "retrieve",
        lambda query, project, n, mode="hybrid": list(baseline),
    )
    monkeypatch.setattr(graph_eval, "_tracked_python_sources", lambda root: SOURCES)
    monkeypatch.setattr(
        graph_eval, "_token_costs", lambda hits: {hit.uid: 10 for hit in hits}
    )
    items = [
        _item(query="first", id="CGV1-C01"),
        _item(query="second", id="CGV1-C02"),
    ]
    report = graph_eval.compare_graph_retrieval(items, tmp_path)
    assert report["overall"]["fixed_hybrid"]["recall_at_10"] == 0.5
    assert report["overall"]["fixed_hybrid_plus_graph"]["recall_at_10"] == 1.0
    assert report["paired_difference"]["recall_at_10"]["mean"] == 0.5
    assert report["paired_difference"]["recall_at_10"]["improved"] == 2
    assert report["pollution"]["graph_relevant_added_count"] == 2
    assert report["pollution"]["graph_irrelevant_added_count"] == 0
    assert report["acceptance"]["passed"] is True
    serialized = json.dumps(report, sort_keys=True)
    assert "first" not in serialized
    assert "second" not in serialized
    assert "top_results" not in serialized
