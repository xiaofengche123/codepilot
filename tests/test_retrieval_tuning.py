"""Development-only adaptive retrieval tuning tests for ROUTE-006."""

import json

import pytest

from rag.retrieval_tuning import (
    DevelopmentRankingCase,
    FusionSetting,
    candidate_settings,
    evaluate_family_profile,
    load_development_items,
    tune_family_settings,
)
from rag.retriever import SearchHit


def _hit(uid):
    file = uid.split(":", 1)[0]
    return SearchHit(uid, "", {"file": file, "start_line": 1, "end_line": 10})


def _case(family="query_mixed_language"):
    return DevelopmentRankingCase(
        case_id="dev-1",
        family=family,
        relevant=("target.py",),
        query="development query",
        vector_hits=(_hit("target.py:1-10"), _hit("other.py:1-10")),
        bm25_hits=(_hit("other.py:1-10"), _hit("target.py:1-10")),
    )


def test_loader_accepts_designated_development_filename(tmp_path):
    path = tmp_path / "codepilot-dev.json"
    path.write_text(json.dumps([{"id": "d1"}]), encoding="utf-8")
    assert load_development_items(path) == [{"id": "d1"}]


@pytest.mark.parametrize(
    "name",
    ["codepilot-test-v1.json", "agent-tasks-v1.json", "formal-results.json"],
)
def test_loader_rejects_non_development_artifacts_before_reading(tmp_path, name):
    with pytest.raises(ValueError, match="accepts only codepilot-dev.json"):
        load_development_items(tmp_path / name)


def test_loader_rejects_empty_or_non_array_development_data(tmp_path):
    path = tmp_path / "codepilot-dev.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        load_development_items(path)


def test_search_space_is_bounded_and_stable():
    settings = candidate_settings()
    assert len(settings) == 54
    assert len(set(settings)) == 54
    assert {setting.rrf_k for setting in settings} == {10, 30, 60}
    assert {setting.candidate_count for setting in settings} == {30, 40, 50}


@pytest.mark.parametrize(
    "args",
    [
        (True, 1, 10, 30),
        (1, "1", 10, 30),
        (float("nan"), 1, 10, 30),
        (10**1000, 1, 10, 30),
        (-1, 1, 10, 30),
        (0, 0, 10, 30),
        (1, 1, True, 30),
        (1, 1, 0, 30),
        (1, 1, 10, 101),
    ],
)
def test_fusion_setting_rejects_invalid_values(args):
    exception = TypeError if any(isinstance(value, (bool, str)) for value in args) else ValueError
    with pytest.raises(exception):
        FusionSetting(*args)


def test_tuning_prefers_recall_then_mrr_and_lower_cost_deterministically():
    settings = (
        FusionSetting(2.0, 0.25, 10, 30),
        FusionSetting(0.25, 2.0, 10, 30),
        FusionSetting(0.25, 2.0, 60, 50),
    )
    first = tune_family_settings([_case()], settings=settings)
    second = tune_family_settings([_case()], settings=settings)
    assert first == second
    assert first[0].setting == settings[1]
    assert first[0].recall_at_k == 1.0
    assert first[0].mrr_at_k == 1.0


def test_tuning_groups_query_families_independently():
    scores = tune_family_settings(
        [_case("query_mixed_language"), _case("ranking_disagreement")],
        settings=[FusionSetting(1.0, 1.0, 10, 30)],
    )
    assert [score.family for score in scores] == [
        "query_mixed_language",
        "ranking_disagreement",
    ]
    assert all(score.query_count == 1 for score in scores)


def test_profile_evaluation_returns_aggregate_metrics_only():
    setting = FusionSetting(0.25, 2.0, 10, 30)
    result = evaluate_family_profile(
        [_case()], {"query_mixed_language": setting}
    )
    assert result == {"query_count": 1, "recall_at_k": 1.0, "mrr_at_k": 1.0}
    assert "query" not in result


def test_profile_evaluation_requires_every_routed_family():
    with pytest.raises(ValueError, match="missing setting"):
        evaluate_family_profile([_case()], {})


@pytest.mark.parametrize("k", [True, 0, -1])
def test_tuning_validates_k(k):
    exception = TypeError if k is True else ValueError
    with pytest.raises(exception):
        tune_family_settings([_case()], k=k)


def test_tuning_rejects_empty_or_invalid_cases():
    with pytest.raises(ValueError):
        tune_family_settings([])
    with pytest.raises(TypeError):
        tune_family_settings([object()])


def test_case_repr_does_not_copy_query_or_rankings():
    case = _case()
    rendered = repr(case)
    assert "development query" not in rendered
    assert "target.py" in rendered  # relevance labels are expected tuning data
    assert "vector_hits" not in rendered


def test_import_does_not_load_embedding_or_reranker():
    import rag.retrieval_tuning as module

    assert "CrossEncoderReranker" not in module.__dict__
    assert "_get_model" not in module.__dict__
