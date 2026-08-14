"""在 D 盘隔离副本中对比 Hybrid 与 Rerank 的端到端 Agent 表现。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
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

    shutil.rmtree(resolved, onexc=make_writable)


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
            manifest["worker_failures"] = []
    _write_json(manifest_path, manifest)

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    d_temp = ROOT / ".rag-eval/tmp"
    d_temp.mkdir(parents=True, exist_ok=True)
    load_dotenv(ROOT / ".env", override=True)
    env = os.environ.copy()
    env.update({
        "TEMP": str(d_temp), "TMP": str(d_temp),
        "HF_HOME": str(ROOT / ".codepilot/model-cache"),
        "SENTENCE_TRANSFORMERS_HOME": str(ROOT / ".codepilot/model-cache"),
        "CODEPILOT_MODEL_CACHE": str(ROOT / ".codepilot/model-cache"),
        "TOKENIZERS_PARALLELISM": "false",
        "GIT_CONFIG_GLOBAL": "NUL",
    })

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
            try:
                completed = subprocess.run(
                    command, cwd=target, env=env, timeout=900
                )
                returncode = completed.returncode
            except subprocess.TimeoutExpired:
                returncode = 124
            elapsed = time.perf_counter() - started
            print(task["id"], condition, returncode, f"{elapsed:.1f}s", flush=True)
            _remove_tree(target)
            if returncode != 0:
                manifest["worker_failures"].append({
                    "task_id": task["id"], "condition": condition,
                    "returncode": returncode,
                })
            manifest["completed_runs"] = len(load_reports(result_root))
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
    manifest["status"] = (
        "completed" if len(reports) == len(planned) and not manifest["worker_failures"]
        else "partial_failure"
    )
    _write_json(manifest_path, manifest)
    if manifest["status"] != "completed":
        raise SystemExit(
            f"评测未完整完成: {len(reports)}/{len(planned)}, "
            f"worker_failures={len(manifest['worker_failures'])}"
        )


if __name__ == "__main__":
    main()
