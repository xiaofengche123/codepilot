"""Tests for deterministic ROUTE-003 retrieval confidence signals."""

from dataclasses import FrozenInstanceError, dataclass, fields
import json

import pytest

from rag.retrieval_confidence import (
    MAX_SIGNAL_TOP_K,
    RETRIEVAL_CONFIDENCE_SCHEMA_VERSION,
    RetrievalConfidenceSignals,
    calculate_retrieval_confidence,
)


@dataclass
class FakeHit:
    uid: str
    document: str = ""
    metadata: dict | None = None
    score: float = 0.0

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


def _hit(uid, *, file=None, document="", score=0.0):
    metadata = {} if file is None else {"file": file}
    return FakeHit(uid=uid, document=document, metadata=metadata, score=score)


def test_perfect_top_k_overlap_and_top1_agreement():
    vector = [_hit("a", score=0.9), _hit("b", score=0.7)]
    bm25 = [_hit("a"), _hit("b")]
    signals = calculate_retrieval_confidence("plain words", vector, bm25, top_k=2)
    assert signals.overlap_count == 2
    assert signals.overlap_ratio == 1.0
    assert signals.top1_agreement is True
    assert "top1_agreement" in signals.reason_codes


def test_overlap_uses_fixed_k_denominator():
    signals = calculate_retrieval_confidence(
        "plain words", [_hit("shared")], [_hit("shared")], top_k=10
    )
    assert signals.overlap_count == 1
    assert signals.overlap_ratio == 0.1


def test_top1_disagreement_and_no_overlap_are_explicit():
    signals = calculate_retrieval_confidence(
        "plain words", [_hit("vector")], [_hit("bm25")]
    )
    assert signals.top1_agreement is False
    assert signals.overlap_ratio == 0.0
    assert "top1_disagreement" in signals.reason_codes
    assert "no_ranking_overlap" in signals.reason_codes


@pytest.mark.parametrize(
    ("vector", "bm25", "missing_code"),
    [([], [_hit("b")], "vector_results_missing"),
     ([_hit("v")], [], "bm25_results_missing")],
)
def test_one_missing_ranking_has_unknown_top1(vector, bm25, missing_code):
    signals = calculate_retrieval_confidence("plain words", vector, bm25)
    assert signals.top1_agreement is None
    assert missing_code in signals.reason_codes


def test_both_empty_rankings_have_defined_zero_signals():
    signals = calculate_retrieval_confidence("AgentSession.run", [], [])
    assert signals.candidate_count == 0
    assert signals.overlap_ratio == 0.0
    assert signals.identifier_coverage == 0.0
    assert signals.file_diversity_ratio == 0.0
    assert signals.vector_top_score_margin is None
    assert "no_candidates" in signals.reason_codes
    assert "vector_results_missing" in signals.reason_codes
    assert "bm25_results_missing" in signals.reason_codes


def test_identifier_coverage_is_computed_over_union_candidates():
    vector = [_hit("v", document="class AgentSession: pass")]
    bm25 = [_hit("b", document="def run(self): pass")]
    signals = calculate_retrieval_confidence("AgentSession.run", vector, bm25)
    assert signals.query_identifier_count == 2
    assert signals.matched_identifier_count == 2
    assert signals.identifier_coverage == 1.0
    assert "identifiers_fully_covered" in signals.reason_codes


def test_partial_and_missing_identifier_coverage_are_distinct():
    partial = calculate_retrieval_confidence(
        "AgentSession.run", [_hit("a", document="AgentSession")], []
    )
    missing = calculate_retrieval_confidence(
        "AgentSession.run", [_hit("a", document="unrelated")], []
    )
    assert partial.identifier_coverage == 0.5
    assert "identifiers_partially_covered" in partial.reason_codes
    assert missing.identifier_coverage == 0.0
    assert "identifiers_not_covered" in missing.reason_codes


def test_natural_language_query_has_no_identifier_denominator():
    signals = calculate_retrieval_confidence(
        "where is authentication handled", [_hit("a", document="authentication")], []
    )
    assert signals.query_identifier_count == 0
    assert signals.identifier_coverage == 0.0
    assert "query_identifiers_absent" in signals.reason_codes


def test_file_path_identifiers_can_match_candidate_file_metadata():
    signals = calculate_retrieval_confidence(
        "rag/retriever.py",
        [_hit("a", file=r"rag\retriever.py")],
        [],
    )
    assert signals.identifier_coverage == 1.0


def test_vector_margin_is_raw_higher_is_better_score_gap():
    signals = calculate_retrieval_confidence(
        "plain words",
        [_hit("a", score=4.5), _hit("b", score=1.0)],
        [],
    )
    assert signals.vector_top_score_margin == 3.5
    assert signals.vector_top_score_margin > 1.0
    assert "vector_margin_available" in signals.reason_codes


def test_vector_tie_has_zero_available_margin():
    signals = calculate_retrieval_confidence(
        "plain words", [_hit("a", score=0.5), _hit("b", score=0.5)], []
    )
    assert signals.vector_top_score_margin == 0.0
    assert "vector_margin_available" in signals.reason_codes


def test_inconsistent_vector_score_order_is_explicit_and_clamped():
    signals = calculate_retrieval_confidence(
        "plain words", [_hit("a", score=0.1), _hit("b", score=0.9)], []
    )
    assert signals.vector_top_score_margin == 0.0
    assert "vector_score_order_inconsistent" in signals.reason_codes


