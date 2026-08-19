"""Deterministic task-trace funnel metrics and failure classification."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping


FAILURE_STAGES = (
    "retrieval_miss",
    "wrong_file_inspected",
    "no_edit_attempt",
    "edit_precondition_failed",
    "incorrect_edit",
    "syntax_failure",
    "test_assertion_failure",
    "environment_failure",
    "iteration_budget_exhausted",
    "unexpected_file_change",
)

PRECONDITION_ERRORS = {
    "sha_mismatch", "concurrent_modification", "match_count_mismatch",
    "overlapping_edits", "empty_match",
}
ENVIRONMENT_ERROR_CODES = {
    "verification_unavailable", "verification_timeout",
    "verification_result_unknown", "invalid_workdir", "path_outside_workdir",
}
ENVIRONMENT_TEXT_PATTERNS = {
    "timeout": ("timed out", "timeout", "worker_timeout"),
    "permission_denied": ("permissionerror", "permission denied", "access is denied"),
    "dependency_missing": ("modulenotfounderror", "no module named"),
    "command_unavailable": ("not recognized as an internal", "command not found"),
    "api_unavailable": ("connectionerror", "connection refused", "api connection"),
    "resource_exhausted": ("no space left", "out of memory", "memoryerror"),
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _trace(report: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(report.get("execution_trace"))


def _paths(values: Any) -> set[str]:
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {
        str(value).replace("\\", "/")
        for value in values
        if isinstance(value, str) and value
    }


def _retrieved_files(trace: Mapping[str, Any]) -> set[str]:
    files: set[str] = set()
    calls = trace.get("retrieval_calls", [])
    if isinstance(calls, list):
        for call in calls:
            files.update(_paths(_mapping(call).get("result_files")))
    return files


def _relevant_test_inspected(paths: set[str]) -> bool:
    return any(
        path.lower().startswith("tests/")
        or "/tests/" in path.lower()
        or path.rsplit("/", 1)[-1].lower().startswith("test_")
        for path in paths
    )


def trace_funnel_flags(report: Mapping[str, Any]) -> dict[str, bool]:
    trace = _trace(report)
    expected = _paths(report.get("expected_files"))
    retrieved = _retrieved_files(trace)
    inspected = _paths(trace.get("inspected_files"))
    changed = _paths(trace.get("changed_files"))
    edit_attempts = trace.get("edit_attempts", [])
    test_runs = trace.get("test_runs", [])
    edit_attempted = bool(edit_attempts) or report.get("edit_attempted") is True
    target_modified = (
        bool(expected & changed) if expected
        else report.get("modified_expected_file") is True
    )
    tests = test_runs if isinstance(test_runs, list) else []
    return {
        "retrieval_hit_required": bool(expected & retrieved),
        "correct_file_inspected": bool(expected & inspected),
        "relevant_test_inspected": _relevant_test_inspected(inspected),
        "edit_attempted": edit_attempted,
        "target_file_modified": target_modified,
        "oracle_satisfied": report.get("mutation_restored") is True,
        "test_executed": bool(tests),
        "test_passed": any(_mapping(item).get("success") is True for item in tests),
        "no_unexpected_files": not bool(report.get("unexpected_files")),
    }


def aggregate_trace_funnel(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(reports)
    counts = Counter()
    for report in items:
        for name, passed in trace_funnel_flags(report).items():
            counts[name] += int(passed)
    total = len(items)
    result: dict[str, Any] = {"tasks_total": total}
    for name in (
        "retrieval_hit_required", "correct_file_inspected",
        "relevant_test_inspected", "edit_attempted", "target_file_modified",
        "oracle_satisfied", "test_executed", "test_passed",
        "no_unexpected_files",
    ):
        result[name] = counts[name]
    result["rates"] = {
        name: result[name] / total if total else 0.0
        for name in result if name != "tasks_total"
    }
    return result


def _terminal_reason(trace: Mapping[str, Any]) -> str | None:
    phases = trace.get("phases", [])
    if not isinstance(phases, list):
        return None
    for event in reversed(phases):
        item = _mapping(event)
        if item.get("to_phase") == "failed" and isinstance(item.get("reason"), str):
            return item["reason"]
    return None


def _environment_reason(report: Mapping[str, Any]) -> str | None:
    if report.get("worker_failure") is True:
        return "worker_timeout" if report.get("worker_returncode") == 124 else "worker_failure"
    trace = _trace(report)
    stable_codes = {
        str(item.get("error_code"))
        for group in (trace.get("edit_attempts", []), trace.get("test_runs", []))
        if isinstance(group, list)
        for item in map(_mapping, group)
        if item.get("error_code")
    }
    terminal = _terminal_reason(trace)
    if terminal:
        stable_codes.add(terminal)
    for code in sorted(stable_codes):
        if code in ENVIRONMENT_ERROR_CODES:
            return code
    text = " ".join(
        str(report.get(name, ""))[:4000].lower()
        for name in ("agent_error", "test_output")
    )
    for reason, patterns in ENVIRONMENT_TEXT_PATTERNS.items():
        if any(pattern in text for pattern in patterns):
            return reason
    return None


def _candidate_failures(report: Mapping[str, Any]) -> list[str]:
    trace = _trace(report)
    expected = _paths(report.get("expected_files"))
    retrieved = _retrieved_files(trace)
    inspected = _paths(trace.get("inspected_files"))
    edits = trace.get("edit_attempts", [])
    edit_attempted = bool(edits) or report.get("edit_attempted") is True
    candidates: list[str] = []
    if report.get("unexpected_files"):
        candidates.append("unexpected_file_change")
    if _terminal_reason(trace) == "max_iterations_exhausted":
        candidates.append("iteration_budget_exhausted")
    if not edit_attempted:
        if not retrieved:
            candidates.append("retrieval_miss")
        elif expected and not (expected & inspected):
            candidates.append("wrong_file_inspected")
        else:
            candidates.append("no_edit_attempt")
    errors = _mapping(report.get("edit_error_codes"))
    if any(code in PRECONDITION_ERRORS and count for code, count in errors.items()):
        candidates.append("edit_precondition_failed")
    if errors.get("python_syntax_error"):
        candidates.append("syntax_failure")
    if edit_attempted and (
        report.get("modified_expected_file") is False
        or report.get("mutation_restored") is False
    ):
        candidates.append("incorrect_edit")
    returncode = report.get("test_returncode")
    if isinstance(returncode, int) and returncode != 0:
        candidates.append("test_assertion_failure")
    if not candidates:
        candidates.append("incorrect_edit" if edit_attempted else "no_edit_attempt")
    return list(dict.fromkeys(candidates))


def analyze_report(report: Mapping[str, Any]) -> dict[str, Any]:
    trace = _trace(report)
    if report.get("success") is True and trace.get("final_status") != "failed":
        return {
            "failure_stage": None,
            "secondary_failure_reasons": [],
            "failure_domain": None,
            "failure_reason_code": None,
        }
    environment_reason = _environment_reason(report)
    candidates = _candidate_failures(report)
    if environment_reason:
        primary = "environment_failure"
        secondary = [item for item in candidates if item != primary]
        domain = "environment"
        reason_code = environment_reason
    else:
        priority = (
            "unexpected_file_change", "iteration_budget_exhausted",
            "retrieval_miss", "wrong_file_inspected", "no_edit_attempt",
            "edit_precondition_failed", "syntax_failure", "incorrect_edit",
            "test_assertion_failure",
        )
        primary = next(item for item in priority if item in candidates)
        secondary = [item for item in candidates if item != primary]
        domain = "control" if primary == "iteration_budget_exhausted" else "code"
        reason_code = primary
    return {
        "failure_stage": primary,
        "secondary_failure_reasons": secondary,
        "failure_domain": domain,
        "failure_reason_code": reason_code,
    }


def analyze_trace_failure(
    trace: Mapping[str, Any], terminal_reason: str, last_error_code: str | None
) -> dict[str, Any]:
    synthetic = {
        "success": False,
        "execution_trace": trace,
        "edit_attempted": bool(trace.get("edit_attempts")),
        "modified_expected_file": bool(trace.get("changed_files")),
        "mutation_restored": bool(trace.get("changed_files")),
        "unexpected_files": [],
        "edit_error_codes": Counter(
            str(item.get("error_code"))
            for item in map(_mapping, trace.get("edit_attempts", []))
            if item.get("error_code")
        ),
        "test_returncode": (
            _mapping(trace.get("test_runs", [])[-1]).get("returncode")
            if trace.get("test_runs") else None
        ),
    }
    if terminal_reason in ENVIRONMENT_ERROR_CODES:
        synthetic["agent_error"] = terminal_reason
    if last_error_code in ENVIRONMENT_ERROR_CODES:
        synthetic["agent_error"] = last_error_code
    return analyze_report(synthetic)


def aggregate_runtime_traces(traces: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = [trace for trace in traces if isinstance(trace, Mapping)]
    counts = Counter()
    stages: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    for trace in items:
        retrieval = trace.get("retrieval_calls", [])
        inspections = trace.get("inspected_files", [])
        edits = trace.get("edit_attempts", [])
        tests = trace.get("test_runs", [])
        phases = trace.get("phases", [])
        counts["retrieval_attempted"] += int(bool(retrieval))
        counts["inspected"] += int(bool(inspections))
        counts["edit_attempted"] += int(bool(edits))
        counts["edit_succeeded"] += int(any(_mapping(item).get("success") is True for item in edits))
        counts["test_executed"] += int(bool(tests))
        counts["test_passed"] += int(any(_mapping(item).get("success") is True for item in tests))
        counts["review_passed"] += int(any(
            _mapping(item).get("event_type") == "review"
            and _mapping(item).get("success") is True for item in phases
        ))
        counts["completed"] += int(trace.get("final_status") == "complete")
        if trace.get("failure_stage"):
            stages[str(trace["failure_stage"])] += 1
        if trace.get("failure_domain"):
            domains[str(trace["failure_domain"])] += 1
    return {
        "tasks_total": len(items),
        **{name: counts[name] for name in (
            "retrieval_attempted", "inspected", "edit_attempted",
            "edit_succeeded", "test_executed", "test_passed",
            "review_passed", "completed",
        )},
        "failure_stages": dict(sorted(stages.items())),
        "failure_domains": dict(sorted(domains.items())),
    }
