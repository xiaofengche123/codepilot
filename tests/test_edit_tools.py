import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

import tools.edit_tools as edit_tools
from tools import DANGEROUS_TOOLS, TOOLS_REGISTRY, execute_tool, registry
from tools.edit_tools import EditOperation, EditRequest, apply_edit_transaction
from tools.registry import RiskLevel


def _request(path: str, *edits: EditOperation, **kwargs) -> EditRequest:
    return EditRequest(path=path, edits=list(edits), **kwargs)


def test_single_edit_commits_and_returns_hash_and_diff(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = hashlib.sha256(target.read_bytes()).hexdigest()

    result = apply_edit_transaction(
        _request("sample.py", EditOperation("value = 1", "value = 2")),
        str(tmp_path),
    )

    assert result.success is True
    assert result.before_sha256 == before
    assert result.after_sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert result.replacements == 1
    assert "-value = 1" in result.diff
    assert "+value = 2" in result.diff
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_multiple_edits_are_applied_against_original_text(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("alpha beta gamma\n", encoding="utf-8")

    result = apply_edit_transaction(
        _request(
            "sample.txt",
            EditOperation("alpha", "A"),
            EditOperation("gamma", "G"),
        ),
        str(tmp_path),
    )

    assert result.success is True
    assert result.replacements == 2
    assert target.read_text(encoding="utf-8") == "A beta G\n"


@pytest.mark.parametrize(
    ("text", "operation"),
    [
        ("one\n", EditOperation("missing", "x")),
        ("one one\n", EditOperation("one", "x")),
    ],
)
def test_match_count_mismatch_keeps_original(tmp_path, text, operation):
    target = tmp_path / "sample.txt"
    target.write_text(text, encoding="utf-8")
    original = target.read_bytes()

    result = apply_edit_transaction(
        _request("sample.txt", operation), str(tmp_path)
    )

    assert result.success is False
    assert result.error_code == "match_count_mismatch"
    assert target.read_bytes() == original


def test_expected_count_can_replace_multiple_occurrences(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("one one\n", encoding="utf-8")

    result = apply_edit_transaction(
        _request("sample.txt", EditOperation("one", "two", expected_count=2)),
        str(tmp_path),
    )

    assert result.success is True
    assert result.replacements == 2
    assert target.read_text(encoding="utf-8") == "two two\n"


def test_sha_conflict_rejects_transaction(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("before\n", encoding="utf-8")

    result = apply_edit_transaction(
        _request(
            "sample.txt",
            EditOperation("before", "after"),
            expected_sha256="0" * 64,
        ),
        str(tmp_path),
    )

    assert result.error_code == "sha_mismatch"
    assert target.read_text(encoding="utf-8") == "before\n"


def test_parent_traversal_and_absolute_path_cannot_escape_workdir(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")

    relative_result = apply_edit_transaction(
        _request("../outside.txt", EditOperation("secret", "changed")), str(root)
    )
    absolute_result = apply_edit_transaction(
        _request(str(outside), EditOperation("secret", "changed")), str(root)
    )

    assert relative_result.error_code == "path_outside_workdir"
    assert absolute_result.error_code == "path_outside_workdir"
    assert outside.read_text(encoding="utf-8") == "secret\n"


def test_symlink_escape_is_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    result = apply_edit_transaction(
        _request("link.txt", EditOperation("secret", "changed")), str(root)
    )

    assert result.error_code == "symlink_escape"
    assert outside.read_text(encoding="utf-8") == "secret\n"


def test_overlapping_edits_are_rejected_without_partial_write(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("abcdef\n", encoding="utf-8")

    result = apply_edit_transaction(
        _request(
            "sample.txt",
            EditOperation("abc", "A"),
            EditOperation("bc", "B"),
        ),
        str(tmp_path),
    )

    assert result.error_code == "overlapping_edits"
    assert target.read_text(encoding="utf-8") == "abcdef\n"


def test_python_syntax_error_is_rejected_before_write(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")

    result = apply_edit_transaction(
        _request("sample.py", EditOperation("value = 1", "if:")), str(tmp_path)
    )

    assert result.error_code == "python_syntax_error"
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_utf8_bom_and_crlf_are_preserved(tmp_path):
    target = tmp_path / "sample.py"
    original = edit_tools.UTF8_BOM + b"value = 1\r\nprint(value)\r\n"
    target.write_bytes(original)

    result = apply_edit_transaction(
        _request(
            "sample.py",
            EditOperation("value = 1\nprint(value)", "value = 2\nprint(value)"),
        ),
        str(tmp_path),
    )

    updated = target.read_bytes()
    assert result.success is True
    assert updated.startswith(edit_tools.UTF8_BOM)
    assert updated[len(edit_tools.UTF8_BOM):] == b"value = 2\r\nprint(value)\r\n"


def test_dry_run_returns_proposed_hash_and_does_not_write(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_bytes(b"before\n")
    original = target.read_bytes()

    result = apply_edit_transaction(
        _request(
            "sample.txt", EditOperation("before", "after"), dry_run=True
        ),
        str(tmp_path),
    )

    assert result.success is True
    assert result.after_sha256 == hashlib.sha256(b"after\n").hexdigest()
    assert "dry-run" in result.message
    assert target.read_bytes() == original


def test_external_change_after_validation_is_not_overwritten(tmp_path, monkeypatch):
    target = tmp_path / "sample.txt"
    target.write_text("before\n", encoding="utf-8")
    real_read = edit_tools._read_bytes
    calls = 0

    def change_on_second_read(path: Path) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_text("external\n", encoding="utf-8")
        return real_read(path)

    monkeypatch.setattr(edit_tools, "_read_bytes", change_on_second_read)
    result = apply_edit_transaction(
        _request("sample.txt", EditOperation("before", "after")), str(tmp_path)
    )

    assert result.error_code == "concurrent_modification"
    assert target.read_text(encoding="utf-8") == "external\n"
    assert not list(tmp_path.glob("*.codepilot-tmp"))


def test_atomic_replace_failure_keeps_original_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "sample.txt"
    target.write_text("before\n", encoding="utf-8")
    original = target.read_bytes()

    def fail_replace(_source, _target):
        raise PermissionError("denied")

    monkeypatch.setattr(edit_tools.os, "replace", fail_replace)
    result = apply_edit_transaction(
        _request("sample.txt", EditOperation("before", "after")), str(tmp_path)
    )

    assert result.error_code == "write_failed"
    assert target.read_bytes() == original
    assert not list(tmp_path.glob("*.codepilot-tmp"))


def test_post_replace_verification_failure_restores_original(tmp_path, monkeypatch):
    target = tmp_path / "sample.txt"
    target.write_bytes(b"before\n")
    real_read = edit_tools._read_bytes
    calls = 0

    def corrupt_verification_read(path: Path) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 3:
            return b"unexpected bytes"
        return real_read(path)

    monkeypatch.setattr(edit_tools, "_read_bytes", corrupt_verification_read)
    result = apply_edit_transaction(
        _request("sample.txt", EditOperation("before", "after")), str(tmp_path)
    )

    assert result.error_code == "write_verification_failed"
    assert result.rolled_back is True
    assert target.read_bytes() == b"before\n"
    assert not list(tmp_path.glob("*.codepilot-tmp"))


def test_same_file_transactions_are_serialized_without_state_pollution(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_bytes(b"before\n")
    barrier = threading.Barrier(3)
    results = []

    def edit_once(new_text: str) -> None:
        barrier.wait()
        results.append(
            apply_edit_transaction(
                _request("sample.txt", EditOperation("before", new_text)),
                str(tmp_path),
            )
        )

    threads = [
        threading.Thread(target=edit_once, args=("first",)),
        threading.Thread(target=edit_once, args=("second",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(result.success for result in results) == 1
    assert sorted(result.error_code or "" for result in results) == [
        "",
        "match_count_mismatch",
    ]
    assert target.read_text(encoding="utf-8").strip() in {"first", "second"}


@pytest.mark.parametrize(
    ("raw", "error_code"),
    [(b"text\x00binary", "binary_file"), (b"\xff\xfe", "unsupported_encoding")],
)
def test_non_utf8_or_binary_files_are_rejected(tmp_path, raw, error_code):
    target = tmp_path / "sample.dat"
    target.write_bytes(raw)

    result = apply_edit_transaction(
        _request("sample.dat", EditOperation("text", "changed")), str(tmp_path)
    )

    assert result.error_code == error_code
    assert target.read_bytes() == raw


def test_configured_file_size_limit_is_enforced(tmp_path, monkeypatch):
    from config import config

    target = tmp_path / "sample.txt"
    target.write_text("too large", encoding="utf-8")
    monkeypatch.setitem(config._data["tools"], "edit_max_bytes", 3)

    result = apply_edit_transaction(
        _request("sample.txt", EditOperation("too", "x")), str(tmp_path)
    )

    assert result.error_code == "file_too_large"
    assert target.read_text(encoding="utf-8") == "too large"


def test_dispatcher_injects_workdir_and_returns_structured_json(tmp_path):
    outside = tmp_path.parent / "should-not-be-used"
    target = tmp_path / "sample.txt"
    target.write_text("before\n", encoding="utf-8")

    raw_result = execute_tool(
        "edit_file_transaction",
        {
            "path": "sample.txt",
            "edits": [{"old_text": "before", "new_text": "after"}],
            "workdir": str(outside),
        },
        workdir=str(tmp_path),
    )
    result = json.loads(raw_result)

    assert result["success"] is True
    assert result["error_code"] is None
    assert target.read_text(encoding="utf-8") == "after\n"
    assert not outside.exists()


def test_transactional_editor_is_registered_as_scoped_safe_tool():
    assert "edit_file_transaction" in TOOLS_REGISTRY
    assert "edit_file_transaction" not in DANGEROUS_TOOLS
    assert registry.risk_of("edit_file_transaction") is RiskLevel.SAFE


@pytest.mark.parametrize(
    ("extra_arguments", "error_code"),
    [
        ({"dry_run": "false"}, "invalid_dry_run"),
        ({"expected_sha256": "not-a-hash"}, "invalid_expected_sha256"),
    ],
)
def test_public_tool_rejects_schema_type_errors(
    tmp_path, extra_arguments, error_code
):
    target = tmp_path / "sample.txt"
    target.write_bytes(b"before\n")
    result = json.loads(edit_tools.edit_file_transaction(
        "sample.txt",
        [{"old_text": "before", "new_text": "after"}],
        workdir=str(tmp_path),
        **extra_arguments,
    ))

    assert result["success"] is False
    assert result["error_code"] == error_code
    assert target.read_bytes() == b"before\n"
