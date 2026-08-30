"""在单个隔离副本中运行一次 CodePilot Agent 任务。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _commit_injected_state(root: Path, task_id: str, condition: str) -> None:
    """Make the injected defect the review baseline inside the isolated worker."""
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"injected defect {task_id} {condition}"],
        cwd=root,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--condition", choices=("hybrid", "rerank"), required=True)
    parser.add_argument("--model")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = Path.cwd()
    sys.path.insert(0, str(root))
    from rag.agent_eval_metrics import record_tool_call, summarize_edit_metrics

    tasks = json.loads(
        Path(__file__).with_name("agent-tasks-v1.json").read_text(encoding="utf-8")
    )
    task = next(item for item in tasks if item["id"] == args.task)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "CodePilot Eval"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "eval@codepilot.local"], cwd=root, check=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"baseline {args.condition}"],
        cwd=root, check=True,
    )

    for mutation in task["mutations"]:
        path = root / mutation["file"]
        source = path.read_text(encoding="utf-8")
        if source.count(mutation["old"]) != 1:
            raise RuntimeError(f"{task['id']} mutation 不是唯一匹配: {mutation['file']}")
        path.write_text(source.replace(mutation["old"], mutation["new"], 1), encoding="utf-8", newline="\n")

    _commit_injected_state(root, task["id"], args.condition)

    tracked = [
        path for path in root.rglob("*")
        if path.is_file()
        and not ({".git", ".codepilot", "__pycache__", ".pytest_cache"} & set(path.parts))
    ]
    before = {path.relative_to(root).as_posix(): _hash(path) for path in tracked}

    from config import config
    config._data["rag"]["reranker"]["enabled"] = args.condition == "rerank"
    # 故障注入可以修改文件中的 local_files_only，但 harness 自身绝不能因此
    # 发起模型下载；Agent 仍会看到并修复磁盘上的错误配置。
    config._data["rag"]["local_files_only"] = True
    config._data["rag"]["reranker"]["local_files_only"] = True
    from rag.indexer import index_project
    index_message = index_project(str(root), force=True)
    from agent import AgentSession

    tool_calls = []
    started = time.perf_counter()
    error = None
    answer = ""
    session = None
    try:
        session = AgentSession(
            working_dir=str(root), memory_dir=str(root / ".codepilot/eval-memory"),
            session_id=f"{task['id']}-{args.condition}", model_name=args.model,
            confirm=lambda _name, _arguments: True, max_iterations=10,
            task_mode="mutation_required",
        )
        answer = session.run(
            task["prompt"],
            on_tool_call=lambda name, arguments, result: record_tool_call(
                tool_calls, name, arguments, result
            ),
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started

    after_files = [
        path for path in root.rglob("*")
        if path.is_file()
        and not ({".git", ".codepilot", "__pycache__", ".pytest_cache"} & set(path.parts))
    ]
    after = {path.relative_to(root).as_posix(): _hash(path) for path in after_files}
    changed_by_agent = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    test_parts = shlex.split(task["test_command"])
    if not test_parts or test_parts[0] != "pytest":
        raise RuntimeError(f"不支持的测试命令: {task['test_command']}")
    test_env = os.environ.copy()
    # 模型缓存的 D 盘绝对路径只用于评测运行；项目测试应验证隔离副本自身的
    # 相对配置，不能被 harness 环境覆盖，否则会制造与任务无关的假阴性。
    test_env.pop("CODEPILOT_MODEL_CACHE", None)
    test = subprocess.run(
        [sys.executable, "-m", "pytest", *test_parts[1:]],
        cwd=root, env=test_env, text=True,
        capture_output=True, timeout=300,
    )
    expected_files = set(task["expected_files"])
    modified_expected_file = bool(expected_files & set(changed_by_agent))
    mutation_restored = all(
        mutation["old"] in (root / mutation["file"]).read_text(encoding="utf-8")
        and mutation["new"] not in (root / mutation["file"]).read_text(encoding="utf-8")
        for mutation in task["mutations"]
    )
    edit_metrics = summarize_edit_metrics(
        tool_calls, changed_by_agent, expected_files, workdir=root
    )
    report = {
        "schema_version": 2,
        "task_id": task["id"], "condition": args.condition, "model": args.model,
        "success": (
            error is None and test.returncode == 0
            and modified_expected_file and mutation_restored
        ),
        "elapsed_seconds": elapsed, "semantic_search_calls": sum(
            call["name"] == "search_semantic" for call in tool_calls
        ),
        "tool_call_count": len(tool_calls), "changed_by_agent": changed_by_agent,
        "modified_expected_file": modified_expected_file,
        "mutation_restored": mutation_restored,
        "unexpected_files": sorted(set(changed_by_agent) - expected_files),
        "expected_files": sorted(expected_files),
        "test_returncode": test.returncode, "test_output": (test.stdout + test.stderr)[-4000:],
        "agent_error": error, "answer": answer[-4000:], "tool_calls": tool_calls,
        "index_message": index_message,
        "execution_trace": (
            session.execution_state.trace_snapshot() if session is not None else None
        ),
        "model_usage": (
            session.model_usage_snapshot() if session is not None else None
        ),
        **edit_metrics,
    }
    report["agent_final_status"] = (
        report["execution_trace"].get("final_status")
        if isinstance(report.get("execution_trace"), dict) else None
    )
    report["agent_completed"] = report["agent_final_status"] == "complete"
    from trace_analysis import analyze_report
    classification = analyze_report(report)
    report.update(classification)
    if isinstance(report.get("execution_trace"), dict):
        report["execution_trace"].update(classification)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
