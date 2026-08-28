"""Independent validation-set sealing tests for ROUTE-007."""

import json

import pytest

from rag import retrieval_validation


def _items(label="sample.py:1-2"):
    prefixes = {
        "identifier": "I",
        "natural_language": "N",
        "bug_symptom": "B",
        "cross_module": "C",
        "mixed_language": "M",
    }
    return [
        {
            "id": f"CPV1-{prefixes[category]}{index:02d}",
            "category": category,
            "query": f"independent {category} query {index}",
            "required": [label],
            "supporting": [],
        }
        for category in sorted(prefixes)
        for index in range(1, 11)
    ]


def test_independent_items_are_balanced_and_do_not_overlap_dev(tmp_path):
    (tmp_path / "sample.py").write_text(
        "def target():\n    return 1\n", encoding="utf-8"
    )
    summary = retrieval_validation.validate_independent_items(
        _items(), tmp_path, [{"query": "development only"}]
    )
    assert summary["query_count"] == 50
    assert summary["development_query_overlap"] == 0


def test_independent_items_reject_normalized_dev_overlap_before_freeze(tmp_path):
    items = _items()
    with pytest.raises(ValueError, match="规范化 query 重复"):
        retrieval_validation.validate_independent_items(
            items, tmp_path, [{"query": "  INDEPENDENT   identifier QUERY 1 "}]
        )


def test_independent_items_reject_internal_normalized_duplicates(tmp_path):
    items = _items()
    items[1]["query"] = "  INDEPENDENT   bug_symptom QUERY 1 "
    with pytest.raises(ValueError, match="规范化后重复"):
        retrieval_validation.validate_independent_items(items, tmp_path, [])


def test_independent_items_reject_category_id_mismatch(tmp_path):
    items = _items()
    items[0]["id"] = "CPV1-N99"
    with pytest.raises(ValueError, match="id/category"):
        retrieval_validation.validate_independent_items(items, tmp_path, [])


@pytest.mark.parametrize(
    "name", ["codepilot-dev.json", "codepilot-test-v1.json", "formal-results.json"]
)
def test_freezer_rejects_non_validation_artifact_before_reading(tmp_path, name):
    with pytest.raises(ValueError, match="accepts only codepilot-validation-v1.json"):
        retrieval_validation.freeze_validation_dataset(
            tmp_path / name,
            tmp_path,
            tmp_path / "manifest.json",
            tmp_path / "codepilot-dev.json",
        )


def test_manifest_check_rejects_dataset_change(tmp_path):
    dataset = tmp_path / "codepilot-validation-v1.json"
    dataset.write_text("[]", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "dataset_role": "independent_validation",
                "dataset_sha256": retrieval_validation.sha256_file(dataset),
                "router_profile_sha256": retrieval_validation.profile_sha256(),
            }
        ),
        encoding="utf-8",
    )
    dataset.write_text("[{}]", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        retrieval_validation.check_validation_manifest(dataset, manifest)


def test_manifest_check_rejects_router_retuning(tmp_path, monkeypatch):
    dataset = tmp_path / "codepilot-validation-v1.json"
    dataset.write_text("[]", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "dataset_role": "independent_validation",
                "dataset_sha256": retrieval_validation.sha256_file(dataset),
                "router_profile_sha256": retrieval_validation.profile_sha256(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        retrieval_validation, "router_profile", lambda: {"retuned": True}
    )
    with pytest.raises(ValueError, match="路由参数已变化"):
        retrieval_validation.check_validation_manifest(dataset, manifest)


def test_import_has_no_retrieval_or_evaluation_entry_points():
    assert "retrieve" not in retrieval_validation.__dict__
    assert "evaluate_dataset" not in retrieval_validation.__dict__
