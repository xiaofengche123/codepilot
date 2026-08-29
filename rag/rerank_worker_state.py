"""Pure lifecycle contract for the M6 rerank worker.

This module deliberately owns no model, thread, queue, clock, or metrics.  Later
M6 tasks can coordinate those resources while using one deterministic state
contract and without exposing queries or exception messages in health output.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Any


class RerankWorkerPhase(str, Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"


class RerankWorkerEvent(str, Enum):
    START_LOAD = "start_load"
    LOAD_SUCCEEDED = "load_succeeded"
    LOAD_FAILED = "load_failed"
    INFERENCE_FAILED = "inference_failed"
    INFERENCE_SUCCEEDED = "inference_succeeded"
    FAILURE_LIMIT_REACHED = "failure_limit_reached"
    START_RECOVERY_PROBE = "start_recovery_probe"
    UNLOAD = "unload"


_TRANSITIONS = MappingProxyType(
    {
        RerankWorkerEvent.START_LOAD: (
            frozenset({RerankWorkerPhase.UNLOADED, RerankWorkerPhase.FAILED}),
            RerankWorkerPhase.LOADING,
        ),
        RerankWorkerEvent.LOAD_SUCCEEDED: (
            frozenset({RerankWorkerPhase.LOADING}),
            RerankWorkerPhase.READY,
        ),
        RerankWorkerEvent.LOAD_FAILED: (
            frozenset({RerankWorkerPhase.LOADING}),
            RerankWorkerPhase.FAILED,
        ),
        RerankWorkerEvent.INFERENCE_FAILED: (
            frozenset({RerankWorkerPhase.READY, RerankWorkerPhase.DEGRADED}),
            RerankWorkerPhase.DEGRADED,
        ),
        RerankWorkerEvent.INFERENCE_SUCCEEDED: (
            frozenset({RerankWorkerPhase.DEGRADED}),
            RerankWorkerPhase.READY,
        ),
        RerankWorkerEvent.FAILURE_LIMIT_REACHED: (
            frozenset({RerankWorkerPhase.DEGRADED}),
            RerankWorkerPhase.FAILED,
        ),
        RerankWorkerEvent.START_RECOVERY_PROBE: (
            frozenset({RerankWorkerPhase.FAILED}),
            RerankWorkerPhase.DEGRADED,
        ),
        RerankWorkerEvent.UNLOAD: (
            frozenset(
                {
                    RerankWorkerPhase.READY,
                    RerankWorkerPhase.DEGRADED,
                    RerankWorkerPhase.FAILED,
                }
            ),
            RerankWorkerPhase.UNLOADED,
        ),
    }
)

_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class InvalidRerankWorkerTransition(ValueError):
    """Raised when an event is not valid for the current worker phase."""

    error_code = "invalid_rerank_worker_transition"

    def __init__(self, phase: RerankWorkerPhase, event: RerankWorkerEvent) -> None:
        self.phase = phase
        self.event = event
        super().__init__(f"{self.error_code}: {phase.value} + {event.value}")


@dataclass(frozen=True, slots=True)
class RerankWorkerState:
    """Bounded, query-free snapshot suitable for health and metrics adapters."""

    phase: RerankWorkerPhase = RerankWorkerPhase.UNLOADED
    revision: int = 0
    last_event: RerankWorkerEvent | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.phase, RerankWorkerPhase):
            raise TypeError("phase must be a RerankWorkerPhase")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("revision must be an integer")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        if self.last_event is not None and not isinstance(
            self.last_event, RerankWorkerEvent
        ):
            raise TypeError("last_event must be a RerankWorkerEvent or None")
        if (self.last_event is None) != (self.reason_code is None):
            raise ValueError("last_event and reason_code must be set together")
        if self.revision == 0 and (
            self.phase is not RerankWorkerPhase.UNLOADED
            or self.last_event is not None
        ):
            raise ValueError("revision zero must be the initial unloaded state")
        if self.revision > 0:
            if self.last_event is None:
                raise ValueError("non-initial state requires a last_event")
            if _TRANSITIONS[self.last_event][1] is not self.phase:
                raise ValueError("last_event does not produce the snapshot phase")
        if self.reason_code is not None:
            if not isinstance(self.reason_code, str):
                raise TypeError("reason_code must be a string or None")
            if not _REASON_CODE.fullmatch(self.reason_code):
                raise ValueError("reason_code must be a bounded snake-case identifier")

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "revision": self.revision,
            "last_event": self.last_event.value if self.last_event else None,
            "reason_code": self.reason_code,
        }


def transition_rerank_worker(
    state: RerankWorkerState,
    event: RerankWorkerEvent,
    reason_code: str,
) -> RerankWorkerState:
    """Return the next snapshot or reject an illegal lifecycle transition."""
    if not isinstance(state, RerankWorkerState):
        raise TypeError("state must be a RerankWorkerState")
    if not isinstance(event, RerankWorkerEvent):
        raise TypeError("event must be a RerankWorkerEvent")
    if not isinstance(reason_code, str):
        raise TypeError("reason_code must be a string")
    if not _REASON_CODE.fullmatch(reason_code):
        raise ValueError("reason_code must be a bounded snake-case identifier")

    allowed_phases, next_phase = _TRANSITIONS[event]
    if state.phase not in allowed_phases:
        raise InvalidRerankWorkerTransition(state.phase, event)
    return RerankWorkerState(
        phase=next_phase,
        revision=state.revision + 1,
        last_event=event,
        reason_code=reason_code,
    )
