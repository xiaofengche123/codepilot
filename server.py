"""
码搭 CodePilot · FastAPI 服务

启动: python server.py
      uvicorn server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import os
import threading
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import config
from task_queue import Task, TaskQueue
from worktree_manager import WorktreeManager
from events import get_event_buffer, TaskEvent

# ── 调度器封装 ────────────────────────────────────────────────

_task_queue: Optional[TaskQueue] = None
_event_buffer = get_event_buffer()
_worktree_managers: dict[str, WorktreeManager] = {}
_worktree_managers_lock = threading.Lock()


def _get_worktree_manager(project_dir: str) -> WorktreeManager:
    root = str(Path(project_dir).resolve())
    with _worktree_managers_lock:
        manager = _worktree_managers.get(root)
        if manager is None:
            manager = WorktreeManager(root)
            _worktree_managers[root] = manager
        return manager


async def _executor(task: Task):
    """后台执行一个 Agent 任务（在独立 worktree 中）。"""
    from agent import AgentSession
    from model_router import get_router

    # 事件：开始
    _event_buffer.append(TaskEvent(task.id, "started", data={
        "user_input": task.user_input, "project": task.project_dir,
    }))

    # 创建隔离工作区；失败时拒绝执行，绝不降级污染原项目。
    worktree_mgr = _get_worktree_manager(task.project_dir)
    worktree_path = worktree_mgr.create(task.id)
    if not worktree_path:
        _event_buffer.append(TaskEvent(task.id, "failed", data={
            "error": f"无法创建 Git Worktree: {task.project_dir}",
        }))
        raise RuntimeError(
            f"无法为任务创建 Git Worktree，已拒绝在原工作区执行: {task.project_dir}"
        )

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

    def on_stream(chunk: str):
        _event_buffer.append(TaskEvent(task.id, "stream", data={"content": chunk}))

    try:
        wd = worktree_path
        # 无论是否显式指定模型，每个任务都创建独立 LLM 实例。
        model_name = task.model or get_router().default_name()
        session = AgentSession(
            working_dir=wd,
            memory_dir=task.project_dir,
            session_id=task.session_id,
            task_id=task.id,
            model_name=model_name,
            confirm=confirm_dangerous,
        )
        if session.model_unavailable:
            raise RuntimeError(f"模型不可用或未配置 API Key: {model_name}")

        # session.run 是同步阻塞调用，放到线程池执行，
        # 否则会卡住事件循环，所有并发任务和 HTTP 请求都被阻塞
        answer = await asyncio.to_thread(
            session.run,
            task.user_input,
            on_tool,
            on_stream,
        )

        diff = worktree_mgr.collect_diff(worktree_path)

        _event_buffer.append(TaskEvent(task.id, "completed", data={
            "answer": answer[:200], "diff": diff,
        }))
        return {"answer": answer, "diff": diff}

    except Exception as e:
        _event_buffer.append(TaskEvent(task.id, "failed", data={"error": str(e)}))
        raise
    finally:
        if worktree_path and worktree_path != task.project_dir:
            worktree_mgr.cleanup(worktree_path, task.id)


# ── FastAPI 生命周期 ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _task_queue
    max_workers = config.get("server.max_concurrent", 5)
    _task_queue = TaskQueue(max_concurrent=max_workers)
    await _task_queue.start(_executor)
    yield
    await _task_queue.stop()
    for manager in list(_worktree_managers.values()):
        manager.cleanup_all()


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
    session_id: Optional[str] = Field(
        default=None,
        description="会话标识；相同项目和 session_id 共享持久化对话历史",
    )


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
    project_dir = Path(req.project_dir).expanduser().resolve()
    if not project_dir.is_dir():
        raise HTTPException(status_code=400, detail="project_dir 不存在或不是目录")
    manager = _get_worktree_manager(str(project_dir))
    if not manager.is_git_repository:
        raise HTTPException(
            status_code=400,
            detail="project_dir 必须位于 Git 仓库中，服务端不会降级到原工作区执行",
        )
    task = Task(
        user_input=req.input,
        project_dir=str(manager.repo_root),
        model=req.model,
        session_id=req.session_id,
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
        "# HELP codepilot_tasks_running Currently running tasks",
        "# TYPE codepilot_tasks_running gauge",
        f"codepilot_tasks_running {stats['running']}",
        "# HELP codepilot_tasks_pending Currently pending tasks",
        "# TYPE codepilot_tasks_pending gauge",
        f"codepilot_tasks_pending {stats['pending']}",
        "# HELP codepilot_tasks_cancelled Total cancelled tasks",
        "# TYPE codepilot_tasks_cancelled counter",
        f"codepilot_tasks_cancelled {stats['cancelled']}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain")


# ── 启动入口 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("CODEPILOT_HOST", "0.0.0.0")
    port = int(os.getenv("CODEPILOT_PORT", "8000"))
    print(f"码搭 CodePilot API 启动: http://{host}:{port}")
    uvicorn.run("server:app", host=host, port=port, reload=False)
