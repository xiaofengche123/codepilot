"""
码搭 CodePilot · 对话记忆

按项目目录持久化对话历史，只存用户/助手文本对。
读写加进程内锁，避免 server 并发任务写坏 JSON 文件。
"""

import json
import os
import threading
import re
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import HumanMessage, AIMessage

HISTORY_DIR = ".codepilot"
HISTORY_FILE = "history.json"

_lock = threading.Lock()


def _ensure_dir(project_dir: str) -> Path:
    p = Path(project_dir) / HISTORY_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_session_id(session_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", session_id)
    return safe[:120] or "default"


def _get_path(project_dir: str, session_id: str = None) -> Path:
    base = Path(project_dir) / HISTORY_DIR
    if session_id:
        return base / "sessions" / f"{_safe_session_id(session_id)}.json"
    return base / HISTORY_FILE


def load_history(project_dir: str, session_id: str = None) -> list:
    """加载历史，返回 LangChain message 列表。失败时返回空列表。"""
    path = _get_path(project_dir, session_id)
    if not path.exists():
        return []

    try:
        with _lock:
            data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    messages = []
    for turn in data.get("turns", []):
        user = turn.get("user", "")
        assistant = turn.get("assistant", "")
        if user:
            messages.append(HumanMessage(content=user))
        if assistant:
            messages.append(AIMessage(content=assistant))
    return messages


def save_turn(
    project_dir: str,
    user_input: str,
    assistant_answer: str,
    session_id: str = None,
):
    """追加一轮对话。"""
    path = _get_path(project_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        data = {"version": 1, "project": os.path.abspath(project_dir), "turns": []}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["turns"].append({"user": user_input, "assistant": assistant_answer})

        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_history(project_dir: str, session_id: str = None):
    """清除当前项目的历史。"""
    path = _get_path(project_dir, session_id)
    if path.exists():
        path.unlink()


def get_summary(project_dir: str, limit: int = 20, session_id: str = None) -> list[str]:
    """返回最近 N 轮的用户提问列表（用于 /history 展示）。"""
    path = _get_path(project_dir, session_id)
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    turns = data.get("turns", [])
    return [t.get("user", "") for t in turns[-limit:]]
