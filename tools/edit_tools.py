"""CodePilot transactional text editing tool.

The public tool accepts JSON-compatible dictionaries, while the internal API uses
dataclasses so preconditions and outcomes remain explicit and testable.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


UTF8_BOM = b"\xef\xbb\xbf"
_PATH_LOCKS = [threading.RLock() for _ in range(64)]


@dataclass(frozen=True)
class EditOperation:
    old_text: str
    new_text: str
    expected_count: int = 1


@dataclass(frozen=True)
class EditRequest:
    path: str
    edits: list[EditOperation]
    expected_sha256: Optional[str] = None
    dry_run: bool = False


@dataclass(frozen=True)
class EditResult:
    success: bool
    path: str
    before_sha256: str
    after_sha256: Optional[str]
    replacements: int
    diff: str
    rolled_back: bool
    error_code: Optional[str]
    message: str = ""


class EditValidationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_target(path: str, workdir: Optional[str]) -> tuple[Path, Path]:
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise EditValidationError("invalid_path", "path must be a non-empty string")

    root = Path(workdir or os.getcwd()).resolve(strict=True)
    if not root.is_dir():
        raise EditValidationError("invalid_workdir", "workdir is not a directory")

    supplied = Path(path)
    candidate = supplied if supplied.is_absolute() else root / supplied
    lexical = Path(os.path.abspath(candidate))
    if not _is_within(lexical, root):
        raise EditValidationError(
            "path_outside_workdir", "target path is outside the injected workdir"
        )

    try:
        resolved = lexical.resolve(strict=True)
    except FileNotFoundError as exc:
        raise EditValidationError("path_not_found", "target file does not exist") from exc
    except OSError as exc:
        raise EditValidationError("path_resolution_failed", str(exc)) from exc

    if not _is_within(resolved, root):
        raise EditValidationError(
            "symlink_escape", "resolved target escapes the injected workdir"
        )
    if resolved.is_dir():
        raise EditValidationError("path_is_directory", "target path is a directory")
    if not resolved.is_file():
        raise EditValidationError("path_not_file", "target is not a regular file")
    return root, resolved


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _decode_text(raw: bytes) -> tuple[str, bool]:
    if b"\x00" in raw:
        raise EditValidationError("binary_file", "target contains NUL bytes")
    has_bom = raw.startswith(UTF8_BOM)
    payload = raw[len(UTF8_BOM):] if has_bom else raw
    try:
        return payload.decode("utf-8"), has_bom
    except UnicodeDecodeError as exc:
        raise EditValidationError(
            "unsupported_encoding", "only UTF-8 text files are supported"
        ) from exc


def _newline_style(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def _adapt_newlines(text: str, newline: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)


def _find_non_overlapping(text: str, needle: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return spans
        end = index + len(needle)
        spans.append((index, end))
        start = end


def _prepare_edits(
    original: str, operations: list[EditOperation], newline: str
) -> tuple[str, int]:
    if not operations:
        raise EditValidationError("empty_edits", "at least one edit is required")

    replacements: list[tuple[int, int, str, int]] = []
    for operation_index, operation in enumerate(operations):
        if not isinstance(operation.old_text, str) or not operation.old_text:
            raise EditValidationError(
                "empty_match", f"edit {operation_index} old_text must not be empty"
            )
        if not isinstance(operation.new_text, str):
            raise EditValidationError(
                "invalid_new_text", f"edit {operation_index} new_text must be a string"
            )
        if (
            isinstance(operation.expected_count, bool)
            or not isinstance(operation.expected_count, int)
            or operation.expected_count < 1
        ):
            raise EditValidationError(
                "invalid_expected_count",
                f"edit {operation_index} expected_count must be a positive integer",
            )

        old_text = _adapt_newlines(operation.old_text, newline)
        new_text = _adapt_newlines(operation.new_text, newline)
        spans = _find_non_overlapping(original, old_text)
        if len(spans) != operation.expected_count:
            raise EditValidationError(
                "match_count_mismatch",
                f"edit {operation_index} expected {operation.expected_count} match(es), "
                f"found {len(spans)}",
            )
        replacements.extend(
            (start, end, new_text, operation_index) for start, end in spans
        )

    replacements.sort(key=lambda item: (item[0], item[1]))
    for previous, current in zip(replacements, replacements[1:]):
        if current[0] < previous[1]:
            raise EditValidationError(
                "overlapping_edits",
                f"edits {previous[3]} and {current[3]} target overlapping text",
            )

    updated = original
    for start, end, new_text, _ in reversed(replacements):
        updated = updated[:start] + new_text + updated[end:]
    return updated, len(replacements)


def _validate_python(path: Path, text: str) -> None:
    if path.suffix.lower() != ".py":
        return
    try:
        ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        location = f"line {exc.lineno}" if exc.lineno else "unknown line"
        raise EditValidationError(
            "python_syntax_error", f"Python syntax validation failed at {location}: {exc.msg}"
        ) from exc


def _make_diff(path: str, before: str, after: str) -> str:
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    from config import config

    limit = max(256, int(config.get("tools.diff_max_chars", 3000)))
    if len(diff) <= limit:
        return diff
    omitted = len(diff) - limit
    return f"{diff[:limit]}\n... [diff truncated; {omitted} characters omitted]"


def _write_temp(path: Path, data: bytes, mode: int) -> Path:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".codepilot-tmp", dir=str(path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, stat.S_IMODE(mode))
        return temp_path
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise


def _restore_original(path: Path, original: bytes, mode: int) -> bool:
    rollback_temp: Optional[Path] = None
    try:
        rollback_temp = _write_temp(path, original, mode)
        os.replace(rollback_temp, path)
        return _read_bytes(path) == original
    except Exception:
        return False
    finally:
        if rollback_temp is not None:
            rollback_temp.unlink(missing_ok=True)


def _path_lock(path: Path) -> threading.RLock:
    normalized = os.path.normcase(str(path))
    return _PATH_LOCKS[hash(normalized) % len(_PATH_LOCKS)]


def _failure(
    request: EditRequest,
    code: str,
    message: str,
    before_sha256: str = "",
    rolled_back: bool = False,
) -> EditResult:
    return EditResult(
        success=False,
        path=request.path,
        before_sha256=before_sha256,
        after_sha256=None,
        replacements=0,
        diff="",
        rolled_back=rolled_back,
        error_code=code,
        message=message,
    )


def apply_edit_transaction(request: EditRequest, workdir: Optional[str] = None) -> EditResult:
    """Validate and atomically apply a single-file edit transaction."""
    if not isinstance(request.dry_run, bool):
        return _failure(request, "invalid_dry_run", "dry_run must be a boolean")
    if request.expected_sha256 is not None and (
        not isinstance(request.expected_sha256, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", request.expected_sha256) is None
    ):
        return _failure(
            request,
            "invalid_expected_sha256",
            "expected_sha256 must be a 64-character hexadecimal digest",
        )
    try:
        _, target = _resolve_target(request.path, workdir)
    except EditValidationError as exc:
        return _failure(request, exc.code, str(exc))
    except Exception as exc:
        return _failure(request, "path_resolution_failed", str(exc))

    with _path_lock(target):
        before_sha = ""
        try:
            original_raw = _read_bytes(target)
            before_sha = _sha256(original_raw)
            from config import config

            max_bytes = max(1, int(config.get("tools.edit_max_bytes", 2_000_000)))
            if len(original_raw) > max_bytes:
                raise EditValidationError(
                    "file_too_large",
                    f"target is {len(original_raw)} bytes; limit is {max_bytes}",
                )
            if request.expected_sha256 and request.expected_sha256.lower() != before_sha:
                raise EditValidationError(
                    "sha_mismatch", "target changed since the caller last read it"
                )

            original_text, has_bom = _decode_text(original_raw)
            newline = _newline_style(original_text)
            updated_text, replacement_count = _prepare_edits(
                original_text, request.edits, newline
            )
            _validate_python(target, updated_text)
            updated_raw = (UTF8_BOM if has_bom else b"") + updated_text.encode("utf-8")
            after_sha = _sha256(updated_raw)
            diff = _make_diff(request.path, original_text, updated_text)

            if request.dry_run:
                return EditResult(
                    True, request.path, before_sha, after_sha, replacement_count,
                    diff, False, None, "dry-run validated; file was not changed",
                )

            if updated_raw == original_raw:
                return EditResult(
                    True, request.path, before_sha, after_sha, replacement_count,
                    diff, False, None, "transaction produced no byte changes",
                )

            mode = target.stat().st_mode
            temp_path: Optional[Path] = None
            try:
                temp_path = _write_temp(target, updated_raw, mode)
                if _read_bytes(target) != original_raw:
                    raise EditValidationError(
                        "concurrent_modification",
                        "target changed after validation; transaction was aborted",
                    )
                os.replace(temp_path, target)
                temp_path = None
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

            verification_error = ""
            try:
                verified = _read_bytes(target) == updated_raw
            except OSError as exc:
                verified = False
                verification_error = f": {exc}"
            if not verified:
                rolled_back = _restore_original(target, original_raw, mode)
                code = "write_verification_failed" if rolled_back else "rollback_failed"
                return _failure(
                    request,
                    code,
                    "written bytes could not be verified against the prepared transaction"
                    f"{verification_error}",
                    before_sha,
                    rolled_back,
                )

            return EditResult(
                True, request.path, before_sha, after_sha, replacement_count,
                diff, False, None, "transaction committed",
            )
        except EditValidationError as exc:
            return _failure(request, exc.code, str(exc), before_sha)
        except OSError as exc:
            return _failure(request, "write_failed", str(exc), before_sha)
        except Exception as exc:
            return _failure(request, "internal_error", str(exc), before_sha)


def _parse_operations(edits: Any) -> list[EditOperation]:
    if not isinstance(edits, list):
        raise EditValidationError("invalid_edits", "edits must be an array")
    parsed: list[EditOperation] = []
    for index, item in enumerate(edits):
        if not isinstance(item, dict):
            raise EditValidationError("invalid_edit", f"edit {index} must be an object")
        unknown = set(item) - {"old_text", "new_text", "expected_count"}
        if unknown:
            raise EditValidationError(
                "invalid_edit", f"edit {index} has unknown fields: {sorted(unknown)}"
            )
        if "old_text" not in item or "new_text" not in item:
            raise EditValidationError(
                "invalid_edit", f"edit {index} requires old_text and new_text"
            )
        expected_count = item.get("expected_count", 1)
        if isinstance(expected_count, bool) or not isinstance(expected_count, int):
            raise EditValidationError(
                "invalid_expected_count",
                f"edit {index} expected_count must be an integer",
            )
        parsed.append(EditOperation(item["old_text"], item["new_text"], expected_count))
    return parsed


def edit_file_transaction(
    path: str,
    edits: list[dict],
    expected_sha256: Optional[str] = None,
    dry_run: bool = False,
    workdir: Optional[str] = None,
) -> str:
    """Public tool adapter; returns a stable JSON result for Agent and MCP clients."""
    try:
        request = EditRequest(
            path=path,
            edits=_parse_operations(edits),
            expected_sha256=expected_sha256,
            dry_run=dry_run,
        )
        result = apply_edit_transaction(request, workdir)
    except EditValidationError as exc:
        result = EditResult(
            False, str(path), "", None, 0, "", False, exc.code, str(exc)
        )
    return json.dumps(asdict(result), ensure_ascii=False)


EDIT_TOOLS = {"edit_file_transaction": edit_file_transaction}

EDIT_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "edit_file_transaction",
            "description": (
                "安全地对一个现有文本文件执行精确局部修改。所有匹配、SHA 和语法检查"
                "通过后才原子写入；失败时不会部分修改。优先用于修改现有代码。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "现有文件路径，必须位于 Agent 工作目录内",
                    },
                    "edits": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {
                                    "type": "string",
                                    "description": "要精确匹配的原文本，不能为空",
                                },
                                "new_text": {
                                    "type": "string",
                                    "description": "替换后的文本，可为空字符串",
                                },
                                "expected_count": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "default": 1,
                                    "description": "原文本预期出现次数，默认 1",
                                },
                            },
                            "required": ["old_text", "new_text"],
                            "additionalProperties": False,
                        },
                    },
                    "expected_sha256": {
                        "type": "string",
                        "description": "可选的读取时 SHA-256，用于乐观并发控制",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "仅验证并返回 diff，不写入文件",
                    },
                },
                "required": ["path", "edits"],
                "additionalProperties": False,
            },
        },
    }
]

# The tool is scoped to the injected workdir and performs preflight validation, so
# it remains available to unattended Agent/API execution like the existing writer.
EDIT_DANGEROUS_TOOLS: set[str] = set()
