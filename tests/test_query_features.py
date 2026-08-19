"""Deterministic, model-free query feature extraction tests."""

from dataclasses import FrozenInstanceError, asdict, fields
import json

import pytest

from rag.query_features import (
    MAX_ANALYZED_CHARS,
    QueryFeatures,
    extract_query_features,
)


@pytest.mark.parametrize("query", ["", "   \t\r\n"])
def test_empty_and_whitespace_queries_have_defined_zero_ratios(query):
    features = extract_query_features(query)
    assert features.token_count == 0
    assert features.identifier_ratio == 0.0
    assert features.natural_language_ratio == 0.0
    assert features.chinese_character_ratio == 0.0
    assert features.english_character_ratio == 0.0
    assert features.length_bucket == "empty"
    assert "empty_query" in features.reason_codes


def test_python_and_dotted_identifiers_are_code_like():
    symbol = extract_query_features("AgentSession.run")
    assert symbol.identifier_ratio == 1.0
    assert symbol.natural_language_ratio == 0.0
    assert "code_identifier" in symbol.reason_codes


@pytest.mark.parametrize(
    "query",
    ["rag/retriever.py", r"D:\codepilot\rag\retriever.py"],
)
def test_posix_and_windows_paths_are_detected(query):
    features = extract_query_features(query)
    assert features.contains_path is True
    assert features.identifier_ratio > 0.0
    assert "file_path" in features.reason_codes


def test_file_and_line_reference_is_both_path_and_stack_trace():
    features = extract_query_features('File "agent.py", line 42, in run')
    assert features.contains_path is True
    assert features.contains_stack_trace is True


def test_configuration_dotted_key_is_distinct_from_regular_dotted_symbol():
    config_key = extract_query_features("config.rag.reranker.enabled")
    symbol = extract_query_features("AgentSession.run")
    assert config_key.contains_config_key is True
    assert symbol.contains_config_key is False
    assert "config_key" in config_key.reason_codes


def test_python_traceback_and_exception_text_are_detected():
    traceback = extract_query_features(
        'Traceback (most recent call last):\n'
        '  File "agent.py", line 42, in run\n'
        "ModuleNotFoundError: No module named chromadb"
    )
    assert traceback.contains_stack_trace is True
    assert traceback.contains_error_text is True


@pytest.mark.parametrize(
    "query",
    [
        "ModuleNotFoundError: No module named chromadb",
        "authentication failed with an error",
        "登录时报错，认证失败",
    ],
)
def test_common_exception_names_and_error_words_are_detected(query):
    assert extract_query_features(query).contains_error_text is True


def test_chinese_natural_language_query():
    features = extract_query_features("登录认证逻辑在哪里")
    assert features.chinese_character_ratio == 1.0
    assert features.english_character_ratio == 0.0
    assert features.natural_language_ratio > 0.0
    assert features.is_mixed_language is False


def test_english_natural_language_query():
    features = extract_query_features("where is authentication handled")
    assert features.english_character_ratio == 1.0
    assert features.chinese_character_ratio == 0.0
    assert features.natural_language_ratio == 1.0
    assert features.is_mixed_language is False


def test_mixed_chinese_english_query():
    features = extract_query_features("登录流程 authentication handler")
    assert 0.0 < features.chinese_character_ratio < 1.0
    assert 0.0 < features.english_character_ratio < 1.0
    assert features.is_mixed_language is True
    assert "mixed_language" in features.reason_codes


def test_snake_case_camel_case_class_and_function_call_are_identifiers():
    features = extract_query_features(
        "task_queue camelCase AgentSession run_handler()"
    )
    assert features.identifier_ratio == 1.0
    assert features.natural_language_ratio == 0.0


@pytest.mark.parametrize(
    "query",
    [
        "查找 server 到 task_queue 的调用关系",
        "compare agent.py and execution_state.py",
        "show the dependency between auth.handler and user.service",
    ],
)
def test_cross_module_intent_uses_explainable_rules(query):
    features = extract_query_features(query)
    assert features.has_cross_module_intent is True
    assert "cross_module_intent" in features.reason_codes


def test_single_symbol_is_not_mislabeled_as_cross_module():
    assert extract_query_features("AgentSession.run").has_cross_module_intent is False
    assert extract_query_features("rag/retriever.py").has_cross_module_intent is False


def test_class_method_chain_is_not_a_configuration_key():
    assert extract_query_features("Package.Service.Handler").contains_config_key is False


def test_very_long_input_is_bounded_but_reports_exact_length():
    query = "authentication " * (MAX_ANALYZED_CHARS * 2)
    features = extract_query_features(query)
    assert features.query_length == len(query)
    assert features.analyzed_length == MAX_ANALYZED_CHARS
    assert features.length_bucket == "very_long"
    assert "analysis_truncated" in features.reason_codes
    assert len(features.reason_codes) <= 14


def test_unicode_and_punctuation_are_safe_and_serializable():
    features = extract_query_features("🔐 café—登录？！ Δοκιμή")
    payload = json.loads(json.dumps(asdict(features), ensure_ascii=False))
    assert payload["query_length"] == len("🔐 café—登录？！ Δοκιμή")
    assert payload["token_count"] > 0


@pytest.mark.parametrize(
    "query",
    [
        "",
        "AgentSession.run",
        "登录流程 authentication handler",
        r"D:\repo\app.py",
        "Traceback (most recent call last):",
        "!@#$%^&*()",
    ],
)
def test_all_ratios_remain_in_unit_interval(query):
    features = extract_query_features(query)
    ratios = (
        features.identifier_ratio,
        features.natural_language_ratio,
        features.chinese_character_ratio,
        features.english_character_ratio,
    )
    assert all(0.0 <= ratio <= 1.0 for ratio in ratios)


def test_data_structure_is_immutable_and_does_not_store_original_query():
    features = extract_query_features("private query AgentSession.run")
    field_names = {field.name for field in fields(QueryFeatures)}
    assert "query" not in field_names
    assert "raw_query" not in field_names
    assert "private query" not in repr(features)
    with pytest.raises(FrozenInstanceError):
        features.token_count = 99


def test_repeated_calls_are_equal():
    query = "compare agent.py and execution_state.py"
    assert extract_query_features(query) == extract_query_features(query)


def test_extraction_does_not_import_or_load_retrieval_models(monkeypatch):
    import builtins

    original_import = builtins.__import__
    forbidden = {
        "chromadb",
        "sentence_transformers",
        "rag.indexer",
        "rag.reranker",
        "rag.retriever",
    }

    def guarded_import(name, *args, **kwargs):
        if name in forbidden or any(name.startswith(item + ".") for item in forbidden):
            raise AssertionError(f"unexpected model/retrieval import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert extract_query_features("where is authentication handled").token_count > 0


def test_non_string_rejected_without_echoing_value():
    with pytest.raises(TypeError, match="query must be a string") as exc_info:
        extract_query_features(["secret"])
    assert "secret" not in str(exc_info.value)
