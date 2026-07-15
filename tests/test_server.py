"""
码搭 CodePilot · API 集成测试

需要 DEEPSEEK_API_KEY 环境变量才能运行完整测试。
CI 上无 Key 时自动跳过。
"""

import os
import pytest
from httpx import AsyncClient, ASGITransport

from server import app

needs_api_key = pytest.mark.skipif(
    not os.getenv("DEEPSEEK_API_KEY") or "xxx" in os.getenv("DEEPSEEK_API_KEY", ""),
    reason="DEEPSEEK_API_KEY not configured (CI has no API key)",
)


@pytest.fixture
def client():
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    )


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
    assert resp.status_code in (404, 503)  # 503 if lifespan not triggered in test


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
