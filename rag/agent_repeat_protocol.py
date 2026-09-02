"""Validate the frozen M7 repeated Agent evaluation protocol.

This module is deliberately read-only. It never runs an Agent, loads models,
reads existing result contents, accesses the network, or authorizes API spend.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = PROJECT_ROOT / ".rag-eval/agent-repeat-v2.protocol.json"
MANIFEST_PATH = PROJECT_ROOT / ".rag-eval/agent-repeat-v2.manifest.json"
TASK_PATH = PROJECT_ROOT / ".rag-eval/agent-tasks-v1.json"
AUTHORIZATION_PATH = PROJECT_ROOT / ".rag-eval/agent-repeat-v2.authorization.json"
PROTOCOL_ID = "m7-agent-repeat-v2-qwen37flash"
RUN_ID_PREFIX = "m7-agent-repeat-v2-qwen"
CONDITIONS = ("hybrid", "rerank")
REPETITIONS = 3
TASK_COUNT = 20
EXPECTED_RUNS = TASK_COUNT * len(CONDITIONS) * REPETITIONS
PROPOSED_COST_CAP_CNY = 10.0


class AgentRepeatProtocolError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_frozen_text(data: bytes) -> bytes:
    """Keep frozen text hashes stable across Git LF/CRLF checkouts."""
    return data.replace(b"\r\n", b"\n")


def validate_repeat_protocol(
    *,
    root: Path = PROJECT_ROOT,
    protocol_path: Path | None = None,
    manifest_path: Path | None = None,
    require_pristine_results: bool = False,
    require_authorization: bool = False,
) -> dict[str, Any]:
    """Validate hashes, matrix, frozen Git tree, result paths and authorization."""
    root = root.resolve()
    protocol_path = protocol_path or root / ".rag-eval/agent-repeat-v2.protocol.json"
    manifest_path = manifest_path or root / ".rag-eval/agent-repeat-v2.manifest.json"
    task_path = root / ".rag-eval/agent-tasks-v1.json"
    protocol_bytes = _read_required(protocol_path, "m7_protocol_missing")
    manifest_bytes = _read_required(manifest_path, "m7_manifest_missing")
    task_bytes = _read_required(task_path, "m7_task_dataset_missing")
    protocol = _load_object(protocol_bytes, "m7_protocol_invalid_json")
    manifest = _load_object(manifest_bytes, "m7_manifest_invalid_json")
    tasks = _load_array(task_bytes, "m7_task_dataset_invalid_json")

    if sha256_bytes(_canonical_frozen_text(protocol_bytes)) != manifest.get(
        "protocol_sha256"
    ):
        raise AgentRepeatProtocolError("m7_protocol_hash_mismatch")
    task_sha256 = sha256_bytes(_canonical_frozen_text(task_bytes))
    if task_sha256 != manifest.get("task_sha256"):
        raise AgentRepeatProtocolError("m7_task_hash_mismatch")
    _validate_matrix(protocol, tasks)
    _validate_manifest(protocol, manifest)
    _validate_frozen_git(root, protocol, task_bytes)

    result_dirs = [root / path for path in protocol["execution"]["result_dirs"]]
    if require_pristine_results and any(path.exists() for path in result_dirs):
        raise AgentRepeatProtocolError("m7_result_path_collision")
    if require_authorization:
        _validate_authorization(root, protocol, manifest)
    return {
        "status": manifest["status"],
        "protocol_id": protocol["protocol_id"],
        "task_sha256": task_sha256,
        "protocol_sha256": manifest["protocol_sha256"],
        "evaluation_code_commit": protocol["evaluation"]["code_commit"],
        "expected_runs": protocol["matrix"]["expected_runs"],
        "cost_currency": protocol["budget"]["currency"],
        "proposed_cost_cap_cny": protocol["budget"]["proposed_cost_cap_cny"],
        "results_pristine": not any(path.exists() for path in result_dirs),
        "execution_authorized": _authorization_exists(root, protocol),
    }


def _validate_matrix(protocol: dict[str, Any], tasks: list[Any]) -> None:
    if protocol.get("schema_version") != 1:
        raise AgentRepeatProtocolError("m7_protocol_schema_mismatch")
    if protocol.get("protocol_id") != PROTOCOL_ID:
        raise AgentRepeatProtocolError("m7_protocol_id_mismatch")
    ids = [task.get("id") for task in tasks if isinstance(task, dict)]
    expected_ids = [f"A{index:02d}" for index in range(1, TASK_COUNT + 1)]
    if len(tasks) != TASK_COUNT or ids != expected_ids or len(set(ids)) != TASK_COUNT:
        raise AgentRepeatProtocolError("m7_task_matrix_mismatch")
    matrix = protocol.get("matrix", {})
    if matrix.get("task_ids") != expected_ids:
        raise AgentRepeatProtocolError("m7_task_ids_mismatch")
    if matrix.get("conditions") != list(CONDITIONS):
        raise AgentRepeatProtocolError("m7_conditions_mismatch")
    if matrix.get("repetitions_per_task_condition") != REPETITIONS:
        raise AgentRepeatProtocolError("m7_repetitions_mismatch")
    if matrix.get("expected_runs") != EXPECTED_RUNS:
        raise AgentRepeatProtocolError("m7_expected_runs_mismatch")
    expected_run_ids = [f"{RUN_ID_PREFIX}-r{index}" for index in range(1, 4)]
    execution = protocol.get("execution", {})
    if execution.get("run_ids") != expected_run_ids:
        raise AgentRepeatProtocolError("m7_run_ids_mismatch")
    expected_dirs = [f".rag-eval/results/{run_id}" for run_id in expected_run_ids]
    if execution.get("result_dirs") != expected_dirs:
        raise AgentRepeatProtocolError("m7_result_dirs_mismatch")
    if execution.get("overwrite_existing_results") is not False:
        raise AgentRepeatProtocolError("m7_overwrite_policy_mismatch")
    budget = protocol.get("budget", {})
    if budget.get("maximum_agent_runs") != EXPECTED_RUNS:
        raise AgentRepeatProtocolError("m7_run_budget_mismatch")
    if budget.get("maximum_model_turns") != EXPECTED_RUNS * 10:
        raise AgentRepeatProtocolError("m7_turn_budget_mismatch")
    if budget.get("currency") != "CNY":
        raise AgentRepeatProtocolError("m7_cost_currency_mismatch")
    if budget.get("proposed_cost_cap_cny") != PROPOSED_COST_CAP_CNY:
        raise AgentRepeatProtocolError("m7_cost_cap_mismatch")
    evaluation = protocol.get("evaluation", {})
    if evaluation.get("model") != "qwen3.7-flash":
        raise AgentRepeatProtocolError("m7_evaluation_model_mismatch")
    if evaluation.get("provider_usage_required") is not True:
        raise AgentRepeatProtocolError("m7_usage_policy_mismatch")
    preflight = protocol.get("preflight", {})
    if preflight.get("model") != "glm-4.7-flash":
        raise AgentRepeatProtocolError("m7_preflight_model_mismatch")
    if preflight.get("formal_results_reusable") is not False:
        raise AgentRepeatProtocolError("m7_preflight_reuse_policy_mismatch")


def _validate_manifest(protocol: dict[str, Any], manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != 1:
        raise AgentRepeatProtocolError("m7_manifest_schema_mismatch")
    if manifest.get("status") != "frozen_unfunded":
        raise AgentRepeatProtocolError("m7_manifest_status_mismatch")
    evaluation = protocol.get("evaluation", {})
    for field in ("code_commit", "code_tree", "task_sha256"):
        expected = evaluation.get(field) if field != "task_sha256" else protocol.get(field)
        if manifest.get(field) != expected:
            raise AgentRepeatProtocolError(f"m7_manifest_{field}_mismatch")


def _validate_frozen_git(root: Path, protocol: dict[str, Any], task_bytes: bytes) -> None:
    evaluation = protocol["evaluation"]
    commit = evaluation["code_commit"]
    tree = _git_text(root, "rev-parse", f"{commit}^{{tree}}")
    if tree != evaluation["code_tree"]:
        raise AgentRepeatProtocolError("m7_code_tree_mismatch")
    frozen_tasks = _git_bytes(root, "show", f"{commit}:.rag-eval/agent-tasks-v1.json")
    if _canonical_frozen_text(frozen_tasks) != _canonical_frozen_text(task_bytes):
        raise AgentRepeatProtocolError("m7_frozen_task_drift")


def _validate_authorization(
    root: Path, protocol: dict[str, Any], manifest: dict[str, Any]
) -> None:
    relative = protocol["authorization"]["path"]
    path = root / relative
    data = _load_object(
        _read_required(path, "m7_authorization_missing"),
        "m7_authorization_invalid_json",
    )
    if data.get("schema_version") != 1 or data.get("protocol_sha256") != manifest.get(
        "protocol_sha256"
    ):
        raise AgentRepeatProtocolError("m7_authorization_protocol_mismatch")
    cap = data.get("approved_cost_cap_cny")
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0:
        raise AgentRepeatProtocolError("m7_authorization_cost_invalid")
    if float(cap) > float(protocol["budget"]["proposed_cost_cap_cny"]):
        raise AgentRepeatProtocolError("m7_authorization_cost_exceeds_protocol")
    if not isinstance(data.get("authorized_at"), str) or not data["authorized_at"]:
        raise AgentRepeatProtocolError("m7_authorization_timestamp_missing")


def _authorization_exists(root: Path, protocol: dict[str, Any]) -> bool:
    return (root / protocol["authorization"]["path"]).is_file()


def _read_required(path: Path, reason_code: str) -> bytes:
    try:
        return path.read_bytes()
    except (OSError, ValueError) as exc:
        raise AgentRepeatProtocolError(reason_code) from exc


def _load_object(data: bytes, reason_code: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentRepeatProtocolError(reason_code) from exc
    if not isinstance(value, dict):
        raise AgentRepeatProtocolError(reason_code)
    return value


def _load_array(data: bytes, reason_code: str) -> list[Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentRepeatProtocolError(reason_code) from exc
    if not isinstance(value, list):
        raise AgentRepeatProtocolError(reason_code)
    return value


def _git_text(root: Path, *args: str) -> str:
    return _git_bytes(root, *args).decode("utf-8").strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AgentRepeatProtocolError("m7_frozen_commit_unavailable") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Check frozen M7 repeat protocol")
    parser.add_argument("--check-pristine", action="store_true")
    parser.add_argument("--require-authorization", action="store_true")
    args = parser.parse_args()
    try:
        result = validate_repeat_protocol(
            require_pristine_results=args.check_pristine,
            require_authorization=args.require_authorization,
        )
    except AgentRepeatProtocolError as exc:
        raise SystemExit(exc.reason_code) from exc
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
