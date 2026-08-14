"""Metrics helpers for CodePilot end-to-end Agent edit evaluation."""

from __future__ import annotations

import json
import posixpath
from collections import Counter
from pathlib import Path
from typing import Any


TRANSACTIONAL_EDIT_TOOL = "edit_file_transaction"
LEGACY_EDIT_TOOL = "write_file"
EDIT_TOOLS = frozenset({TRANSACTIONAL_EDIT_TOOL, LEGACY_EDIT_TOOL})
WRITE_FAILURE_CODES = frozenset(
    {"write_failed", "write_verification_failed", "rollback_failed", "internal_error"}
)
_STRUCTURED_RESULT_FIELDS = (
    "success",
    "path",
    "before_sha256",
    "after_sha256",
    "replacements",
    "rolled_back",
    "error_code",
    "message",
)


def record_tool_call(
    calls: list[dict[str, Any]],
    name: str,
    arguments: Any,
    result: Any,
    preview_chars: int = 300,
) -> None:
    """Append an evaluation-safe tool record without storing an unbounded result."""
    record: dict[str, Any] = {
        "name": name,
        "arguments": arguments if isinstance(arguments, dict) else {},
        "result_preview": str(result)[:preview_chars],
    }
    if name == TRANSACTIONAL_EDIT_TOOL:
        try:
            decoded = json.loads(result) if isinstance(result, str) else result
        except (TypeError, json.JSONDecodeError):
            decoded = None
        if isinstance(decoded, dict):
            record["structured_result"] = {
                field: decoded.get(field) for field in _STRUCTURED_RESULT_FIELDS
            }
    calls.append(record)


def _normalize_target_path(value: str, workdir: str | Path | None) -> str:
    candidate = Path(value)
    if workdir is not None and candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(Path(workdir).resolve()).as_posix()
        except (OSError, ValueError):
            pass
    return posixpath.normpath(value.replace("\\", "/"))


def summarize_edit_metrics(
    calls: list[dict[str, Any]],
    changed_files: list[str],
    expected_files: set[str],
    workdir: str | Path | None = None,
) -> dict[str, Any]:
    """Summarize whether and how the Agent attempted to modify task files."""
    edit_calls = [call for call in calls if call.get("name") in EDIT_TOOLS]
    transactional = [
        call for call in edit_calls if call.get("name") == TRANSACTIONAL_EDIT_TOOL
    ]
    legacy = [call for call in edit_calls if call.get("name") == LEGACY_EDIT_TOOL]
    error_codes: Counter[str] = Counter()
    success_count = 0
    precondition_failures = 0
    write_failures = 0
    rollback_count = 0
    unparseable_count = 0
    target_files: set[str] = set()

    for call in transactional:
        arguments = call.get("arguments") or {}
        if isinstance(arguments.get("path"), str):
            target_files.add(_normalize_target_path(arguments["path"], workdir))
        result = call.get("structured_result")
        if not isinstance(result, dict):
            unparseable_count += 1
            continue
        if result.get("success") is True:
            success_count += 1
        error_code = result.get("error_code")
        if isinstance(error_code, str) and error_code:
            error_codes[error_code] += 1
            if error_code in WRITE_FAILURE_CODES:
                write_failures += 1
            else:
                precondition_failures += 1
        if result.get("rolled_back") is True:
            rollback_count += 1

    for call in legacy:
        arguments = call.get("arguments") or {}
        if isinstance(arguments.get("path"), str):
            target_files.add(_normalize_target_path(arguments["path"], workdir))
        preview = str(call.get("result_preview", ""))
        if preview.startswith("[成功]"):
            success_count += 1

    changed = {_normalize_target_path(path, workdir) for path in changed_files}
    expected = {_normalize_target_path(path, workdir) for path in expected_files}
    modified_targets = sorted(target_files & changed)
    return {
        "edit_attempted": bool(edit_calls),
        "edit_attempt_count": len(edit_calls),
        "transactional_edit_attempt_count": len(transactional),
        "legacy_write_attempt_count": len(legacy),
        "edit_success_count": success_count,
        "edit_precondition_failure_count": precondition_failures,
        "edit_write_failure_count": write_failures,
        "edit_rollback_count": rollback_count,
        "edit_unparseable_result_count": unparseable_count,
        "edit_error_codes": dict(sorted(error_codes.items())),
        "edit_target_files": sorted(target_files),
        "edit_modified_files": modified_targets,
        "edit_modified_expected_file": bool(set(modified_targets) & expected),
    }
