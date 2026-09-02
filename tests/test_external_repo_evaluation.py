import json
from pathlib import Path

import pytest

from rag import external_repo_evaluation as external


def test_frozen_matrix_has_three_repositories_and_25_percent_audit():
    assert set(external.REPOSITORIES) == {"itsdangerous", "markupsafe", "click"}
    assert len(external.AUDIT_IDS) == 3


def test_validate_rejects_wrong_size(tmp_path, monkeypatch):
    monkeypatch.setattr(external, "_git_head", lambda root: "x")
    with pytest.raises(ValueError, match="12 unique"):
        external.validate([], tmp_path)


def test_dataset_has_unique_ids_and_four_items_per_repository():
    root = Path(__file__).parents[1]
    data = json.loads((root / ".rag-eval/external-repo-v1.json").read_text(encoding="utf-8"))
    assert len({item["id"] for item in data}) == 12
    assert {name: sum(item["repository"] == name for item in data)
            for name in external.REPOSITORIES} == {name: 4 for name in external.REPOSITORIES}
    assert set(external.AUDIT_IDS).issubset({item["id"] for item in data})
    assert len(external.AUDIT_IDS) / len(data) >= 0.2
