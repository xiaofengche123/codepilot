"""
码搭 CodePilot · 模型路由

手动 /model 切换，支持 DeepSeek / Claude / OpenAI。
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

from config import config

load_dotenv()


@dataclass
class ModelInfo:
    name: str
    provider: str
    cost_tier: str
    env_key: str
    base_url_env: str
    model_id: str
    display_name: str


DEFAULT_MODELS = [
    ModelInfo(
        name="deepseek-chat",
        provider="deepseek",
        cost_tier="budget",
        env_key="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
        model_id="deepseek-chat",
        display_name="DeepSeek Chat (V3)",
    ),
    ModelInfo(
        name="deepseek-reasoner",
        provider="deepseek",
        cost_tier="budget",
        env_key="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
        model_id="deepseek-reasoner",
        display_name="DeepSeek Reasoner (R1)",
    ),
    ModelInfo(
        name="claude-sonnet-4-6",
        provider="anthropic",
        cost_tier="premium",
        env_key="ANTHROPIC_API_KEY",
        base_url_env="ANTHROPIC_BASE_URL",
        model_id="claude-sonnet-4-6",
        display_name="Claude Sonnet 4.6",
    ),
    ModelInfo(
        name="gpt-4o",
        provider="openai",
        cost_tier="premium",
        env_key="OPENAI_API_KEY",
        base_url_env="OPENAI_BASE_URL",
        model_id="gpt-4o",
        display_name="GPT-4o",
    ),
]


class ModelRouter:
    def __init__(self):
        self._models: dict[str, ModelInfo] = {}
        self._active_llm = None
        self._active_name: str = ""
        for m in DEFAULT_MODELS:
            self._models[m.name] = m

    def _check_available(self, info: ModelInfo) -> bool:
        key = os.getenv(info.env_key, "").strip()
        return bool(key) and "xxx" not in key

    def _create_llm(self, name: str):
        info = self._models[name]
        key = os.getenv(info.env_key, "").strip()
        base_url = os.getenv(info.base_url_env, "").strip()

        if info.provider == "anthropic":
            return ChatAnthropic(
                model=info.model_id,
                api_key=key,
                base_url=base_url or "https://api.anthropic.com",
                temperature=config.get("model.temperature", 0.3),
                max_tokens=config.get("model.max_tokens", 4096),
            )

        default_base = "https://api.deepseek.com" if info.provider == "deepseek" else "https://api.openai.com"
        return ChatOpenAI(
            model=info.model_id,
            api_key=key,
            base_url=base_url or default_base,
            temperature=config.get("model.temperature", 0.3),
            max_tokens=config.get("model.max_tokens", 4096),
        )

    def _auto_select(self):
        for info in DEFAULT_MODELS:
            if self._check_available(info):
                self._active_llm = self._create_llm(info.name)
                self._active_name = info.name
                return
        raise RuntimeError(
            "未找到有效的 API Key，请在 .env 中配置 ANTHROPIC_API_KEY、"
            "DEEPSEEK_API_KEY 或 OPENAI_API_KEY"
        )

    def get_llm(self):
        if self._active_llm is None:
            self._auto_select()
        return self._active_llm

    def get_current(self) -> str:
        if self._active_name:
            return self._active_name
        for info in DEFAULT_MODELS:
            if self._check_available(info):
                return info.name
        return "none"

    def switch(self, name: str) -> bool:
        if name not in self._models:
            return False
        if not self._check_available(self._models[name]):
            return False
        self._active_llm = self._create_llm(name)
        self._active_name = name
        return True

    def list_models(self) -> list[dict]:
        result = []
        for info in DEFAULT_MODELS:
            available = self._check_available(info)
            result.append({
                "name": info.name,
                "provider": info.provider,
                "cost_tier": info.cost_tier,
                "display_name": info.display_name,
                "available": available,
                "current": info.name == self._active_name,
            })
        return result


_router: Optional[ModelRouter] = None


def get_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router


def get_llm():
    return get_router().get_llm()


def switch_model(name: str) -> bool:
    return get_router().switch(name)


def get_current() -> str:
    return get_router().get_current()


def list_models() -> list[dict]:
    return get_router().list_models()
