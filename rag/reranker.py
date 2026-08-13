"""Cross-Encoder 精排层。

只处理多路召回后的少量候选，不参与全库召回。模型懒加载并在进程内复用；
加载或推理失败时由调用方决定是否回退到 RRF 排名。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Iterable, Protocol


class RerankHit(Protocol):
    uid: str
    document: str
    metadata: dict
    rerank_score: float | None
    rrf_rank: int | None


@dataclass(frozen=True)
class RerankerStatus:
    enabled: bool
    loaded: bool
    model_name: str
    error: str | None = None


_model = None
_model_name = ""
_load_error: str | None = None
_load_lock = threading.Lock()
_predict_lock = threading.Lock()


def _settings() -> dict:
    from config import PROJECT_ROOT, config

    configured_cache = str(
        config.get("rag.reranker.cache_folder", ".codepilot/model-cache")
    )
    cache_path = Path(configured_cache)
    if not cache_path.is_absolute():
        cache_path = PROJECT_ROOT / cache_path

    return {
        "enabled": bool(config.get("rag.reranker.enabled", True)),
        "model_name": str(config.get(
            "rag.reranker.model_name",
            "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        )),
        "batch_size": max(1, int(config.get("rag.reranker.batch_size", 16))),
        "max_length": max(64, int(config.get("rag.reranker.max_length", 512))),
        "device": config.get("rag.reranker.device", None),
        "cache_folder": str(cache_path),
        "local_files_only": bool(
            config.get("rag.reranker.local_files_only", True)
        ),
    }


def _load_model(local_files_only: bool | None = None):
    global _model, _model_name, _load_error
    settings = _settings()
    wanted = settings["model_name"]
    if _model is not None and _model_name == wanted:
        return _model
    if _load_error and _model_name == wanted:
        raise RuntimeError(_load_error)

    with _load_lock:
        if _model is not None and _model_name == wanted:
            return _model
        if _load_error and _model_name == wanted:
            raise RuntimeError(_load_error)
        _model_name = wanted
        try:
            from sentence_transformers import CrossEncoder

            _model = CrossEncoder(
                wanted,
                device=settings["device"],
                max_length=settings["max_length"],
                cache_folder=settings["cache_folder"],
                local_files_only=(
                    settings["local_files_only"]
                    if local_files_only is None else local_files_only
                ),
            )
            _load_error = None
            return _model
        except Exception as exc:
            _model = None
            _load_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(_load_error) from exc


def _candidate_text(hit: RerankHit) -> str:
    """把路径与行号加入候选，让模型能利用代码定位信息。"""
    file = hit.metadata.get("file", "")
    start = hit.metadata.get("start_line", "")
    end = hit.metadata.get("end_line", "")
    return f"file: {file}\nlines: {start}-{end}\n{hit.document}"


def rerank(query: str, hits: Iterable[RerankHit], limit: int) -> list[RerankHit]:
    """对 ``(query, chunk)`` 成对打分并稳定排序。"""
    candidates = list(hits)
    if not candidates or limit <= 0:
        return []

    model = _load_model()
    settings = _settings()
    pairs = [(query, _candidate_text(hit)) for hit in candidates]
    # PyTorch 模型的 forward 不是所有后端/设备上都能安全并发；单进程复用时串行
    # predict，外层召回仍可并行。
    with _predict_lock:
        scores = model.predict(
            pairs,
            batch_size=settings["batch_size"],
            show_progress_bar=False,
            convert_to_numpy=True,
        )

    if len(scores) != len(candidates):
        raise RuntimeError(
            f"reranker returned {len(scores)} scores for {len(candidates)} candidates"
        )
    for rank, (hit, score) in enumerate(zip(candidates, scores), start=1):
        # 正常调用来自 RRF，保留召回阶段写入的原始名次。直接调用时仍提供
        # 一个稳定的输入顺序名次，便于独立使用和测试。
        if hit.rrf_rank is None:
            hit.rrf_rank = rank
        hit.rerank_score = float(score)
    return sorted(
        candidates,
        key=lambda hit: (
            -(hit.rerank_score if hit.rerank_score is not None else float("-inf")),
            hit.rrf_rank or 0,
            hit.uid,
        ),
    )[:limit]


def status() -> RerankerStatus:
    settings = _settings()
    return RerankerStatus(
        enabled=settings["enabled"],
        loaded=_model is not None and _model_name == settings["model_name"],
        model_name=settings["model_name"],
        error=_load_error if _model_name == settings["model_name"] else None,
    )


def reset_for_tests() -> None:
    global _model, _model_name, _load_error
    with _load_lock:
        _model = None
        _model_name = ""
        _load_error = None


def main() -> None:
    """显式检查或下载模型，避免首个在线查询承担下载成本。"""
    import argparse

    parser = argparse.ArgumentParser(description="准备 CodePilot Cross-Encoder")
    parser.add_argument(
        "--download",
        action="store_true",
        help="允许从模型仓库下载；默认只检查本地缓存",
    )
    args = parser.parse_args()
    started = __import__("time").perf_counter()
    try:
        _load_model(local_files_only=not args.download)
    except Exception as exc:
        raise SystemExit(f"[失败] Reranker 不可用: {exc}") from exc
    elapsed = __import__("time").perf_counter() - started
    current = status()
    print(f"[完成] {current.model_name} 已就绪，加载耗时 {elapsed:.1f}s")


if __name__ == "__main__":
    main()
