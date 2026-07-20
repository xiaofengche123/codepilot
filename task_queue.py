"""
码搭 CodePilot · 异步任务队列

asyncio.Queue + asyncio.Semaphore 并发控制。
"""

import asyncio
import time
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task:
    _id_counter = 0

    def __init__(self, user_input: str, project_dir: str, model: str = None):
        Task._id_counter += 1
        self.id = f"task-{Task._id_counter:06d}"
        self.user_input = user_input
        self.project_dir = project_dir
        self.model = model
        self.status = TaskStatus.PENDING
        self.result: Optional[str] = None
        self.error: Optional[str] = None
        self.diff: Optional[str] = None
        self.created_at = time.time()


class TaskQueue:
    def __init__(self, max_concurrent: int = 5):
        self._queue: asyncio.Queue[Task] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[str, Task] = {}
        self._running = False
        self._workers: list[asyncio.Task] = []

    def submit(self, task: Task):
        self._tasks[task.id] = task
        self._queue.put_nowait(task)

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task and task.status == TaskStatus.PENDING:
            task.status = TaskStatus.CANCELLED
            return True
        return False

    async def start(self, executor):
        """启动 Worker 协程，executor(task) 是实际执行函数。"""
        self._running = True
        for _ in range(self._semaphore._value):
            worker = asyncio.create_task(self._worker(executor))
            self._workers.append(worker)

    async def stop(self):
        self._running = False
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    async def _worker(self, executor):
        while self._running:
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if task.status == TaskStatus.CANCELLED:
                continue

            async with self._semaphore:
                task.status = TaskStatus.RUNNING
                try:
                    result = await executor(task)
                    task.status = TaskStatus.COMPLETED
                    task.result = result.get("answer", "")
                    task.diff = result.get("diff", "")
                except Exception as e:
                    task.status = TaskStatus.FAILED
                    task.error = str(e)

    def stats(self) -> dict:
        """返回任务计数快照（供 /metrics 使用）。"""
        total = completed = failed = 0
        for t in self._tasks.values():
            total += 1
            if t.status == TaskStatus.COMPLETED:
                completed += 1
            elif t.status == TaskStatus.FAILED:
                failed += 1
        return {"total": total, "completed": completed, "failed": failed}
