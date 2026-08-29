"""
码搭 CodePilot · API 集成测试

需要 DEEPSEEK_API_KEY 环境变量才能运行完整测试。
CI 上无 Key 时自动跳过。
"""

import os
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from server import SubmitRequest, app
import server

needs_api_key = pytest.mark.skipif(
    os.getenv("CODEPILOT_RUN_LLM_TESTS") != "1"
    or not os.getenv("DEEPSEEK_API_KEY")
    or "xxx" in os.getenv("DEEPSEEK_API_KEY", ""),
    reason="set CODEPILOT_RUN_LLM_TESTS=1 with DEEPSEEK_API_KEY to run live LLM tests",
)


@pytest_asyncio.fixture
async def client():
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as test_client:
            yield test_client


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "2.0.0"


@pytest.mark.asyncio
async def test_metrics(client):
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "codepilot_tasks_total" in response.text
    assert "codepilot_trace_tasks_total" in response.text
    assert "codepilot_trace_review_passed" in response.text
    assert "codepilot_trace_failures_total" in response.text


@pytest.mark.asyncio
async def test_lifespan_schedules_warmup_and_closes_worker(monkeypatch):
    events = []
    monkeypatch.setattr(
        "rag.reranker.start_background_warmup",
        lambda: events.append("warmup"),
    )
    monkeypatch.setattr(
        "rag.reranker.shutdown_worker",
        lambda **kwargs: events.append(("shutdown", kwargs)),
    )

    async with server.lifespan(app):
        assert events == ["warmup"]

    assert events == ["warmup", ("shutdown", {"wait": False})]


@pytest.mark.asyncio
async def test_lifespan_survives_warmup_scheduling_failure(monkeypatch):
    def fail_to_schedule():
        raise RuntimeError("private startup detail")

    monkeypatch.setattr("rag.reranker.start_background_warmup", fail_to_schedule)
    monkeypatch.setattr("rag.reranker.shutdown_worker", lambda **_kwargs: True)
    with pytest.warns(RuntimeWarning) as caught:
        async with server.lifespan(app):
            assert True

    message = str(caught[0].message)
    assert "rerank_warmup_start_failed" in message
    assert "private startup detail" not in message


def test_dashboard_exposes_trace_funnel_metrics():
    dashboard = (Path(__file__).parents[1] / "static" / "dashboard.html").read_text(
        encoding="utf-8"
    )
    assert "Trace 执行漏斗" in dashboard
    assert "codepilot_trace_edit_attempted" in dashboard
    assert "codepilot_trace_test_passed" in dashboard
    assert "codepilot_trace_review_passed" in dashboard


@needs_api_key
@pytest.mark.asyncio
async def test_submit_and_poll(client):
    resp = await client.post("/tasks/submit", json={
        "input": "列出当前目录文件", "project_dir": ".",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data
    assert data["status"] == "pending"

    task_id = data["task_id"]

    # 轮询直到完成
    for _ in range(30):
        resp = await client.get(f"/tasks/{task_id}")
        assert resp.status_code == 200
        task = resp.json()
        if task["status"] in ("completed", "failed"):
            break
        await pytest.importorskip("asyncio").sleep(0.5)

    assert task["status"] in ("completed", "failed")
    if task["status"] == "completed":
        assert task["result"] is not None


@needs_api_key
@pytest.mark.asyncio
async def test_cancel_task(client):
    resp = await client.post("/tasks/submit", json={
        "input": "sleep 100", "project_dir": ".",
    })
    task_id = resp.json()["task_id"]

    resp = await client.delete(f"/tasks/{task_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cancelled"] is True


@pytest.mark.asyncio
async def test_get_nonexistent_task(client):
    resp = await client.get("/tasks/nonexistent-99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_submit_rejects_non_git_project(client, tmp_path):
    resp = await client.post("/tasks/submit", json={
        "input": "hello",
        "project_dir": str(tmp_path),
    })
    assert resp.status_code == 400
    assert "Git" in resp.json()["detail"]


def test_submit_request_task_mode_is_optional_and_validated():
    assert SubmitRequest(input="hello").task_mode == "auto"
    assert SubmitRequest(input="fix", task_mode="mutation_required").task_mode == "mutation_required"
    with pytest.raises(Exception):
        SubmitRequest(input="bad", task_mode="unsafe")


@needs_api_key
@pytest.mark.asyncio
async def test_events(client):
    resp = await client.post("/tasks/submit", json={
        "input": "hello", "project_dir": ".",
    })
    task_id = resp.json()["task_id"]

    # 等待完成
    for _ in range(30):
        resp = await client.get(f"/tasks/{task_id}")
        if resp.json()["status"] in ("completed", "failed"):
            break
        await pytest.importorskip("asyncio").sleep(0.5)

    resp = await client.get(f"/tasks/{task_id}/events")
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert "cursor" in data
    assert len(data["events"]) > 0
    types = [e["type"] for e in data["events"]]
    assert "created" in types
    assert "completed" in types or "failed" in types
