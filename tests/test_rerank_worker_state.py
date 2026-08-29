"""Lifecycle contract tests for MODEL-001."""

from dataclasses import FrozenInstanceError
import json

import pytest

from rag.rerank_worker_state import (
    InvalidRerankWorkerTransition,
    RerankWorkerEvent,
    RerankWorkerPhase,
    RerankWorkerState,
    transition_rerank_worker,
)


def _transition(state, event, reason=None):
    return transition_rerank_worker(state, event, reason or event.value)


def _state_in(phase):
    state = RerankWorkerState()
    if phase is RerankWorkerPhase.UNLOADED:
        return state
    state = _transition(state, RerankWorkerEvent.START_LOAD)
    if phase is RerankWorkerPhase.LOADING:
        return state
    if phase is RerankWorkerPhase.FAILED:
        return _transition(state, RerankWorkerEvent.LOAD_FAILED)
    state = _transition(state, RerankWorkerEvent.LOAD_SUCCEEDED)
    if phase is RerankWorkerPhase.READY:
        return state
    return _transition(state, RerankWorkerEvent.INFERENCE_FAILED)


def test_initial_state_is_unloaded_and_json_ready():
    state = RerankWorkerState()
    assert state.to_dict() == {
        "phase": "unloaded",
        "revision": 0,
        "last_event": None,
        "reason_code": None,
    }
    json.dumps(state.to_dict())


def test_load_success_path_is_explicit_and_immutable():
    initial = RerankWorkerState()
    loading = _transition(initial, RerankWorkerEvent.START_LOAD)
    ready = _transition(loading, RerankWorkerEvent.LOAD_SUCCEEDED)

    assert initial.phase is RerankWorkerPhase.UNLOADED
    assert loading.phase is RerankWorkerPhase.LOADING
    assert ready.phase is RerankWorkerPhase.READY
    assert [initial.revision, loading.revision, ready.revision] == [0, 1, 2]
    with pytest.raises(FrozenInstanceError):
        ready.phase = RerankWorkerPhase.FAILED


def test_loading_failure_and_retry_return_through_loading():
    loading = _transition(RerankWorkerState(), RerankWorkerEvent.START_LOAD)
    failed = _transition(loading, RerankWorkerEvent.LOAD_FAILED)
    retrying = _transition(failed, RerankWorkerEvent.START_LOAD, "recovery_probe")

    assert failed.phase is RerankWorkerPhase.FAILED
    assert retrying.phase is RerankWorkerPhase.LOADING
    assert retrying.reason_code == "recovery_probe"


def test_inference_health_path_supports_repeated_failures_and_recovery():
    loading = _transition(RerankWorkerState(), RerankWorkerEvent.START_LOAD)
    ready = _transition(loading, RerankWorkerEvent.LOAD_SUCCEEDED)
    degraded = _transition(ready, RerankWorkerEvent.INFERENCE_FAILED)
    still_degraded = _transition(degraded, RerankWorkerEvent.INFERENCE_FAILED)
    recovered = _transition(
        still_degraded, RerankWorkerEvent.INFERENCE_SUCCEEDED
    )

    assert degraded.phase is RerankWorkerPhase.DEGRADED
    assert still_degraded.phase is RerankWorkerPhase.DEGRADED
    assert still_degraded.revision == degraded.revision + 1
    assert recovered.phase is RerankWorkerPhase.READY


def test_failure_limit_is_separate_from_individual_inference_failure():
    state = _state_in(RerankWorkerPhase.DEGRADED)
    failed = _transition(
        state, RerankWorkerEvent.FAILURE_LIMIT_REACHED, "failure_limit_reached"
    )
    assert failed.phase is RerankWorkerPhase.FAILED


def test_failed_worker_has_distinct_reload_and_recovery_probe_paths():
    failed = _state_in(RerankWorkerPhase.FAILED)
    reload_state = _transition(failed, RerankWorkerEvent.START_LOAD)
    probe_state = _transition(failed, RerankWorkerEvent.START_RECOVERY_PROBE)

    assert reload_state.phase is RerankWorkerPhase.LOADING
    assert probe_state.phase is RerankWorkerPhase.DEGRADED


@pytest.mark.parametrize(
    ("phase", "event"),
    [
        (RerankWorkerPhase.UNLOADED, RerankWorkerEvent.LOAD_SUCCEEDED),
        (RerankWorkerPhase.LOADING, RerankWorkerEvent.INFERENCE_FAILED),
        (RerankWorkerPhase.READY, RerankWorkerEvent.START_LOAD),
        (RerankWorkerPhase.FAILED, RerankWorkerEvent.INFERENCE_SUCCEEDED),
    ],
)
def test_illegal_transitions_are_rejected_without_mutating_state(phase, event):
    state = _state_in(phase)
    before = state.to_dict()
    with pytest.raises(InvalidRerankWorkerTransition) as caught:
        _transition(state, event)
    assert caught.value.error_code == "invalid_rerank_worker_transition"
    assert state.to_dict() == before


@pytest.mark.parametrize(
    "reason",
    ["", "UPPER_CASE", "contains spaces", "exception: secret", "x" * 65],
)
def test_reason_codes_are_bounded_identifiers(reason):
    with pytest.raises(ValueError):
        transition_rerank_worker(
            RerankWorkerState(), RerankWorkerEvent.START_LOAD, reason
        )


def test_reason_contract_does_not_store_exception_or_query_content():
    secret = "compare secret_alpha.py and secret_beta.py"
    with pytest.raises(ValueError):
        transition_rerank_worker(
            RerankWorkerState(), RerankWorkerEvent.START_LOAD, secret
        )
    assert secret not in json.dumps(RerankWorkerState().to_dict())


@pytest.mark.parametrize("bad_state", [None, object(), "unloaded"])
def test_transition_rejects_non_contract_states(bad_state):
    with pytest.raises(TypeError):
        transition_rerank_worker(
            bad_state, RerankWorkerEvent.START_LOAD, "start_load"
        )


def test_unload_is_explicit_for_stable_non_loading_states():
    for phase in (
        RerankWorkerPhase.READY,
        RerankWorkerPhase.DEGRADED,
        RerankWorkerPhase.FAILED,
    ):
        state = _state_in(phase)
        assert _transition(state, RerankWorkerEvent.UNLOAD).phase is (
            RerankWorkerPhase.UNLOADED
        )


def test_module_has_no_runtime_model_queue_or_config_dependencies():
    import rag.rerank_worker_state as module

    names = set(module.__dict__)
    assert not ({"CrossEncoder", "Queue", "Thread", "config"} & names)


def test_snapshot_rejects_history_that_cannot_produce_its_phase():
    with pytest.raises(ValueError, match="last_event does not produce"):
        RerankWorkerState(
            phase=RerankWorkerPhase.READY,
            revision=1,
            last_event=RerankWorkerEvent.LOAD_FAILED,
            reason_code="load_failed",
        )
