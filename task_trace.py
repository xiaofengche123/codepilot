"""Bounded, objective task execution trace records.

Trace records stable event metadata only. They never retain model context,
retrieval queries, source content, diffs, shell output, or tool arguments.
"""

from __future__ import annotations

import time
import re
from dataclasses import asdict, dataclass, field
from typing import Any


TRACE_SCHEMA_VERSION = 1
MAX_PHASE_EVENTS = 500
MAX_RETRIEVAL_TRACES = 200
MAX_INSPECTED_FILES = 200
MAX_EDIT_TRACES = 200
MAX_TEST_TRACES = 200
MAX_CHANGED_FILES = 200
MAX_TRACE_PATH_CHARS = 500
MAX_TRACE_IDENTIFIER_CHARS = 128
SENSITIVE_PATH = "[sensitive_path]"
REDACTED = "[redacted]"
_SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9._-]{8,}|bearer\s+\S+|api[_-]?key\s*[=:]\s*\S+)",
    re.I,
)
_RETRIEVED_FILE = re.compile(r"^\s+(.+?):\d+(?::|\s+\()", re.MULTILINE)


def safe_trace_identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if _SECRET.search(value):
        return REDACTED
    return value[:MAX_TRACE_IDENTIFIER_CHARS]


def _sanitize_trace_value(value: Any) -> Any:
    if isinstance(value, str):
        return REDACTED if _SECRET.search(value) else value[:MAX_TRACE_PATH_CHARS]
    if isinstance(value, dict):
        return {str(key)[:80]: _sanitize_trace_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_trace_value(item) for item in value]
    return value


def safe_trace_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("\\", "/")
    parts = [part.lower() for part in normalized.split("/")]
    if (
        _SECRET.search(normalized)
        or any(
            part == ".env" or part.startswith(".env.")
            or "secret" in part or "credential" in part
            for part in parts
        )
    ):
        return SENSITIVE_PATH
    return normalized[:MAX_TRACE_PATH_CHARS]


def extract_trace_result_files(result_text: Any) -> tuple[str, ...]:
    if not isinstance(result_text, str):
        return ()
    files: list[str] = []
    for match in _RETRIEVED_FILE.finditer(result_text):
        path = safe_trace_path(match.group(1))
        if path and path not in files:
            files.append(path)
        if len(files) >= MAX_CHANGED_FILES:
            break
    return tuple(files)


@dataclass(frozen=True)
class PhaseEvent:
    event_type: str
    iteration: int
    phase: str
    from_phase: str | None = None
    to_phase: str | None = None
    reason: str | None = None
    triggering_tool: str | None = None
    success: bool | None = None
    completion_allowed: bool | None = None
    missing_evidence: tuple[str, ...] = ()
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class RetrievalTrace:
    tool_name: str
    iteration: int
    success: bool
    error_code: str | None
    result_files: tuple[str, ...] = ()
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class EditTrace:
    tool_name: str
    path: str | None
    iteration: int
    success: bool
    byte_changed: bool
    rolled_back: bool
    legacy: bool
    revision: int
    error_code: str | None
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class TestTrace:
    tool_name: str
    iteration: int
    returncode: int | None
    success: bool
    revision: int
    error_code: str | None
    timestamp: float = field(default_factory=time.time)


