"""Thread-safe, content-free RAG runtime metrics for health adapters."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import threading


SEARCH_MODES = ("vector", "bm25", "hybrid", "rerank", "unknown")
RERANK_FALLBACK_REASONS = (
    "rerank_queue_full",
    "rerank_queue_closed",
    "rerank_timeout",
    "rerank_load_error",
    "rerank_inference_error",
    "rerank_circuit_open",
    "rerank_recovery_probe_in_progress",
    "rerank_unexpected_error",
)


@dataclass(frozen=True, slots=True)
class RagMetricsSnapshot:
    retrieval_count: tuple[tuple[str, int], ...]
    retrieval_latency_ms_sum: tuple[tuple[str, float], ...]
    rerank_count: int
    rerank_latency_ms_sum: float
    fallback_total: tuple[tuple[str, int], ...]
    timeout_total: int
    model_load_count: int
    model_load_seconds_sum: float
    search_mode_total: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict:
        return {
            "retrieval_count": dict(self.retrieval_count),
            "retrieval_latency_ms_sum": dict(self.retrieval_latency_ms_sum),
            "rerank_count": self.rerank_count,
            "rerank_latency_ms_sum": self.rerank_latency_ms_sum,
            "fallback_total": dict(self.fallback_total),
            "timeout_total": self.timeout_total,
            "model_load_count": self.model_load_count,
            "model_load_seconds_sum": self.model_load_seconds_sum,
            "search_mode_total": dict(self.search_mode_total),
        }


class _RagRuntimeMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._retrieval_count: Counter[str] = Counter()
            self._retrieval_latency_ms_sum: Counter[str] = Counter()
            self._rerank_count = 0
            self._rerank_latency_ms_sum = 0.0
            self._fallback_total: Counter[str] = Counter()
            self._timeout_total = 0
            self._model_load_count = 0
            self._model_load_seconds_sum = 0.0
            self._search_mode_total: Counter[str] = Counter()

    def observe_retrieval(self, mode: str, elapsed_seconds: float) -> None:
        normalized = _normalize_mode(mode)
        elapsed_ms = _non_negative_finite(elapsed_seconds) * 1_000.0
        with self._lock:
            self._retrieval_count[normalized] += 1
            self._retrieval_latency_ms_sum[normalized] += elapsed_ms
            self._search_mode_total[normalized] += 1

    def observe_rerank(self, elapsed_seconds: float) -> None:
        elapsed_ms = _non_negative_finite(elapsed_seconds) * 1_000.0
        with self._lock:
            self._rerank_count += 1
            self._rerank_latency_ms_sum += elapsed_ms

    def record_fallback(self, reason_code: str) -> None:
        reason = (
            reason_code
            if reason_code in RERANK_FALLBACK_REASONS
            else "rerank_unexpected_error"
        )
        with self._lock:
            self._fallback_total[reason] += 1
            if reason == "rerank_timeout":
                self._timeout_total += 1

    def observe_model_load(self, elapsed_seconds: float) -> None:
        elapsed = _non_negative_finite(elapsed_seconds)
        with self._lock:
            self._model_load_count += 1
            self._model_load_seconds_sum += elapsed

    def snapshot(self) -> RagMetricsSnapshot:
        with self._lock:
            return RagMetricsSnapshot(
                retrieval_count=tuple(
                    (mode, self._retrieval_count[mode]) for mode in SEARCH_MODES
                ),
                retrieval_latency_ms_sum=tuple(
                    (mode, float(self._retrieval_latency_ms_sum[mode]))
                    for mode in SEARCH_MODES
                ),
                rerank_count=self._rerank_count,
                rerank_latency_ms_sum=self._rerank_latency_ms_sum,
                fallback_total=tuple(
                    (reason, self._fallback_total[reason])
                    for reason in RERANK_FALLBACK_REASONS
                ),
                timeout_total=self._timeout_total,
                model_load_count=self._model_load_count,
                model_load_seconds_sum=self._model_load_seconds_sum,
                search_mode_total=tuple(
                    (mode, self._search_mode_total[mode]) for mode in SEARCH_MODES
                ),
            )


_metrics = _RagRuntimeMetrics()


def observe_retrieval(mode: str, elapsed_seconds: float) -> None:
    _metrics.observe_retrieval(mode, elapsed_seconds)


def observe_rerank(elapsed_seconds: float) -> None:
    _metrics.observe_rerank(elapsed_seconds)


def record_rerank_fallback(reason_code: str) -> None:
    _metrics.record_fallback(reason_code)


def observe_model_load(elapsed_seconds: float) -> None:
    _metrics.observe_model_load(elapsed_seconds)


def metrics_snapshot() -> RagMetricsSnapshot:
    return _metrics.snapshot()


def reset_metrics_for_tests() -> None:
    _metrics.reset()


def prometheus_lines(queue_size: int) -> list[str]:
    snapshot = metrics_snapshot()
    retrieval_count = dict(snapshot.retrieval_count)
    retrieval_sum = dict(snapshot.retrieval_latency_ms_sum)
    search_total = dict(snapshot.search_mode_total)
    fallback_total = dict(snapshot.fallback_total)
    lines = [
        "# HELP rag_retrieval_latency_ms Retrieval latency in milliseconds",
        "# TYPE rag_retrieval_latency_ms summary",
    ]
    for mode in SEARCH_MODES:
        lines.extend([
            f'rag_retrieval_latency_ms_count{{mode="{mode}"}} {retrieval_count[mode]}',
            f'rag_retrieval_latency_ms_sum{{mode="{mode}"}} {retrieval_sum[mode]:.6f}',
        ])
    lines.extend([
        "# HELP rag_rerank_latency_ms Rerank worker latency in milliseconds",
        "# TYPE rag_rerank_latency_ms summary",
        f"rag_rerank_latency_ms_count {snapshot.rerank_count}",
        f"rag_rerank_latency_ms_sum {snapshot.rerank_latency_ms_sum:.6f}",
        "# HELP rag_rerank_queue_size Current rerank queue size",
        "# TYPE rag_rerank_queue_size gauge",
        f"rag_rerank_queue_size {max(0, int(queue_size))}",
        "# HELP rag_rerank_fallback_total Rerank fallbacks by stable reason",
        "# TYPE rag_rerank_fallback_total counter",
    ])
    for reason in RERANK_FALLBACK_REASONS:
        lines.append(
            f'rag_rerank_fallback_total{{reason="{reason}"}} '
            f"{fallback_total[reason]}"
        )
    lines.extend([
        "# HELP rag_rerank_timeout_total Rerank caller deadline expirations",
        "# TYPE rag_rerank_timeout_total counter",
        f"rag_rerank_timeout_total {snapshot.timeout_total}",
        "# HELP rag_model_load_seconds Rerank model load duration in seconds",
        "# TYPE rag_model_load_seconds summary",
        f"rag_model_load_seconds_count {snapshot.model_load_count}",
        f"rag_model_load_seconds_sum {snapshot.model_load_seconds_sum:.6f}",
        "# HELP rag_search_mode_total Retrieval calls by bounded mode",
        "# TYPE rag_search_mode_total counter",
    ])
    for mode in SEARCH_MODES:
        lines.append(f'rag_search_mode_total{{mode="{mode}"}} {search_total[mode]}')
    return lines


def _normalize_mode(mode: str) -> str:
    return mode if mode in SEARCH_MODES[:-1] else "unknown"


def _non_negative_finite(value: float) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValueError("elapsed time must be finite and non-negative")
    return normalized