@pytest.mark.parametrize(
    "score",
    [
        float("nan"),
        float("inf"),
        "bad",
        True,
        pytest.param(10**10_000, id="overflowing_integer"),
    ],
)
def test_invalid_vector_scores_make_margin_unavailable(score):
    signals = calculate_retrieval_confidence(
        "plain words", [_hit("a", score=score), _hit("b", score=0.1)], []
    )
    assert signals.vector_top_score_margin is None
    assert "vector_margin_unavailable" in signals.reason_codes


def test_single_vector_result_has_unavailable_margin():
    signals = calculate_retrieval_confidence("plain words", [_hit("a")], [])
    assert signals.vector_top_score_margin is None


def test_file_diversity_uses_unique_files_over_unique_candidates():
    vector = [
        _hit("a", file="auth.py"),
        _hit("b", file="auth.py"),
        _hit("c", file="user.py"),
    ]
    signals = calculate_retrieval_confidence("plain words", vector, [], top_k=3)
    assert signals.candidate_count == 3
    assert signals.candidates_with_file_count == 3
    assert signals.unique_file_count == 2
    assert signals.file_diversity_ratio == pytest.approx(2 / 3, abs=1e-6)
    assert "multiple_candidate_files" in signals.reason_codes


def test_file_paths_are_normalized_and_missing_metadata_is_counted():
    vector = [
        _hit("a", file=r"rag\retriever.py"),
        _hit("b", file="rag/retriever.py"),
        _hit("c"),
    ]
    signals = calculate_retrieval_confidence("plain words", vector, [], top_k=3)
    assert signals.candidates_with_file_count == 2
    assert signals.unique_file_count == 1
    assert signals.file_diversity_ratio == pytest.approx(1 / 3, abs=1e-6)
    assert "candidate_file_missing" in signals.reason_codes


def test_duplicate_uids_are_deduplicated_without_mutating_inputs():
    duplicate = _hit("A", file="one.py")
    vector = [duplicate, _hit("a", file="two.py")]
    before = list(vector)
    signals = calculate_retrieval_confidence("plain words", vector, [])
    assert signals.vector_result_count == 1
    assert signals.candidate_count == 1
    assert vector == before


@pytest.mark.parametrize("top_k", [0, -1, MAX_SIGNAL_TOP_K + 1])
def test_top_k_is_bounded(top_k):
    with pytest.raises(ValueError, match="top_k"):
        calculate_retrieval_confidence("q", [], [], top_k=top_k)


@pytest.mark.parametrize("top_k", [True, 10.0, "10"])
def test_top_k_requires_an_integer(top_k):
    with pytest.raises(TypeError, match="top_k"):
        calculate_retrieval_confidence("q", [], [], top_k=top_k)


@pytest.mark.parametrize("value", [None, "hits", iter(())])
def test_rankings_must_be_sequences(value):
    with pytest.raises(TypeError, match="must be a sequence"):
        calculate_retrieval_confidence("q", value, [])


@pytest.mark.parametrize("uid", [None, "", 42])
def test_hit_uid_must_be_a_non_empty_string(uid):
    with pytest.raises(TypeError, match="uid"):
        calculate_retrieval_confidence("q", [_hit(uid)], [])


def test_signal_structure_is_frozen_json_ready_and_query_free():
    signals = calculate_retrieval_confidence("AgentSession.run", [], [])
    payload = json.loads(json.dumps(signals.to_dict()))
    names = {field.name for field in fields(RetrievalConfidenceSignals)}
    assert payload["query_identifier_count"] == 2
    assert payload["schema_version"] == RETRIEVAL_CONFIDENCE_SCHEMA_VERSION
    assert "query" not in names
    assert "identifiers" not in names
    assert "hits" not in names
    with pytest.raises(FrozenInstanceError):
        signals.top_k = 20


def test_repeated_calculation_is_deterministic():
    vector = [_hit("a", file="a.py", document="AgentSession", score=0.9)]
    args = ("AgentSession.run", vector, [])
    assert calculate_retrieval_confidence(*args) == calculate_retrieval_confidence(*args)


def test_candidate_text_scanning_is_bounded():
    huge = "x" * 100_000 + " AgentSession"
    signals = calculate_retrieval_confidence(
        "AgentSession", [_hit("a", document=huge)], []
    )
    assert signals.identifier_coverage == 0.0


def test_query_identifier_analysis_has_a_stable_count_bound():
    query = " ".join(f"identifier_{index}" for index in range(300))
    signals = calculate_retrieval_confidence(query, [], [])
    assert signals.query_identifier_count == 256
    assert signals.matched_identifier_count == 0


def test_hits_beyond_top_k_are_not_inspected():
    valid = _hit("valid")
    invalid_beyond_limit = _hit(None)
    signals = calculate_retrieval_confidence(
        "plain words", [valid, invalid_beyond_limit], [], top_k=1
    )
    assert signals.vector_result_count == 1


def test_calculation_does_not_import_models_retriever_or_plan(monkeypatch):
    import builtins

    original_import = builtins.__import__
    forbidden = {
        "chromadb",
        "sentence_transformers",
        "rag.indexer",
        "rag.reranker",
        "rag.retriever",
        "rag.retrieval_plan",
    }

    def guarded_import(name, *args, **kwargs):
        if name in forbidden or any(name.startswith(item + ".") for item in forbidden):
            raise AssertionError(f"unexpected runtime import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert calculate_retrieval_confidence("plain words", [], []).top_k == 10
