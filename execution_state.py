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

from task_trace import TaskTrace, extract_trace_result_files
from trace_analysis import analyze_trace_failure


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


class RecoveryAction(str, Enum):
    REREAD_TARGET = "reread_target"
    REINSPECT_MATCH = "reinspect_match"
    REVISE_EDIT = "revise_edit"
    ANALYZE_TEST_FAILURE = "analyze_test_failure"
    RETRY_VERIFICATION = "retry_verification"
    REQUEST_APPROVAL = "request_approval"
    ABORT = "abort"


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    target_phase: AgentPhase
    reason_code: str
    recoverable: bool
    directive: str


@dataclass(frozen=True)
class CompletionDecision:
    allowed: bool
    missing_evidence: tuple[str, ...] = ()
    target_phase: AgentPhase | None = None
    directive: str | None = None
    terminal_reason: str | None = None


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
_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
MAX_TRANSITION_HISTORY = 500
MAX_MODIFIED_FILES = 200
MAX_REVIEWED_FILES = 200
MAX_CONTROL_DIRECTIVE_CHARS = 500

_RECOVERY_TEMPLATES: Mapping[RecoveryAction, str] = {
    RecoveryAction.REREAD_TARGET: (
        "Recovery control: reread the target file, obtain its current content and hash, "
        "then prepare a fresh edit. Do not reuse stale edit preconditions."
    ),
    RecoveryAction.REINSPECT_MATCH: (
        "Recovery control: inspect the current target and locate an exact, unique match "
        "before submitting a revised transaction."
    ),
    RecoveryAction.REVISE_EDIT: (
        "Recovery control: revise the proposed edit so the resulting source is syntactically "
        "valid, then submit a new bounded edit transaction."
    ),
    RecoveryAction.ANALYZE_TEST_FAILURE: (
        "Recovery control: inspect the failing test and relevant implementation, identify the "
        "objective failure, then revise the code before rerunning verification."
    ),
    RecoveryAction.RETRY_VERIFICATION: (
        "Recovery control: verification did not produce a usable result. Check the bounded test "
        "command and environment, then retry verification once evidence is available."
    ),
    RecoveryAction.REQUEST_APPROVAL: (
        "Recovery control: verification requires a dangerous tool that was not approved. "
        "Do not claim tests passed; request an approved verification channel."
    ),
    RecoveryAction.ABORT: (
        "Recovery control: the failure is not safely recoverable in this task. Stop mutation "
        "attempts and report the stable failure reason."
    ),
}


def _normalize_error_code(value: Any, fallback: str = "edit_failed") -> str:
    return value if isinstance(value, str) and _SAFE_ERROR_CODE.fullmatch(value) else fallback


def recovery_decision_for_edit(
    error_code: str | None, *, rolled_back: bool = False
) -> RecoveryDecision:
    """Map a structured edit failure to a bounded deterministic recovery action."""
    code = _normalize_error_code(error_code)
    if code in {"sha_mismatch", "concurrent_modification"}:
        action, target, recoverable = RecoveryAction.REREAD_TARGET, AgentPhase.INSPECT, True
    elif code in {"match_count_mismatch", "overlapping_edits", "empty_match"}:
        action, target, recoverable = RecoveryAction.REINSPECT_MATCH, AgentPhase.INSPECT, True
    elif code == "python_syntax_error":
        action, target, recoverable = RecoveryAction.REVISE_EDIT, AgentPhase.EDIT, True
    elif code == "write_verification_failed" and rolled_back:
        action, target, recoverable = RecoveryAction.REREAD_TARGET, AgentPhase.INSPECT, True
    else:
        action, target, recoverable = RecoveryAction.ABORT, AgentPhase.FAILED, False
    return RecoveryDecision(action, target, code, recoverable, _RECOVERY_TEMPLATES[action])


