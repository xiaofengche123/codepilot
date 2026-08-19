"""Deterministic, model-free features for retrieval queries.

The values in :class:`QueryFeatures` are lexical heuristics, not semantic truth.
Extraction is deliberately bounded and does not retain the original query so the
result can be logged or serialized without copying arbitrary user text.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


MAX_ANALYZED_CHARS = 16_384
MAX_EXTRACTED_IDENTIFIERS = 256

_TOKEN_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*|\d+|[\u3400-\u4dbf\u4e00-\u9fff]+|[^\W\d_]+",
    re.UNICODE,
)
_CHINESE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_ENGLISH_RE = re.compile(r"[A-Za-z]")
_DOTTED_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*)+"
)
_CONFIG_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?:config|settings?|options?|rag)\.[a-z][a-z0-9_-]*"
    r"|[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*){2,}"
    r")(?![A-Za-z0-9_])",
)
_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"[A-Za-z]:[\\/](?:[A-Za-z0-9_.-]+[\\/])*[A-Za-z0-9_.-]+"
    r"|[\\/]{1,2}(?:[A-Za-z0-9_.-]+[\\/])*[A-Za-z0-9_.-]+"
    r"|(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+"
    r")(?![A-Za-z0-9_])"
)
_FILE_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_.-]*\."
    r"(?:py|pyi|js|jsx|ts|tsx|java|go|rs|c|cc|cpp|h|hpp|"
    r"yaml|yml|json|toml|ini|cfg|md|txt|xml|sql|sh)\b",
    re.IGNORECASE,
)
_FUNCTION_CALL_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\s*(?=\()")
_OBVIOUS_IDENTIFIER_RE = re.compile(
    r"(?:^[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+$"
    r"|^[a-z]+(?:[A-Z][A-Za-z0-9]*)+$"
    r"|^[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)*$"
    r"|^[A-Z][A-Z0-9_]{1,}$)"
)
_ERROR_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning)\b"
    r"|\b(?:error|exception|failed|failure|fatal|traceback)\b"
    r"|(?:错误|异常|报错|失败|错误码)",
    re.IGNORECASE,
)
_PYTHON_FRAME_RE = re.compile(
    r"(?m)^\s*File\s+[\"'][^\"'\r\n]{1,500}[\"'],\s+line\s+\d+"
    r"(?:,\s+in\s+\S+)?\s*$"
)
_JAVA_FRAME_RE = re.compile(
    r"(?m)^\s*at\s+[A-Za-z0-9_.$<>]+\([^()\r\n]{1,200}:\d+\)\s*$"
)
_TRACEBACK_HEADER_RE = re.compile(
    r"(?m)^\s*Traceback \(most recent call last\):\s*$",
    re.IGNORECASE,
)
_CROSS_MODULE_PHRASE_RE = re.compile(
    r"(?:调用关系|调用链|跨模块|依赖关系?|模块间|"
    r"从[^\r\n]{1,80}(?:到|至)[^\r\n]{1,80}|"
    r"\bacross\s+modules?\b|\bcall\s+(?:chain|graph|relationship)\b|"
    r"\bdependenc(?:y|ies)\b|\bfrom\s+\S[^\r\n]{0,60}\s+to\s+\S|"
    r"\bcompare\b[^\r\n]{1,80}\b(?:and|with|to)\b)",
    re.IGNORECASE,
)
_RELATION_WORD_RE = re.compile(
    r"(?:调用|依赖|关系|关联|比较|连接|流向)"
    r"|\b(?:call|calls|called|depend|depends|related|relationship|"
    r"compare|connect|flow)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QueryFeatures:
    """A serializable snapshot of bounded lexical query characteristics.

    Ratios are calculated over the analyzed prefix. ``identifier_ratio`` and
    ``natural_language_ratio`` use lexical tokens as their denominator;
    Chinese and English ratios use Chinese-plus-ASCII-letter characters.
    Empty denominators produce ``0.0``.
    """

    query_length: int
    analyzed_length: int
    length_bucket: str
    token_count: int
    identifier_ratio: float
    natural_language_ratio: float
    chinese_character_ratio: float
    english_character_ratio: float
    is_mixed_language: bool
    contains_path: bool
    contains_config_key: bool
    contains_error_text: bool
    contains_stack_trace: bool
    has_cross_module_intent: bool
    reason_codes: tuple[str, ...]


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return min(1.0, max(0.0, round(numerator / denominator, 6)))


def _length_bucket(content_length: int) -> str:
    if content_length == 0:
        return "empty"
    if content_length <= 32:
        return "short"
    if content_length <= 128:
        return "medium"
    if content_length <= 512:
        return "long"
    return "very_long"


def _inside_any(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(span_start <= start and end <= span_end for span_start, span_end in spans)


def extract_query_identifiers(query: str) -> tuple[str, ...]:
    """Return bounded, normalized code identifiers for transient scoring.

    The original query is not retained.  Results are deduplicated in encounter
    order and capped so downstream confidence calculation has a stable memory
    bound.  Callers should use the identifiers transiently and avoid logging them.
    """
    if not isinstance(query, str):
        raise TypeError("query must be a string")

    text = query[:MAX_ANALYZED_CHARS]
    code_spans = [
        match.span()
        for pattern in (
            _PATH_RE,
            _FILE_REFERENCE_RE,
            _DOTTED_IDENTIFIER_RE,
            _FUNCTION_CALL_RE,
        )
        for match in pattern.finditer(text)
    ]
    identifiers: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_RE.finditer(text):
        value = token.group(0)
        if not (
            _inside_any(token.start(), token.end(), code_spans)
            or _OBVIOUS_IDENTIFIER_RE.fullmatch(value)
        ):
            continue
        normalized = value.casefold()
        if normalized in seen:
            continue
        identifiers.append(normalized)
        seen.add(normalized)
        if len(identifiers) >= MAX_EXTRACTED_IDENTIFIERS:
            break
    return tuple(identifiers)


def extract_query_features(query: str) -> QueryFeatures:
    """Extract deterministic lexical features without I/O, models, or mutation.

    Only the first :data:`MAX_ANALYZED_CHARS` code points are inspected.  This
    bounds all regex work for adversarial or accidentally huge inputs; the exact
    original length is still reported, while the original text is never stored.
    """
    if not isinstance(query, str):
        raise TypeError("query must be a string")

    query_length = len(query)
    text = query[:MAX_ANALYZED_CHARS]
    stripped_length = len(text.strip())
    bucket = _length_bucket(stripped_length)

    tokens = list(_TOKEN_RE.finditer(text))
    path_matches = list(_PATH_RE.finditer(text))
    path_spans = [match.span() for match in path_matches]
    file_matches = list(_FILE_REFERENCE_RE.finditer(text))
    dotted_matches = list(_DOTTED_IDENTIFIER_RE.finditer(text))
    function_matches = list(_FUNCTION_CALL_RE.finditer(text))
    code_spans = [
        match.span()
        for match in (*path_matches, *file_matches, *dotted_matches, *function_matches)
    ]

    identifier_tokens = 0
    natural_language_tokens = 0
    code_references: set[str] = set()
    for token in tokens:
        value = token.group(0)
        is_identifier = (
            _inside_any(token.start(), token.end(), code_spans)
            or bool(_OBVIOUS_IDENTIFIER_RE.fullmatch(value))
        )
        if is_identifier:
            identifier_tokens += 1
            code_references.add(value.casefold())
        elif any(character.isalpha() for character in value):
            natural_language_tokens += 1

    chinese_count = len(_CHINESE_RE.findall(text))
    english_count = len(_ENGLISH_RE.findall(text))
    language_characters = chinese_count + english_count
    mixed_language = chinese_count > 0 and english_count > 0

    contains_path = bool(path_matches or file_matches)
    contains_config_key = bool(_CONFIG_KEY_RE.search(text))
    contains_error_text = bool(_ERROR_RE.search(text))
    contains_stack_trace = bool(
        _TRACEBACK_HEADER_RE.search(text)
        or _PYTHON_FRAME_RE.search(text)
        or _JAVA_FRAME_RE.search(text)
    )

    # A file name inside a path is the same reference, not a second module.
    # Keeping only standalone file matches avoids treating ``rag/retriever.py``
    # as both ``rag/retriever.py`` and ``retriever.py``.
    standalone_file_matches = [
        match
        for match in file_matches
        if not _inside_any(match.start(), match.end(), path_spans)
    ]
    file_references = {
        match.group(0).replace("\\", "/").casefold()
        for match in (*path_matches, *standalone_file_matches)
    }
    has_relation_phrase = bool(_CROSS_MODULE_PHRASE_RE.search(text))
    has_cross_module_intent = (
        has_relation_phrase
        or len(file_references) >= 2
        or (
            len(code_references) >= 2
            and bool(_RELATION_WORD_RE.search(text))
        )
    )

    identifier_ratio = _ratio(identifier_tokens, len(tokens))
    natural_language_ratio = _ratio(natural_language_tokens, len(tokens))
    chinese_ratio = _ratio(chinese_count, language_characters)
    english_ratio = _ratio(english_count, language_characters)

    reasons = [f"length_{bucket}"]
    if query_length > MAX_ANALYZED_CHARS:
        reasons.append("analysis_truncated")
    if not query.strip():
        reasons.append("empty_query")
    elif not tokens:
        reasons.append("no_lexical_tokens")
    if identifier_tokens:
        reasons.append("code_identifier")
    if natural_language_tokens:
        reasons.append("natural_language")
    if chinese_count:
        reasons.append("chinese_text")
    if english_count:
        reasons.append("english_text")
    if mixed_language:
        reasons.append("mixed_language")
    if contains_path:
        reasons.append("file_path")
    if contains_config_key:
        reasons.append("config_key")
    if contains_error_text:
        reasons.append("error_text")
    if contains_stack_trace:
        reasons.append("stack_trace")
    if has_cross_module_intent:
        reasons.append("cross_module_intent")

    return QueryFeatures(
        query_length=query_length,
        analyzed_length=len(text),
        length_bucket=bucket,
        token_count=len(tokens),
        identifier_ratio=identifier_ratio,
        natural_language_ratio=natural_language_ratio,
        chinese_character_ratio=chinese_ratio,
        english_character_ratio=english_ratio,
        is_mixed_language=mixed_language,
        contains_path=contains_path,
        contains_config_key=contains_config_key,
        contains_error_text=contains_error_text,
        contains_stack_trace=contains_stack_trace,
        has_cross_module_intent=has_cross_module_intent,
        reason_codes=tuple(reasons),
    )
