"""在 D 盘隔离副本中对比 Hybrid 与 Rerank 的端到端 Agent 表现。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import stat
import subprocess
import sys
import time

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK_ROOT = ROOT / ".rag-eval/worktrees"
RESULT_BASE = ROOT / ".rag-eval/results"
PYTHON = ROOT / "venv/Scripts/python.exe"


def _ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in {".git", ".codepilot", "__pycache__", ".pytest_cache"}}
    current = Path(directory)
    if current.name == ".rag-eval":
        ignored.update(name for name in names if name in {"external-data", "worktrees", "results"})
    ignored.update(name for name in names if name.startswith(("venv.broken-", ".venv.broken-")))
    if current == ROOT:
        ignored.update({
            "venv", ".rag-eval", ".env", ".agents", ".codex", ".codex-tmp",
            ".idea", "agent面试",
        })
    return ignored


def _remove_tree(target: Path) -> None:
    if not target.exists():
        return
    resolved = target.resolve()
    if resolved.parent != WORK_ROOT.resolve():
        raise RuntimeError(f"拒绝清理非评测工作区: {resolved}")

    def make_writable(function, path, _exc_info):
        os.chmod(path, stat.S_IWRITE)
        function(path)

    last_error = None
    for attempt in range(5):
        try:
            shutil.rmtree(resolved, onerror=make_writable)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    raise last_error


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout.strip()


def _write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, timeout=30,
            )
            if result.returncode != 0 and process.poll() is None:
                process.kill()
        except (OSError, subprocess.SubprocessError):
            if process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _run_worker(command: list[str], cwd: Path, env: dict, timeout: int) -> tuple[int, str | None]:
    options = {"cwd": cwd, "env": env}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, **options)
    try:
        return process.wait(timeout=timeout), None
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        return 124, f"worker_timeout_after_{timeout}_seconds"


def _worker_failure_report(
    task_id: str,
    condition: str,
    model: str,
    returncode: int,
    error: str,
    elapsed: float,
) -> dict:
    report = {
        "schema_version": 2,
        "task_id": task_id,
        "condition": condition,
        "model": model,
        "success": False,
        "elapsed_seconds": elapsed,
        "worker_elapsed_seconds": elapsed,
        "timing_scope": "worker_wall_failure",
        "semantic_search_calls": 0,
        "tool_call_count": 0,
        "changed_by_agent": [],
        "modified_expected_file": False,
        "mutation_restored": False,
        "unexpected_files": [],
        "test_returncode": None,
        "test_output": "",
        "agent_error": error,
        "answer": "",
        "tool_calls": [],
        "index_message": "",
        "edit_attempted": False,
        "edit_attempt_count": 0,
        "transactional_edit_attempt_count": 0,
        "legacy_write_attempt_count": 0,
        "edit_success_count": 0,
        "edit_precondition_failure_count": 0,
        "edit_write_failure_count": 0,
        "edit_rollback_count": 0,
        "edit_unparseable_result_count": 0,
        "edit_error_codes": {},
        "edit_target_files": [],
        "edit_modified_files": [],
        "edit_modified_expected_file": False,
        "worker_failure": True,
        "worker_returncode": returncode,
    }
    from trace_analysis import analyze_report
    report.update(analyze_report(report))
    return report


def _worker_environment(temp_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
        "HF_HOME": str(ROOT / ".codepilot/model-cache"),
        "SENTENCE_TRANSFORMERS_HOME": str(ROOT / ".codepilot/model-cache"),
        "CODEPILOT_MODEL_CACHE": str(ROOT / ".codepilot/model-cache"),
        "TOKENIZERS_PARALLELISM": "false",
        "GIT_CONFIG_GLOBAL": "NUL" if os.name == "nt" else "/dev/null",
        "VIRTUAL_ENV": str(PYTHON.parent.parent),
        "PIP_NO_INDEX": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    })
    env["PATH"] = str(PYTHON.parent) + os.pathsep + env.get("PATH", "")
    return env


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-id", required=True, help="独立结果目录名称")
    parser.add_argument("--task", action="append", help="只运行指定任务，可重复")
    parser.add_argument("--condition", choices=("hybrid", "rerank", "both"), default="both")
    parser.add_argument("--confirm-cost", action="store_true", help="确认允许真实模型 API 调用")
    parser.add_argument("--resume", action="store_true", help="跳过已有结果并断点续跑")
    parser.add_argument("--overwrite", action="store_true", help="明确允许覆盖同 run-id 的单项结果")
    parser.add_argument("--plan", action="store_true", help="只显示执行计划，不调用模型")
    args = parser.parse_args()

    from rag.agent_eval_report import (
        load_reports, summarize_reports, validate_run_id,
    )

    try:
        run_id = validate_run_id(args.run_id)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.resume and args.overwrite:
        raise SystemExit("--resume 与 --overwrite 不能同时使用")

    tasks = json.loads((ROOT / ".rag-eval/agent-tasks-v1.json").read_text(encoding="utf-8"))
    if args.task:
        wanted = set(args.task)
        tasks = [task for task in tasks if task["id"] in wanted]
        missing = sorted(wanted - {task["id"] for task in tasks})
        if missing:
            raise SystemExit(f"未知任务: {', '.join(missing)}")
    conditions = ("hybrid", "rerank") if args.condition == "both" else (args.condition,)
    planned = [(task["id"], condition) for task in tasks for condition in conditions]
    print(
        json.dumps({
            "run_id": run_id,
            "model": args.model,
            "task_count": len(tasks),
            "conditions": list(conditions),
            "planned_runs": len(planned),
        }, ensure_ascii=False),
        flush=True,
    )
    if args.plan:
        return
    if not args.confirm_cost:
        raise SystemExit("这会调用真实模型 API；确认费用后追加 --confirm-cost")

    result_root = RESULT_BASE / run_id
    if (
        result_root.exists()
        and any(result_root.iterdir())
        and not (args.resume or args.overwrite)
    ):
        raise SystemExit(
            f"结果目录已存在且非空: {result_root}; 使用 --resume 或新的 --run-id"
        )
    result_root.mkdir(parents=True, exist_ok=True)
    manifest_path = result_root / "manifest.json"
    task_path = ROOT / ".rag-eval/agent-tasks-v1.json"
    task_sha256 = hashlib.sha256(task_path.read_bytes()).hexdigest()
    commit = _git_output("rev-parse", "HEAD")
    branch = _git_output("rev-parse", "--abbrev-ref", "HEAD")
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "started_at": _utc_now(),
        "completed_at": None,
        "model": args.model,
        "conditions": list(conditions),
        "task_ids": [task["id"] for task in tasks],
        "task_dataset": ".rag-eval/agent-tasks-v1.json",
        "task_sha256": task_sha256,
        "code_commit": commit,
        "branch": branch,
        "expected_runs": len(planned),
        "completed_runs": 0,
        "worker_failures": [],
        "cleanup_failures": [],
    }
    if manifest_path.exists():
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not (args.resume or args.overwrite):
            raise SystemExit(
                f"结果目录已存在: {result_root}; 使用 --resume 或新的 --run-id"
            )
        for field in ("run_id", "model", "conditions", "task_ids", "task_sha256", "code_commit"):
            if args.resume and existing_manifest.get(field) != manifest.get(field):
                raise SystemExit(f"拒绝恢复：manifest 字段不一致: {field}")
        if args.resume:
            manifest = existing_manifest
            manifest["status"] = "running"
            manifest["completed_at"] = None
            manifest.setdefault("worker_failures", [])
            manifest.setdefault("cleanup_failures", [])
    _write_json(manifest_path, manifest)

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    d_temp = ROOT / ".rag-eval/tmp"
    d_temp.mkdir(parents=True, exist_ok=True)
    load_dotenv(ROOT / ".env", override=True)
    env = _worker_environment(d_temp)

    for task in tasks:
        for condition in conditions:
            target = WORK_ROOT / f"{run_id}-{task['id']}-{condition}"
            output = result_root / f"{task['id']}-{condition}.json"
            if args.resume and output.exists():
                print(task["id"], condition, "existing result")
                continue
            if output.exists() and not args.overwrite:
                raise SystemExit(f"拒绝覆盖已有结果: {output}")
            _remove_tree(target)
            shutil.copytree(ROOT, target, ignore=_ignore)
            command = [
                str(PYTHON), str(ROOT / ".rag-eval/agent_eval_worker.py"),
                "--task", task["id"], "--condition", condition,
                "--model", args.model, "--output", str(output),
            ]
            started = time.perf_counter()
            returncode, worker_error = _run_worker(command, target, env, timeout=900)
            elapsed = time.perf_counter() - started
            print(task["id"], condition, returncode, f"{elapsed:.1f}s", flush=True)
            if returncode != 0:
                failure = {
                    "task_id": task["id"], "condition": condition,
                    "returncode": returncode, "error": worker_error,
                    "elapsed_seconds": elapsed,
                }
                manifest["worker_failures"].append(failure)
                if not output.exists():
                    _write_json(output, _worker_failure_report(
                        task["id"], condition, args.model, returncode,
                        worker_error or f"worker_exit_{returncode}", elapsed,
                    ))
            manifest["completed_runs"] = len(load_reports(result_root))
            _write_json(manifest_path, manifest)
            try:
                _remove_tree(target)
            except Exception as exc:
                manifest["cleanup_failures"].append({
                    "task_id": task["id"], "condition": condition,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                _write_json(manifest_path, manifest)

    reports = load_reports(result_root)
    final_task_sha256 = hashlib.sha256(task_path.read_bytes()).hexdigest()
    if final_task_sha256 != task_sha256:
        manifest["status"] = "dataset_changed"
        manifest["completed_at"] = _utc_now()
        _write_json(manifest_path, manifest)
        raise SystemExit("冻结任务文件在评测期间发生变化，拒绝生成正式汇总")
    summary = summarize_reports(reports, conditions)
    summary.update({
        "run_id": run_id,
        "model": args.model,
        "task_sha256": task_sha256,
        "code_commit": commit,
        "generated_at": _utc_now(),
    })
    _write_json(result_root / "summary.json", summary)
    manifest["completed_runs"] = len(reports)
    manifest["completed_at"] = _utc_now()
    if len(reports) != len(planned):
        manifest["status"] = "partial_failure"
    elif manifest["worker_failures"]:
        manifest["status"] = "completed_with_worker_failures"
    else:
        manifest["status"] = "completed"
    _write_json(manifest_path, manifest)
    if manifest["status"] not in {"completed", "completed_with_worker_failures"}:
        raise SystemExit(
            f"评测未完整完成: {len(reports)}/{len(planned)}, "
            f"worker_failures={len(manifest['worker_failures'])}"
        )


if __name__ == "__main__":
    main()
