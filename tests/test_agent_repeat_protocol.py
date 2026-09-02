"""M7 repeated Agent evaluation freeze and authorization tests."""

import hashlib
import json
from pathlib import Path
import shutil

import pytest

import rag.agent_repeat_protocol as protocol
from rag.agent_repeat_protocol import AgentRepeatProtocolError


def _copy_frozen_files(tmp_path: Path, monkeypatch) -> Path:
    eval_dir = tmp_path / ".rag-eval"
    eval_dir.mkdir()
    for name in (
        "agent-repeat-v2.protocol.json",
        "agent-repeat-v2.manifest.json",
        "agent-tasks-v1.json",
    ):
        shutil.copy2(protocol.PROJECT_ROOT / ".rag-eval" / name, eval_dir / name)
    frozen = json.loads(
        (eval_dir / "agent-repeat-v2.protocol.json").read_text(encoding="utf-8")
    )
    task_bytes = (eval_dir / "agent-tasks-v1.json").read_bytes()
    monkeypatch.setattr(
        protocol, "_git_text", lambda _root, *_args: frozen["evaluation"]["code_tree"]
    )
    monkeypatch.setattr(protocol, "_git_bytes", lambda _root, *_args: task_bytes)
    return tmp_path


def _rewrite_protocol(root: Path, mutate) -> dict:
    path = root / ".rag-eval/agent-repeat-v2.protocol.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    manifest_path = root / ".rag-eval/agent-repeat-v2.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    canonical = path.read_bytes().replace(b"\r\n", b"\n")
    manifest["protocol_sha256"] = hashlib.sha256(canonical).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return data


def test_checked_in_protocol_is_frozen_authorized_and_has_formal_results():
    result = protocol.validate_repeat_protocol(require_authorization=True)

    assert result["status"] == "frozen_unfunded"
    assert result["expected_runs"] == 120
    assert result["cost_currency"] == "CNY"
    assert result["proposed_cost_cap_cny"] == 10.0
    assert result["results_pristine"] is False
    assert result["execution_authorized"] is True


def test_execution_requires_separate_authorization_file(tmp_path, monkeypatch):
    root = _copy_frozen_files(tmp_path, monkeypatch)
    with pytest.raises(AgentRepeatProtocolError) as caught:
        protocol.validate_repeat_protocol(root=root, require_authorization=True)
    assert caught.value.reason_code == "m7_authorization_missing"


def test_protocol_hash_drift_is_rejected(tmp_path, monkeypatch):
    root = _copy_frozen_files(tmp_path, monkeypatch)
    path = root / ".rag-eval/agent-repeat-v2.protocol.json"
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(AgentRepeatProtocolError) as caught:
        protocol.validate_repeat_protocol(root=root)
    assert caught.value.reason_code == "m7_protocol_hash_mismatch"


def test_frozen_hashes_accept_git_crlf_checkout(tmp_path, monkeypatch):
    root = _copy_frozen_files(tmp_path, monkeypatch)
    for name in ("agent-repeat-v2.protocol.json", "agent-tasks-v1.json"):
        path = root / ".rag-eval" / name
        lf_bytes = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(lf_bytes.replace(b"\n", b"\r\n"))

    result = protocol.validate_repeat_protocol(root=root)

    assert result["expected_runs"] == 120
    assert result["task_sha256"] == (
        "71caa70e7b441380c79745c701bb02a77f8b4d0efcfb2d892b3a91f053d7ac09"
    )


