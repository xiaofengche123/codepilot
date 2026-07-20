"""
码搭 CodePilot · FastAPI 服务

启动: python server.py
      uvicorn server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import config
from task_queue import Task, TaskQueue
from worktree_manager import WorktreeManager
from events import get_event_buffer, TaskEvent

# ── 调度器封装 ────────────────────────────────────────────────

_worktree_mgr: Optional[WorktreeManager] = None
_task_queue: Optional[TaskQueue] = None
_event_buffer = get_event_buffer()


async def _executor(task: Task):
    """后台执行一个 Agent 任务（在独立 worktree 中）。"""
    from agent import AgentSession

    # 事件：开始
    _event_buffer.append(TaskEvent(task.id, "started", data={
        "user_input": task.user_input, "project": task.project_dir,
    }))

    # 创建隔离工作区（失败时返回 None，降级为在项目目录直接执行）
    worktree_path = _worktree_mgr.create(task.id)

    # 工具回调 → 事件
    def on_tool(tool_name, args, result):
        _event_buffer.append(TaskEvent(task.id, "tool_call", data={
            "tool": tool_name, "args": args, "result": result[:500],
        }))

    # server 是无交互场景：危险工具（run_shell/git_add/git_commit）自动拒绝
    def confirm_dangerous(tool_name, args) -> bool:
        _event_buffer.append(TaskEvent(task.id, "warning", data={
            "msg": f"Dangerous tool '{tool_name}' auto-rejected in server mode",
        }))
        return False

    try:
        wd = worktree_path or task.project_dir
        # 每个任务独立的 LLM 实例（model 不可用时回退全局默认），互不影响
        session = AgentSession(
            working_dir=wd, model_name=task.model, confirm=confirm_dangerous,
        )
        if task.model and session.model_unavailable:
            _event_buffer.append(TaskEvent(task.id, "warning", data={
                "msg": f"Model '{task.model}' not available, using default",
            }))

        # session.run 是同步阻塞调用，放到线程池执行，
        # 否则会卡住事件循环，所有并发任务和 HTTP 请求都被阻塞
        answer = await asyncio.to_thread(session.run, task.user_input, on_tool)

        diff = _worktree_mgr.collect_diff(worktree_path) if worktree_path else ""

        _event_buffer.append(TaskEvent(task.id, "completed", data={
            "answer": answer[:200], "diff": diff,
        }))
        return {"answer": answer, "diff": diff}

    except Exception as e:
        _event_buffer.append(TaskEvent(task.id, "failed", data={"error": str(e)}))
        raise
    finally:
        if worktree_path and worktree_path != task.project_dir:
            _worktree_mgr.cleanup(worktree_path, task.id)


# ── FastAPI 生命周期 ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worktree_mgr, _task_queue
    project_dir = os.getcwd()
    _worktree_mgr = WorktreeManager(project_dir)
    max_workers = config.get("server.max_concurrent", 5)
    _task_queue = TaskQueue(max_concurrent=max_workers)
    await _task_queue.start(_executor)
    yield
    await _task_queue.stop()
    _worktree_mgr.cleanup_all()


app = FastAPI(
    title="码搭 CodePilot API",
    description="AI 编码任务执行平台 — 提交自然语言指令，在隔离环境中由 Agent 执行并返回结果。",
    version="2.0.0",
    lifespan=lifespan,
)

# 静态文件 — Dashboard
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/static/dashboard.html")


# ── 请求/响应模型 ─────────────────────────────────────────────

class SubmitRequest(BaseModel):
    input: str = Field(..., description="自然语言指令，如'帮我修复 login.py 的空指针异常'")
    project_dir: str = Field(default=".", description="项目目录路径")
    model: Optional[str] = Field(default=None, description="指定模型，如 deepseek-chat")


class SubmitResponse(BaseModel):
    task_id: str
    status: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    input: str
    result: Optional[str] = None
    error: Optional[str] = None
    diff: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    tools_count: int


# ── 端点 ──────────────────────────────────────────────────────

@app.post("/tasks/submit", response_model=SubmitResponse)
async def submit_task(req: SubmitRequest):
    """提交编码任务，立即返回 task_id，后台异步执行。"""
    task = Task(
        user_input=req.input,
        project_dir=req.project_dir,
        model=req.model,
    )
    _event_buffer.append(TaskEvent(task.id, "created", data={
        "user_input": req.input, "project": req.project_dir,
    }))
    if _task_queue is None:
        raise HTTPException(status_code=503, detail="Server not ready")
    _task_queue.submit(task)
    return SubmitResponse(task_id=task.id, status=task.status.value)


@app.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task(task_id: str):
    """查询任务状态和结果。"""
    if _task_queue is None:
        raise HTTPException(status_code=503, detail="Server not ready")
    task = _task_queue.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(
        task_id=task.id,
        status=task.status.value,
        input=task.user_input,
        result=task.result,
        error=task.error,
        diff=task.diff,
    )


@app.delete("/tasks/{task_id}")
async def cancel_task(task_id: str):
    """取消待执行的任务。"""
    if _task_queue is None:
        raise HTTPException(status_code=503, detail="Server not ready")
    ok = _task_queue.cancel(task_id)
    if ok:
        _event_buffer.append(TaskEvent(task_id, "cancelled", data={}))
        return {"task_id": task_id, "cancelled": True}
    return {"task_id": task_id, "cancelled": False}


@app.get("/tasks/{task_id}/events")
async def get_events(task_id: str, cursor: int = Query(default=0)):
    """增量拉取任务事件（实时进度监控）。"""
    events, new_cursor = _event_buffer.get_since(task_id, cursor)
    return {"task_id": task_id, "cursor": new_cursor, "events": events}


@app.get("/health", response_model=HealthResponse)
async def health():
    """健康检查 + 服务器信息。"""
    from tools import TOOL_DEFINITIONS
    return HealthResponse(
        status="ok",
        version="2.0.0",
        tools_count=len(TOOL_DEFINITIONS),
    )


@app.get("/metrics")
async def metrics():
    """Prometheus 格式指标端点。"""
    if _task_queue is None:
        return PlainTextResponse(
            "codepilot_tasks_total 0\ncodepilot_tasks_completed 0\ncodepilot_tasks_failed 0\n",
            media_type="text/plain",
        )
    stats = _task_queue.stats()
    lines = [
        "# HELP codepilot_tasks_total Total tasks submitted",
        "# TYPE codepilot_tasks_total counter",
        f"codepilot_tasks_total {stats['total']}",
        "# HELP codepilot_tasks_completed Total tasks completed",
        "# TYPE codepilot_tasks_completed counter",
        f"codepilot_tasks_completed {stats['completed']}",
        "# HELP codepilot_tasks_failed Total tasks failed",
        "# TYPE codepilot_tasks_failed counter",
        f"codepilot_tasks_failed {stats['failed']}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain")


# ── 启动入口 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("CODEPILOT_HOST", "0.0.0.0")
    port = int(os.getenv("CODEPILOT_PORT", "8000"))
    print(f"码搭 CodePilot API 启动: http://{host}:{port}")
    uvicorn.run("server:app", host=host, port=port, reload=False)
