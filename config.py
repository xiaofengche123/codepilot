"""
码搭 CodePilot · 配置管理

从 config/settings.yaml 加载，不存在时用内置默认值。
支持点号路径: config.get("agent.max_iterations")
"""

import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"

DEFAULTS = {
    "agent": {
        "max_iterations": 10,
        "max_context_tokens": 8000,
    },
    "model": {
        "temperature": 0.3,
        "max_tokens": 4096,
    },
    "tools": {
        "shell_timeout": 30,
        "output_max_chars": 4000,
        "diff_max_chars": 3000,
        "fetch_max_chars": 4000,
    },
    "rag": {
        "model_name": "all-MiniLM-L6-v2",
        "chunk_lines": 30,
    },
    "server": {
        "max_concurrent": 5,
        "host": "0.0.0.0",
        "port": 8000,
    },
    "mcp": {
        "allow_dangerous": False,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并字典，override 覆盖 base。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    def __init__(self):
        self._data: dict = dict(DEFAULTS)
        self._load()

    def _load(self):
        if not CONFIG_PATH.exists():
            return
        try:
            import yaml
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
            self._data = _deep_merge(self._data, user_config)
        except Exception:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        """点号路径取值: config.get('agent.max_iterations')。"""
        parts = key.split(".")
        node = self._data
        for p in parts:
            if isinstance(node, dict):
                node = node.get(p)
                if node is None:
                    return default
            else:
                return default
        return node

    def get_all(self) -> dict:
        return dict(self._data)

    def reload(self):
        self._data = dict(DEFAULTS)
        self._load()


config = Config()