def recovery_decision_for_verification(result_text: str, returncode: int | None) -> RecoveryDecision:
    """Classify verification failure without retaining or copying command output."""
    if result_text.startswith(("[拒绝]", "[用户取消]", "[拦截]")):
        action = RecoveryAction.REQUEST_APPROVAL
        return RecoveryDecision(
            action, AgentPhase.FAILED, "verification_unavailable", False,
            _RECOVERY_TEMPLATES[action],
        )
    if result_text.startswith("[超时]") or returncode is None:
        action = RecoveryAction.RETRY_VERIFICATION
        reason = "verification_timeout" if result_text.startswith("[超时]") else "verification_result_unknown"
        return RecoveryDecision(
            action, AgentPhase.VERIFY, reason, True, _RECOVERY_TEMPLATES[action]
        )
    action = RecoveryAction.ANALYZE_TEST_FAILURE
    return RecoveryDecision(
        action, AgentPhase.INSPECT, "tests_failed", True, _RECOVERY_TEMPLATES[action]
    )


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
    review_count: int = 0
    mutation_revision: int = 0
    verified_revision: int | None = None
    reviewed_revision: int | None = None
    legacy_mutation_count: int = 0
    modified_files: list[str] = field(default_factory=list)
    reviewed_files: list[str] = field(default_factory=list)
    last_tool_name: str | None = None
    last_error_code: str | None = None
    last_test_returncode: int | None = None
    pending_directive: str | None = None
    pending_recovery: RecoveryDecision | None = None
    transition_history: list[TransitionRecord] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    terminal_reason: str | None = None
    trace: TaskTrace = field(init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.task_mode, str):
            self.task_mode = TaskMode(self.task_mode)
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        self.trace = TaskTrace(task_id=self.task_id, session_id=self.session_id)

    @property
    def is_terminal(self) -> bool:
        return self.current_phase in {AgentPhase.COMPLETE, AgentPhase.FAILED}

    def begin_iteration(self, iteration: int) -> None:
        with self._lock:
            self.iteration = iteration
            self.updated_at = time.time()
            self.trace.record_iteration(iteration, self.current_phase.value)

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
            self.trace.record_transition(
                from_phase=from_phase.value,
                to_phase=to_phase.value,
                reason=reason,
                iteration=self.iteration,
                triggering_tool=triggering_tool,
                timestamp=record.timestamp,
            )
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
                self.trace.set_failure_classification(analyze_trace_failure(
                    self.trace.snapshot(), reason, self.last_error_code
                ))
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

    @staticmethod
    def tool_budget_category(
        tool_name: str, arguments: Mapping[str, Any] | None = None
    ) -> str | None:
        arguments = arguments if isinstance(arguments, Mapping) else {}
        if tool_name in _SEARCH_TOOLS:
            return "discovery"
        if tool_name in _READ_TOOLS:
            return "inspect"
        if tool_name in _EDIT_TOOLS:
            return "edit"
        if tool_name == "run_shell" and TaskExecutionState._is_test_command(
            arguments.get("command")
        ):
            return "verify"
        return None

    def reject_tool(self, error_code: str) -> None:
        """Record a scheduler rejection without treating it as a tool outcome."""
        with self._lock:
            self.last_error_code = error_code
            self.updated_at = time.time()
            if not self.is_terminal:
                self.fail(error_code, error_code)

    def consume_pending_directive(self) -> str | None:
        """Apply a queued recovery target and return one transient control directive."""
        with self._lock:
            directive = self.pending_directive
            recovery = self.pending_recovery
            self.pending_directive = None
            self.pending_recovery = None
            if (
                recovery is not None
                and recovery.recoverable
                and self.current_phase is AgentPhase.RECOVER
            ):
                result = self.transition(
                    recovery.target_phase,
                    f"recovery_action:{recovery.action.value}",
                )
                if not result.accepted:
                    self.fail("recovery_transition_failed", "recovery_transition_failed")
                    return None
            return directive

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
                failed = result_text.startswith(_ERROR_PREFIXES)
                self.last_error_code = "search_tool_failed" if failed else None
                self.trace.record_retrieval(
                    tool_name, self.iteration, not failed, self.last_error_code,
                    extract_trace_result_files(result_text),
                )
                return

            if tool_name in _READ_TOOLS:
                self.read_call_count += 1
                self._transition_if_allowed(AgentPhase.INSPECT, "read_tool_completed", tool_name)
                failed = result_text.startswith(_ERROR_PREFIXES)
                self.last_error_code = "read_tool_failed" if failed else None
                if not failed:
                    self.trace.record_inspection(arguments.get("path"))
                return

            if tool_name in _EDIT_TOOLS:
                self._observe_edit(tool_name, arguments, result_text)
                return

            if tool_name == "run_shell" and self._is_test_command(arguments.get("command")):
                self._observe_verification(tool_name, result_text)
                return

            if tool_name == "git_diff":
                self._observe_review(tool_name, result_text)

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
        rolled_back = False
        byte_changed = False

        if tool_name == "edit_file_transaction":
            try:
                decoded = json.loads(result_text)
            except (TypeError, json.JSONDecodeError):
                decoded = None
            if isinstance(decoded, dict):
                rolled_back = decoded.get("rolled_back") is True
                before_sha = decoded.get("before_sha256")
                after_sha = decoded.get("after_sha256")
                byte_changed = (
                    isinstance(before_sha, str)
                    and isinstance(after_sha, str)
                    and bool(before_sha)
                    and before_sha != after_sha
                )
                success = (
                    decoded.get("success") is True
                    and not dry_run
                    and not rolled_back
                    and byte_changed
                )
                error_code = (
                    _normalize_error_code(decoded.get("error_code"))
                    if decoded.get("error_code") is not None else None
                )
                path = (
                    decoded.get("path")[:500]
                    if isinstance(decoded.get("path"), str) else None
                )
                if decoded.get("success") is True and not success and error_code is None:
                    error_code = "edit_no_byte_change"
            else:
                error_code = "unparseable_edit_result"
        else:
            success = result_text.startswith("[成功]")
            byte_changed = success
            path = (
                arguments.get("path")[:500]
                if isinstance(arguments.get("path"), str) else None
            )
            if not success:
                error_code = "legacy_write_failed"

        if success:
            self.edit_success_count += 1
            self.mutation_revision += 1
            self.verified_revision = None
            self.reviewed_revision = None
            self.reviewed_files.clear()
            if tool_name == "write_file":
                self.legacy_mutation_count += 1
            if (
                path
                and path not in self.modified_files
                and len(self.modified_files) < MAX_MODIFIED_FILES
            ):
                self.modified_files.append(path)
            self.last_error_code = None
            self.trace.record_edit(
                tool_name=tool_name,
                path=path,
                iteration=self.iteration,
                success=True,
                byte_changed=byte_changed,
                rolled_back=rolled_back,
                legacy=tool_name == "write_file",
                revision=self.mutation_revision,
                error_code=None,
            )
            return

        # A successful dry-run or no-op is an edit attempt, not a failed mutation.
        if error_code == "edit_no_byte_change":
            self.last_error_code = error_code
            self.trace.record_edit(
                tool_name=tool_name, path=path, iteration=self.iteration,
                success=False, byte_changed=byte_changed,
                rolled_back=rolled_back, legacy=tool_name == "write_file",
                revision=self.mutation_revision, error_code=error_code,
            )
            return

        self.last_error_code = error_code or "edit_failed"
        self.trace.record_edit(
            tool_name=tool_name, path=path, iteration=self.iteration,
            success=False, byte_changed=byte_changed,
            rolled_back=rolled_back, legacy=tool_name == "write_file",
            revision=self.mutation_revision, error_code=self.last_error_code,
        )
        decision = recovery_decision_for_edit(
            self.last_error_code, rolled_back=rolled_back
        )
        self._handle_recovery(decision, tool_name)

    @staticmethod
    def _is_test_command(command: Any) -> bool:
        return isinstance(command, str) and bool(_TEST_COMMAND.search(command))

    def _observe_verification(self, tool_name: str, result_text: str) -> None:
        self.verification_count += 1
        # A review performed before the latest verification cannot be final review evidence.
        self.reviewed_revision = None
        self.reviewed_files.clear()
        self._transition_if_allowed(AgentPhase.VERIFY, "test_command_completed", tool_name)
        match = _RETURNCODE.search(result_text) or _LEGACY_RETURNCODE.search(result_text)
        returncode = int(match.group(1)) if match else None
        self.last_test_returncode = returncode
        if returncode == 0:
            self.verification_success_count += 1
            self.verified_revision = self.mutation_revision
            self.last_error_code = None
            self.trace.record_test(
                tool_name=tool_name, iteration=self.iteration,
                returncode=returncode, success=True,
                revision=self.mutation_revision, error_code=None,
            )
            self._transition_if_allowed(AgentPhase.REVIEW, "tests_passed", tool_name)
        else:
            self.verified_revision = None
            decision = recovery_decision_for_verification(result_text, returncode)
            self.last_error_code = decision.reason_code
            self.trace.record_test(
                tool_name=tool_name, iteration=self.iteration,
                returncode=returncode, success=False,
                revision=self.mutation_revision,
                error_code=decision.reason_code,
            )
            self._handle_recovery(decision, tool_name)

    def _handle_recovery(
        self, decision: RecoveryDecision, tool_name: str | None = None
    ) -> None:
        if not decision.recoverable:
            terminal = (
                "verification_unavailable"
                if decision.action is RecoveryAction.REQUEST_APPROVAL
                else "unrecoverable_edit_failure"
            )
            self.fail(terminal, decision.reason_code)
            return
        budget = self.budgets.recovery_budget
        if budget is not None and self.recovery_count >= budget:
            self.fail("recovery_budget_exhausted", "recovery_budget_exhausted")
            return
        result = self.transition(AgentPhase.RECOVER, decision.reason_code, tool_name)
        if not result.accepted:
            self.fail("recovery_transition_failed", "recovery_transition_failed")
            return
        self.pending_recovery = decision
        self.pending_directive = decision.directive[:MAX_CONTROL_DIRECTIVE_CHARS]

    def _observe_review(self, tool_name: str, result_text: str) -> None:
        self.review_count += 1
        self._transition_if_allowed(AgentPhase.REVIEW, "diff_review_completed", tool_name)
        if result_text.startswith(_ERROR_PREFIXES):
            self.last_error_code = "diff_review_failed"
            self.trace.record_review(
                iteration=self.iteration, phase=self.current_phase.value,
                success=False, error_code=self.last_error_code,
            )
            return
        if "diff --git " not in result_text and not (
            "--- " in result_text and "+++ " in result_text
        ):
            self.last_error_code = "diff_review_empty"
            self.trace.record_review(
                iteration=self.iteration, phase=self.current_phase.value,
                success=False, error_code=self.last_error_code,
            )
            return
        if self.verified_revision != self.mutation_revision:
            self.last_error_code = "diff_review_before_verification"
            self.trace.record_review(
                iteration=self.iteration, phase=self.current_phase.value,
                success=False, error_code=self.last_error_code,
            )
            return
        files: list[str] = []
        for match in re.finditer(r"^\+\+\+ b/(.+)$", result_text, re.MULTILINE):
            path = match.group(1).strip()
            if path != "/dev/null" and path not in files:
                files.append(path[:500])
            if len(files) >= MAX_REVIEWED_FILES:
                break
        if files and self.modified_files and not (
            set(files) & set(self.modified_files)
        ):
            self.last_error_code = "diff_review_scope_mismatch"
            self.trace.record_review(
                iteration=self.iteration, phase=self.current_phase.value,
                success=False, error_code=self.last_error_code,
            )
            return
        self.reviewed_files = files
        self.reviewed_revision = self.mutation_revision
        self.last_error_code = None
        self.trace.record_review(
            iteration=self.iteration, phase=self.current_phase.value,
            success=True, error_code=None,
        )

    def completion_decision(self) -> CompletionDecision:
        """Return the deterministic completion gate without making the task terminal."""
        with self._lock:
            if self.is_terminal:
                return CompletionDecision(
                    False, terminal_reason=self.terminal_reason or "terminal_state"
                )
            mode = self.task_mode
            mutation_evidence = self.edit_attempt_count > 0
            if mode is TaskMode.AUTO:
                mode = TaskMode.MUTATION_REQUIRED if mutation_evidence else TaskMode.READ_ONLY

            if mode is TaskMode.MUTATION_REQUIRED:
                missing: list[str] = []
                if self.edit_success_count < 1:
                    missing.append("successful_edit")
                if self.verified_revision != self.mutation_revision:
                    missing.append("fresh_verification")
                if self.reviewed_revision != self.mutation_revision:
                    missing.append("fresh_diff_review")
                if missing:
                    if "successful_edit" in missing:
                        target = AgentPhase.EDIT
                        directive = (
                            "Completion control: this mutation task has no successful byte-changing "
                            "edit. Inspect as needed and perform a real edit before answering."
                        )
                    elif "fresh_verification" in missing:
                        target = AgentPhase.VERIFY
                        directive = (
                            "Completion control: run an objective test for the latest mutation "
                            "revision. Model prose is not verification evidence."
                        )
                    else:
                        target = AgentPhase.REVIEW
                        directive = (
                            "Completion control: call git_diff and review a non-empty diff for the "
                            "latest verified mutation revision before answering."
                        )
                    return CompletionDecision(
                        False, tuple(missing), target,
                        directive[:MAX_CONTROL_DIRECTIVE_CHARS],
                    )
            return CompletionDecision(True)

    def request_completion(self, reason: str = "agent_finished") -> TransitionResult:
        """Apply deterministic evidence rules; missing evidence queues another model turn."""
        with self._lock:
            decision = self.completion_decision()
            self.trace.record_completion_decision(
                iteration=self.iteration,
                phase=self.current_phase.value,
                allowed=decision.allowed,
                missing_evidence=decision.missing_evidence,
            )
            if not decision.allowed:
                code = "completion_evidence_missing"
                self.last_error_code = code
                if decision.directive:
                    self.pending_directive = decision.directive
                target = decision.target_phase
                if target is not None and target is not self.current_phase:
                    transition = self.transition(target, code)
                    if not transition.accepted:
                        self.fail("completion_transition_failed", "completion_transition_failed")
                        return transition
                return TransitionResult(
                    False, code,
                    f"missing evidence: {', '.join(decision.missing_evidence)}",
                )
            mutation_required = (
                self.task_mode is TaskMode.MUTATION_REQUIRED
                or (self.task_mode is TaskMode.AUTO and self.edit_attempt_count > 0)
            )
            if self.current_phase is not AgentPhase.REVIEW and mutation_required:
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
                "review_count": self.review_count,
                "mutation_revision": self.mutation_revision,
                "verified_revision": self.verified_revision,
                "reviewed_revision": self.reviewed_revision,
                "legacy_mutation_count": self.legacy_mutation_count,
                "modified_files": list(self.modified_files),
                "reviewed_files": list(self.reviewed_files),
                "last_tool_name": self.last_tool_name,
                "last_error_code": self.last_error_code,
                "last_test_returncode": self.last_test_returncode,
                "started_at": self.started_at,
                "updated_at": self.updated_at,
                "terminal_reason": self.terminal_reason,
                "pending_directive": bool(self.pending_directive),
                "pending_recovery_action": (
                    self.pending_recovery.action.value if self.pending_recovery else None
                ),
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
                "trace": self.trace.snapshot(),
            }

    def trace_snapshot(self) -> dict[str, Any]:
        """Return the standalone task trace for reports and event consumers."""
        with self._lock:
            return self.trace.snapshot()