def test_task_dataset_drift_is_rejected_before_git_check(tmp_path, monkeypatch):
    root = _copy_frozen_files(tmp_path, monkeypatch)
    path = root / ".rag-eval/agent-tasks-v1.json"
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(AgentRepeatProtocolError) as caught:
        protocol.validate_repeat_protocol(root=root)
    assert caught.value.reason_code == "m7_task_hash_mismatch"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda data: data["matrix"].update(repetitions_per_task_condition=2),
            "m7_repetitions_mismatch",
        ),
        (
            lambda data: data["matrix"].update(conditions=["rerank", "hybrid"]),
            "m7_conditions_mismatch",
        ),
        (
            lambda data: data["budget"].update(proposed_cost_cap_cny=500.0),
            "m7_cost_cap_mismatch",
        ),
        (
            lambda data: data["execution"].update(overwrite_existing_results=True),
            "m7_overwrite_policy_mismatch",
        ),
    ],
)
def test_frozen_matrix_and_budget_cannot_be_silently_changed(
    tmp_path, monkeypatch, mutate, reason
):
    root = _copy_frozen_files(tmp_path, monkeypatch)
    _rewrite_protocol(root, mutate)

    with pytest.raises(AgentRepeatProtocolError) as caught:
        protocol.validate_repeat_protocol(root=root)
    assert caught.value.reason_code == reason


def test_existing_result_directory_is_a_collision_without_reading_results(
    tmp_path, monkeypatch
):
    root = _copy_frozen_files(tmp_path, monkeypatch)
    (root / ".rag-eval/results/m7-agent-repeat-v2-qwen-r1").mkdir(parents=True)

    with pytest.raises(AgentRepeatProtocolError) as caught:
        protocol.validate_repeat_protocol(root=root, require_pristine_results=True)
    assert caught.value.reason_code == "m7_result_path_collision"


def test_valid_bounded_authorization_can_be_checked(tmp_path, monkeypatch):
    root = _copy_frozen_files(tmp_path, monkeypatch)
    manifest = json.loads(
        (root / ".rag-eval/agent-repeat-v2.manifest.json").read_text(encoding="utf-8")
    )
    authorization = {
        "schema_version": 1,
        "protocol_sha256": manifest["protocol_sha256"],
        "approved_cost_cap_cny": 5.0,
        "authorized_at": "2026-08-30T12:00:00+08:00",
    }
    (root / ".rag-eval/agent-repeat-v2.authorization.json").write_text(
        json.dumps(authorization), encoding="utf-8"
    )

    result = protocol.validate_repeat_protocol(
        root=root, require_authorization=True
    )
    assert result["execution_authorized"] is True


def test_authorization_cannot_exceed_frozen_cost_cap(tmp_path, monkeypatch):
    root = _copy_frozen_files(tmp_path, monkeypatch)
    manifest = json.loads(
        (root / ".rag-eval/agent-repeat-v2.manifest.json").read_text(encoding="utf-8")
    )
    authorization = {
        "schema_version": 1,
        "protocol_sha256": manifest["protocol_sha256"],
        "approved_cost_cap_cny": 10.01,
        "authorized_at": "2026-08-30T12:00:00+08:00",
    }
    (root / ".rag-eval/agent-repeat-v2.authorization.json").write_text(
        json.dumps(authorization), encoding="utf-8"
    )

    with pytest.raises(AgentRepeatProtocolError) as caught:
        protocol.validate_repeat_protocol(root=root, require_authorization=True)
    assert caught.value.reason_code == "m7_authorization_cost_exceeds_protocol"


def test_frozen_git_tree_mismatch_is_rejected(tmp_path, monkeypatch):
    root = _copy_frozen_files(tmp_path, monkeypatch)
    monkeypatch.setattr(protocol, "_git_text", lambda _root, *_args: "0" * 40)

    with pytest.raises(AgentRepeatProtocolError) as caught:
        protocol.validate_repeat_protocol(root=root)
    assert caught.value.reason_code == "m7_code_tree_mismatch"


def test_protocol_contains_no_task_or_credential_content():
    serialized = protocol.PROTOCOL_PATH.read_text(encoding="utf-8").lower()
    for forbidden in (
        '"prompt"', '"mutations"', '"oracle"', '"api_key"', '"query"'
    ):
        assert forbidden not in serialized
