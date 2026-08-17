"""Objective, per-session execution state for the CodePilot Agent loop.

The state machine observes tool outcomes.  It intentionally stores no model
context, source text, tool arguments, or shell output.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class AgentPhase(str, Enum):
    INIT = "init"
    DISCOVER = "discover"
    INSPECT = "inspect"
    PLAN = "plan"
    EDIT = "edit"
    VERIFY = "verify"
    RECOVER = "recover"
    REVIEW = "review"
    COMPLETE = "complete"
    FAILED = "failed"


class TaskMode(str, Enum):
    AUTO = "auto"
    READ_ONLY = "read_only"
    MUTATION_REQUIRED = "mutation_required"


LEGAL_TRANSITIONS: Mapping[AgentPhase, frozenset[AgentPhase]] = {
    AgentPhase.INIT: frozenset({
        AgentPhase.DISCOVER, AgentPhase.INSPECT, AgentPhase.EDIT,
        AgentPhase.VERIFY, AgentPhase.COMPLETE, AgentPhase.FAILED,
    }),
    AgentPhase.DISCOVER: frozenset({
        AgentPhase.INSPECT, AgentPhase.PLAN, AgentPhase.EDIT,
        AgentPhase.VERIFY, AgentPhase.COMPLETE, AgentPhase.FAILED,
    }),
    AgentPhase.INSPECT: frozenset({
        AgentPhase.DISCOVER, AgentPhase.PLAN, AgentPhase.EDIT,
        AgentPhase.VERIFY, AgentPhase.REVIEW, AgentPhase.COMPLETE,
        AgentPhase.FAILED,
    }),
    AgentPhase.PLAN: frozenset({
        AgentPhase.INSPECT, AgentPhase.EDIT, AgentPhase.VERIFY,
        AgentPhase.REVIEW, AgentPhase.COMPLETE, AgentPhase.FAILED,
    }),
    AgentPhase.EDIT: frozenset({
        AgentPhase.VERIFY, AgentPhase.RECOVER, AgentPhase.REVIEW,
        AgentPhase.FAILED,
    }),
    AgentPhase.VERIFY: frozenset({
        AgentPhase.REVIEW, AgentPhase.RECOVER, AgentPhase.EDIT,
        AgentPhase.FAILED,
    }),
    AgentPhase.RECOVER: frozenset({
        AgentPhase.INSPECT, AgentPhase.EDIT, AgentPhase.VERIFY,
        AgentPhase.FAILED,
    }),
    AgentPhase.REVIEW: frozenset({
        AgentPhase.COMPLETE, AgentPhase.EDIT, AgentPhase.VERIFY,
        AgentPhase.FAILED,
    }),
    AgentPhase.COMPLETE: frozenset(),
    AgentPhase.FAILED: frozenset(),
}


@dataclass(frozen=True)
class PhaseBudgets:
    discovery_budget: int | None = 3
    inspect_budget: int | None = 8
    edit_budget: int | None = 3
    verify_budget: int | None = 4
    recovery_budget: int | None = 2

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "PhaseBudgets":
        values = values or {}
        parsed: dict[str, int | None] = {}
        for name, default in cls().__dict__.items():
            value = values.get(name, default)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or null")
            parsed[name] = value
        return cls(**parsed)


@dataclass(frozen=True)
class TransitionRecord:
    from_phase: AgentPhase
    to_phase: AgentPhase
    reason: str
    iteration: int
    triggering_tool: str | None
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class TransitionResult:
    accepted: bool
    error_code: str | None = None
    message: str = ""
    record: TransitionRecord | None = None


@dataclass(frozen=True)
class BudgetDecision:
    category: str
    used: int
    limit: int | None
    remaining: int | None
    exhausted: bool
    terminal_reason: str | None = None


_SEARCH_TOOLS = frozenset({"search_semantic", "search_code"})
_READ_TOOLS = frozenset({"read_file"})
_EDIT_TOOLS = frozenset({"edit_file_transaction", "write_file"})
_TEST_COMMAND = re.compile(r"(?:^|[\\/\s])(?:pytest|py\.test)(?:\s|$)|-m\s+pytest(?:\s|$)", re.I)
_RETURNCODE = re.compile(r"\[returncode\]\s*(-?\d+)", re.I)
_LEGACY_RETURNCODE = re.compile(r"返回码\s*:\s*(-?\d+)")
_ERROR_PREFIXES = ("[错误]", "[超时]", "[拒绝]", "[用户取消]", "[拦截]")
MAX_TRANSITION_HISTORY = 500
MAX_MODIFIED_FILES = 200


@dataclass
class TaskExecutionState:
    session_id: str | None = None
    task_id: str | None = None
    task_mode: TaskMode = TaskMode.AUTO
    current_phase: AgentPhase = AgentPhase.INIT
    iteration: int = 0
    max_iterations: int = 10
    budgets: PhaseBudgets = field(default_factory=PhaseBudgets)
    search_call_count: int = 0
    read_call_count: int = 0
    edit_attempt_count: int = 0
    edit_success_count: int = 0
    verification_count: int = 0
    verification_success_count: int = 0
    recovery_count: int = 0
    modified_files: list[str] = field(default_factory=list)
    last_tool_name: str | None = None
    last_error_code: str | None = None
    last_test_returncode: int | None = None
    transition_history: list[TransitionRecord] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    terminal_reason: str | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.task_mode, str):
            self.task_mode = TaskMode(self.task_mode)
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be positive")

    @property
    def is_terminal(self) -> bool:
        return self.current_phase in {AgentPhase.COMPLETE, AgentPhase.FAILED}

    def begin_iteration(self, iteration: int) -> None:
        with self._lock:
            self.iteration = iteration
            self.updated_at = time.time()

    def transition(
        self,
        to_phase: AgentPhase,
        reason: str,
        triggering_tool: str | None = None,
    ) -> TransitionResult:
        if isinstance(to_phase, str):
            to_phase = AgentPhase(to_phase)
        with self._lock:
            from_phase = self.current_phase
            if to_phase == from_phase:
                return TransitionResult(True, message="phase unchanged")
            if self.is_terminal:
                return TransitionResult(
                    False, "terminal_state", f"cannot leave terminal phase {from_phase.value}"
                )
            if to_phase not in LEGAL_TRANSITIONS[from_phase]:
                self.last_error_code = "invalid_phase_transition"
                self.updated_at = time.time()
                return TransitionResult(
                    False,
                    "invalid_phase_transition",
                    f"illegal transition {from_phase.value}->{to_phase.value}",
                )
            record = TransitionRecord(
                from_phase, to_phase, reason, self.iteration, triggering_tool
            )
            self.current_phase = to_phase
            self.transition_history.append(record)
            if len(self.transition_history) > MAX_TRANSITION_HISTORY:
                del self.transition_history[:-MAX_TRANSITION_HISTORY]
            if to_phase is AgentPhase.RECOVER:
                self.recovery_count += 1
            if to_phase is AgentPhase.COMPLETE:
                self.terminal_reason = reason
            self.updated_at = record.timestamp
            return TransitionResult(True, record=record)

    def fail(self, reason: str, error_code: str | None = None) -> TransitionResult:
        with self._lock:
            if self.is_terminal:
                return TransitionResult(False, "terminal_state", "state is already terminal")
            result = self.transition(AgentPhase.FAILED, reason)
            if result.accepted:
                self.terminal_reason = reason
                self.last_error_code = error_code or reason
            return result

    def budget_decision(self, category: str) -> BudgetDecision:
        fields = {
            "discovery": (self.search_call_count, self.budgets.discovery_budget),
            "inspect": (self.read_call_count, self.budgets.inspect_budget),
            "edit": (self.edit_attempt_count, self.budgets.edit_budget),
            "verify": (self.verification_count, self.budgets.verify_budget),
            "recovery": (self.recovery_count, self.budgets.recovery_budget),
        }
        if category not in fields:
            raise ValueError(f"unknown budget category: {category}")
        used, limit = fields[category]
        remaining = None if limit is None else max(0, limit - used)
        exhausted = limit is not None and used >= limit
        reason = f"{category}_budget_exhausted" if exhausted else None
        return BudgetDecision(category, used, limit, remaining, exhausted, reason)

    def enforce_budget(self, category: str) -> BudgetDecision:
        decision = self.budget_decision(category)
        if decision.exhausted and not self.is_terminal:
            self.fail(decision.terminal_reason or "budget_exhausted")
        return decision

    def observe_tool_result(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None,
        result: Any,
    ) -> None:
        """Update counters and phases from a completed, objective tool event."""
        arguments = arguments if isinstance(arguments, Mapping) else {}
        result_text = result if isinstance(result, str) else str(result)
        with self._lock:
            self.last_tool_name = tool_name
            self.updated_at = time.time()

            if tool_name in _SEARCH_TOOLS:
                self.search_call_count += 1
                self._transition_if_allowed(AgentPhase.DISCOVER, "search_tool_completed", tool_name)
                if result_text.startswith(_ERROR_PREFIXES):
                    self.last_error_code = "search_tool_failed"
                return

            if tool_name in _READ_TOOLS:
                self.read_call_count += 1
                self._transition_if_allowed(AgentPhase.INSPECT, "read_tool_completed", tool_name)
                if result_text.startswith(_ERROR_PREFIXES):
                    self.last_error_code = "read_tool_failed"
                return

            if tool_name in _EDIT_TOOLS:
                self._observe_edit(tool_name, arguments, result_text)
                return

            if tool_name == "run_shell" and self._is_test_command(arguments.get("command")):
                self._observe_verification(tool_name, result_text)

    def _transition_if_allowed(
        self, phase: AgentPhase, reason: str, tool_name: str
    ) -> None:
        if phase is self.current_phase or self.is_terminal:
            return
        self.transition(phase, reason, tool_name)

    def _enter_edit(self, tool_name: str) -> None:
        if self.current_phase is AgentPhase.INSPECT:
            self.transition(AgentPhase.PLAN, "edit_tool_selected", tool_name)
        self._transition_if_allowed(AgentPhase.EDIT, "edit_tool_completed", tool_name)

    def _observe_edit(
        self, tool_name: str, arguments: Mapping[str, Any], result_text: str
    ) -> None:
        self.edit_attempt_count += 1
        self._enter_edit(tool_name)
        success = False
        error_code: str | None = None
        path: str | None = None
        dry_run = arguments.get("dry_run") is True

        if tool_name == "edit_file_transaction":
            try:
                decoded = json.loads(result_text)
            except (TypeError, json.JSONDecodeError):
                decoded = None
            if isinstance(decoded, dict):
                success = decoded.get("success") is True and not dry_run
                error_code = decoded.get("error_code")
                path = decoded.get("path") if isinstance(decoded.get("path"), str) else None
            else:
                error_code = "unparseable_edit_result"
        else:
            success = result_text.startswith("[成功]")
            path = arguments.get("path") if isinstance(arguments.get("path"), str) else None
            if not success:
                error_code = "legacy_write_failed"

        if success:
            self.edit_success_count += 1
            if (
                path
                and path not in self.modified_files
                and len(self.modified_files) < MAX_MODIFIED_FILES
            ):
                self.modified_files.append(path)
            self.last_error_code = None
            return

        self.last_error_code = error_code or "edit_failed"
        self._transition_if_allowed(AgentPhase.RECOVER, "edit_failed", tool_name)

    @staticmethod
    def _is_test_command(command: Any) -> bool:
        return isinstance(command, str) and bool(_TEST_COMMAND.search(command))

    def _observe_verification(self, tool_name: str, result_text: str) -> None:
        self.verification_count += 1
        self._transition_if_allowed(AgentPhase.VERIFY, "test_command_completed", tool_name)
        match = _RETURNCODE.search(result_text) or _LEGACY_RETURNCODE.search(result_text)
        returncode = int(match.group(1)) if match else None
        self.last_test_returncode = returncode
        if returncode == 0:
            self.verification_success_count += 1
            self.last_error_code = None
            self._transition_if_allowed(AgentPhase.REVIEW, "tests_passed", tool_name)
        else:
            self.last_error_code = (
                "test_returncode_unknown" if returncode is None else "tests_failed"
            )
            self._transition_if_allowed(AgentPhase.RECOVER, "tests_failed", tool_name)

    def request_completion(self, reason: str = "agent_finished") -> TransitionResult:
        """Apply deterministic evidence rules; model prose is never evidence."""
        with self._lock:
            if self.is_terminal:
                return TransitionResult(False, "terminal_state", "state is already terminal")
            mode = self.task_mode
            mutation_evidence = self.edit_attempt_count > 0
            if mode is TaskMode.AUTO:
                mode = TaskMode.MUTATION_REQUIRED if mutation_evidence else TaskMode.READ_ONLY

            if mode is TaskMode.MUTATION_REQUIRED:
                missing = []
                if self.edit_success_count < 1:
                    missing.append("successful_edit")
                if self.verification_success_count < 1:
                    missing.append("successful_verification")
                if missing:
                    code = "completion_evidence_missing"
                    self.last_error_code = code
                    self.fail(f"{code}:{','.join(missing)}", code)
                    return TransitionResult(False, code, f"missing evidence: {', '.join(missing)}")
                if self.current_phase is not AgentPhase.REVIEW:
                    review = self.transition(AgentPhase.REVIEW, "completion_evidence_reviewed")
                    if not review.accepted:
                        return review
            return self.transition(AgentPhase.COMPLETE, reason)

    def snapshot(self) -> dict[str, Any]:
        """Return bounded structured state without raw tool/model content."""
        with self._lock:
            return {
                "session_id": self.session_id,
                "task_id": self.task_id,
                "task_mode": self.task_mode.value,
                "current_phase": self.current_phase.value,
                "iteration": self.iteration,
                "max_iterations": self.max_iterations,
                "search_call_count": self.search_call_count,
                "read_call_count": self.read_call_count,
                "edit_attempt_count": self.edit_attempt_count,
                "edit_success_count": self.edit_success_count,
                "verification_count": self.verification_count,
                "verification_success_count": self.verification_success_count,
                "recovery_count": self.recovery_count,
                "modified_files": list(self.modified_files),
                "last_tool_name": self.last_tool_name,
                "last_error_code": self.last_error_code,
                "last_test_returncode": self.last_test_returncode,
                "started_at": self.started_at,
                "updated_at": self.updated_at,
                "terminal_reason": self.terminal_reason,
                "transition_history": [
                    {
                        "from_phase": item.from_phase.value,
                        "to_phase": item.to_phase.value,
                        "reason": item.reason,
                        "iteration": item.iteration,
                        "triggering_tool": item.triggering_tool,
                        "timestamp": item.timestamp,
                    }
                    for item in self.transition_history
                ],
            }
