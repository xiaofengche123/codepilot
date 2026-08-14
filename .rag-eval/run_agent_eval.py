"""在 D 盘隔离副本中对比 Hybrid 与 Rerank 的端到端 Agent 表现。"""

from __future__ import annotations

import argparse
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
WORK_ROOT = ROOT / ".rag-eval/worktrees"
RESULT_ROOT = ROOT / ".rag-eval/results/agent-v1"
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--task", action="append", help="只运行指定任务，可重复")
    parser.add_argument("--condition", choices=("hybrid", "rerank", "both"), default="both")
    parser.add_argument("--confirm-cost", action="store_true", help="确认允许真实模型 API 调用")
    parser.add_argument("--resume", action="store_true", help="跳过已有结果并断点续跑")
    args = parser.parse_args()
    if not args.confirm_cost:
        raise SystemExit("这会调用真实模型 API；确认费用后追加 --confirm-cost")

    tasks = json.loads((ROOT / ".rag-eval/agent-tasks-v1.json").read_text(encoding="utf-8"))
    if args.task:
        wanted = set(args.task)
        tasks = [task for task in tasks if task["id"] in wanted]
    conditions = ("hybrid", "rerank") if args.condition == "both" else (args.condition,)
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
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
            target = WORK_ROOT / f"{task['id']}-{condition}"
            output = RESULT_ROOT / f"{task['id']}-{condition}.json"
            if args.resume and output.exists():
                print(task["id"], condition, "existing result")
                continue
            _remove_tree(target)
            shutil.copytree(ROOT, target, ignore=_ignore)
            command = [
                str(PYTHON), str(ROOT / ".rag-eval/agent_eval_worker.py"),
                "--task", task["id"], "--condition", condition,
                "--model", args.model, "--output", str(output),
            ]
            started = time.perf_counter()
            completed = subprocess.run(command, cwd=target, env=env)
            print(task["id"], condition, completed.returncode, f"{time.perf_counter() - started:.1f}s")
            _remove_tree(target)


if __name__ == "__main__":
    main()
