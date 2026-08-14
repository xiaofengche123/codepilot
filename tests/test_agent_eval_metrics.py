import json

from rag.agent_eval_metrics import record_tool_call, summarize_edit_metrics


def test_record_tool_call_preserves_edit_status_beyond_preview_limit():
    calls = []
    result = {
        "success": False,
        "path": "src/app.py",
        "before_sha256": "a" * 64,
        "after_sha256": None,
        "replacements": 0,
        "diff": "x" * 2000,
        "rolled_back": False,
        "error_code": "sha_mismatch",
        "message": "changed",
    }

    record_tool_call(
        calls,
        "edit_file_transaction",
        {"path": "src/app.py", "edits": []},
        json.dumps(result),
        preview_chars=80,
    )

    assert len(calls[0]["result_preview"]) == 80
    assert calls[0]["structured_result"]["error_code"] == "sha_mismatch"
    assert "diff" not in calls[0]["structured_result"]


def test_edit_metrics_distinguish_success_precondition_write_and_rollback():
    calls = []
    results = [
        ("src/ok.py", True, None, False),
        ("src/stale.py", False, "sha_mismatch", False),
        ("src/failed.py", False, "write_failed", False),
        ("src/rollback.py", False, "write_verification_failed", True),
    ]
    for path, success, error_code, rolled_back in results:
        record_tool_call(
            calls,
            "edit_file_transaction",
            {"path": path, "edits": [{"old_text": "a", "new_text": "b"}]},
            json.dumps({
                "success": success,
                "path": path,
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64 if success else None,
                "replacements": 1 if success else 0,
                "diff": "large diff",
                "rolled_back": rolled_back,
                "error_code": error_code,
                "message": "result",
            }),
        )

    metrics = summarize_edit_metrics(
        calls,
        ["src/ok.py", "src/unrelated.py"],
        {"src/ok.py", "src/expected.py"},
    )

    assert metrics["edit_attempted"] is True
    assert metrics["edit_attempt_count"] == 4
    assert metrics["transactional_edit_attempt_count"] == 4
    assert metrics["edit_success_count"] == 1
    assert metrics["edit_precondition_failure_count"] == 1
    assert metrics["edit_write_failure_count"] == 2
    assert metrics["edit_rollback_count"] == 1
    assert metrics["edit_error_codes"] == {
        "sha_mismatch": 1,
        "write_failed": 1,
        "write_verification_failed": 1,
    }
    assert metrics["edit_modified_files"] == ["src/ok.py"]
    assert metrics["edit_modified_expected_file"] is True


def test_edit_metrics_include_legacy_writer_and_unparseable_transaction():
    calls = []
    record_tool_call(
        calls, "write_file", {"path": "config.yml"}, "[成功] 已写入 config.yml"
    )
    record_tool_call(
        calls,
        "edit_file_transaction",
        {"path": "broken.py"},
        "not-json",
    )
    record_tool_call(calls, "read_file", {"path": "x"}, "content")

    metrics = summarize_edit_metrics(calls, ["config.yml"], {"config.yml"})

    assert metrics["edit_attempt_count"] == 2
    assert metrics["transactional_edit_attempt_count"] == 1
    assert metrics["legacy_write_attempt_count"] == 1
    assert metrics["edit_success_count"] == 1
    assert metrics["edit_unparseable_result_count"] == 1
    assert metrics["edit_target_files"] == ["broken.py", "config.yml"]


def test_no_edit_calls_produce_zeroed_metrics():
    calls = []
    record_tool_call(calls, "read_file", {"path": "x.py"}, "content")

    metrics = summarize_edit_metrics(calls, [], {"x.py"})

    assert metrics["edit_attempted"] is False
    assert metrics["edit_attempt_count"] == 0
    assert metrics["edit_error_codes"] == {}
    assert metrics["edit_modified_expected_file"] is False


def test_edit_target_paths_are_normalized_relative_to_workdir(tmp_path):
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    calls = []
    record_tool_call(
        calls,
        "write_file",
        {"path": str(target)},
        "[成功] 已写入",
    )
    record_tool_call(
        calls,
        "edit_file_transaction",
        {"path": "./src/other.py"},
        json.dumps({"success": True, "path": "./src/other.py"}),
    )

    metrics = summarize_edit_metrics(
        calls,
        ["src/app.py", "src/other.py"],
        {"src/app.py", "src/other.py"},
        workdir=tmp_path,
    )

    assert metrics["edit_target_files"] == ["src/app.py", "src/other.py"]
    assert metrics["edit_modified_files"] == ["src/app.py", "src/other.py"]