@dataclass
class TaskTrace:
    task_id: str | None = None
    session_id: str | None = None
    phases: list[PhaseEvent] = field(default_factory=list)
    retrieval_calls: list[RetrievalTrace] = field(default_factory=list)
    inspected_files: list[str] = field(default_factory=list)
    edit_attempts: list[EditTrace] = field(default_factory=list)
    test_runs: list[TestTrace] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    final_status: str | None = None
    failure_stage: str | None = None
    secondary_failure_reasons: list[str] = field(default_factory=list)
    failure_domain: str | None = None
    failure_reason_code: str | None = None

    def __post_init__(self) -> None:
        self.task_id = safe_trace_identifier(self.task_id)
        self.session_id = safe_trace_identifier(self.session_id)

    @staticmethod
    def _append_bounded(items: list, item: Any, limit: int) -> None:
        items.append(item)
        if len(items) > limit:
            del items[:-limit]

    def record_iteration(self, iteration: int, phase: str) -> None:
        self._append_bounded(
            self.phases,
            PhaseEvent("iteration_started", iteration, phase),
            MAX_PHASE_EVENTS,
        )

    def record_transition(
        self,
        *,
        from_phase: str,
        to_phase: str,
        reason: str,
        iteration: int,
        triggering_tool: str | None,
        timestamp: float,
    ) -> None:
        self._append_bounded(
            self.phases,
            PhaseEvent(
                "transition", iteration, to_phase, from_phase, to_phase,
                reason, triggering_tool, timestamp=timestamp,
            ),
            MAX_PHASE_EVENTS,
        )
        if to_phase in {"complete", "failed"}:
            self.final_status = to_phase

    def record_completion_decision(
        self,
        *,
        iteration: int,
        phase: str,
        allowed: bool,
        missing_evidence: tuple[str, ...],
    ) -> None:
        self._append_bounded(
            self.phases,
            PhaseEvent(
                "completion_decision", iteration, phase,
                completion_allowed=allowed,
                missing_evidence=missing_evidence,
            ),
            MAX_PHASE_EVENTS,
        )

    def record_review(
        self, *, iteration: int, phase: str, success: bool,
        error_code: str | None,
    ) -> None:
        self._append_bounded(
            self.phases,
            PhaseEvent(
                "review", iteration, phase,
                reason=error_code or "diff_review_recorded",
                triggering_tool="git_diff",
                success=success,
            ),
            MAX_PHASE_EVENTS,
        )

    def record_retrieval(
        self, tool_name: str, iteration: int, success: bool,
        error_code: str | None, result_files: tuple[str, ...] = (),
    ) -> None:
        self._append_bounded(
            self.retrieval_calls,
            RetrievalTrace(
                safe_trace_identifier(tool_name) or "unknown_tool",
                iteration, success, safe_trace_identifier(error_code), result_files,
            ),
            MAX_RETRIEVAL_TRACES,
        )

    def record_inspection(self, path: Any) -> None:
        safe_path = safe_trace_path(path)
        if (
            safe_path
            and safe_path not in self.inspected_files
            and len(self.inspected_files) < MAX_INSPECTED_FILES
        ):
            self.inspected_files.append(safe_path)

    def record_edit(
        self,
        *,
        tool_name: str,
        path: Any,
        iteration: int,
        success: bool,
        byte_changed: bool,
        rolled_back: bool,
        legacy: bool,
        revision: int,
        error_code: str | None,
    ) -> None:
        safe_path = safe_trace_path(path)
        self._append_bounded(
            self.edit_attempts,
            EditTrace(
                tool_name, safe_path, iteration, success, byte_changed,
                rolled_back, legacy, revision, error_code,
            ),
            MAX_EDIT_TRACES,
        )
        if (
            success
            and safe_path
            and safe_path not in self.changed_files
            and len(self.changed_files) < MAX_CHANGED_FILES
        ):
            self.changed_files.append(safe_path)

    def record_test(
        self,
        *,
        tool_name: str,
        iteration: int,
        returncode: int | None,
        success: bool,
        revision: int,
        error_code: str | None,
    ) -> None:
        self._append_bounded(
            self.test_runs,
            TestTrace(
                tool_name, iteration, returncode, success, revision, error_code
            ),
            MAX_TEST_TRACES,
        )

    def snapshot(self) -> dict[str, Any]:
        retrieval_calls = []
        for item in self.retrieval_calls:
            serialized = asdict(item)
            serialized["result_files"] = list(item.result_files)
            retrieval_calls.append(serialized)
        snapshot = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "phases": [asdict(item) for item in self.phases],
            "retrieval_calls": retrieval_calls,
            "inspected_files": list(self.inspected_files),
            "edit_attempts": [asdict(item) for item in self.edit_attempts],
            "test_runs": [asdict(item) for item in self.test_runs],
            "changed_files": list(self.changed_files),
            "final_status": self.final_status,
            "failure_stage": self.failure_stage,
            "secondary_failure_reasons": list(self.secondary_failure_reasons),
            "failure_domain": self.failure_domain,
            "failure_reason_code": self.failure_reason_code,
        }
        return _sanitize_trace_value(snapshot)

    def set_failure_classification(self, classification: dict[str, Any]) -> None:
        self.failure_stage = classification.get("failure_stage")
        reasons = classification.get("secondary_failure_reasons", [])
        self.secondary_failure_reasons = [
            str(item)[:80] for item in reasons if isinstance(item, str)
        ][:20]
        self.failure_domain = safe_trace_identifier(classification.get("failure_domain"))
        self.failure_reason_code = safe_trace_identifier(
            classification.get("failure_reason_code")
        )
