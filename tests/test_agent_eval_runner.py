import importlib.util
import os
from pathlib import Path
import shutil
import subprocess


RUNNER_PATH = Path(__file__).parents[1] / ".rag-eval" / "run_agent_eval.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("codepilot_agent_eval_runner", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_worker_timeout_terminates_process_tree(tmp_path, monkeypatch):
    runner = _load_runner()
    class FakeProcess:
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("worker", timeout)

    process = FakeProcess()
    terminated = []

    class FakePopen:
        def __new__(cls, *args, **kwargs):
            assert kwargs["cwd"] == tmp_path
            return process

    monkeypatch.setattr(runner.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        runner, "_terminate_process_tree", lambda candidate: terminated.append(candidate)
    )

    returncode, error = runner._run_worker(
        ["python", "worker.py"],
        tmp_path,
        {},
        timeout=0.1,
    )

    assert returncode == 124
    assert error == "worker_timeout_after_0.1_seconds"
    assert terminated == [process]


def test_worker_failure_report_is_loadable_by_summary_pipeline():
    runner = _load_runner()

    report = runner._worker_failure_report(
        "A16", "hybrid", "deepseek-chat", 124, "worker timeout", 900.1
    )

    assert report["schema_version"] == 2
    assert report["success"] is False
    assert report["worker_failure"] is True
    assert report["worker_returncode"] == 124
    assert report["timing_scope"] == "worker_wall_failure"
    assert report["elapsed_seconds"] == 900.1
    assert report["tool_calls"] == []


def test_worker_environment_uses_project_venv_and_offline_caches(tmp_path):
    runner = _load_runner()

    env = runner._worker_environment(tmp_path)

    assert env["PATH"].split(os.pathsep)[0] == str(runner.PYTHON.parent)
    assert env["VIRTUAL_ENV"] == str(runner.PYTHON.parent.parent)
    assert env["TEMP"] == str(tmp_path)
    assert env["PIP_NO_INDEX"] == "1"
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"


def test_remove_tree_retries_transient_permission_error(tmp_path, monkeypatch):
    runner = _load_runner()
    runner.WORK_ROOT = tmp_path
    target = tmp_path / "locked-worker"
    target.mkdir()
    (target / "data.bin").write_bytes(b"locked")
    real_rmtree = shutil.rmtree
    attempts = 0

    def flaky_rmtree(path, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("file is temporarily locked")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(runner.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    runner._remove_tree(target)

    assert attempts == 3
    assert not target.exists()
