"""Reproducible manifest and summary helpers for paid Agent evaluations."""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from trace_analysis import aggregate_trace_funnel, analyze_report


RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def validate_run_id(value: str) -> str:
    if not isinstance(value, str) or RUN_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "run-id must be 1-64 characters using letters, digits, dot, underscore or hyphen"
        )
    return value


def percentile(values: Iterable[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def _condition_summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = [float(report.get("elapsed_seconds", 0.0)) for report in reports]
    error_codes: Counter[str] = Counter()
    for report in reports:
        codes = report.get("edit_error_codes", {})
        if isinstance(codes, dict):
            for code, count in codes.items():
                if isinstance(code, str) and isinstance(count, int):
                    error_codes[code] += count

    failures = []
    for report in reports:
        analysis = analyze_report(report)
        if report.get("success") is True and analysis["failure_stage"] is None:
            continue
        failures.append({
            "task_id": report.get("task_id"),
            "modified_expected_file": bool(report.get("modified_expected_file")),
            "mutation_restored": bool(report.get("mutation_restored")),
            "test_returncode": report.get("test_returncode"),
            "agent_error": report.get("agent_error"),
            "edit_attempted": bool(report.get("edit_attempted")),
            "edit_error_codes": report.get("edit_error_codes", {}),
            "unexpected_files": report.get("unexpected_files", []),
            **analysis,
        })

    count = len(reports)
    success_count = sum(report.get("success") is True for report in reports)
    modified_expected = sum(
        report.get("modified_expected_file") is True for report in reports
    )
    analyzed = [analyze_report(report) for report in reports]
    failure_stages = Counter(
        item["failure_stage"] for item in analyzed if item["failure_stage"]
    )
    failure_domains = Counter(
        item["failure_domain"] for item in analyzed if item["failure_domain"]
    )
    return {
        "run_count": count,
        "success_count": success_count,
        "success_rate": success_count / count if count else 0.0,
        "agent_completed_count": sum(
            isinstance(report.get("execution_trace"), dict)
            and report["execution_trace"].get("final_status") == "complete"
            for report in reports
        ),
        "classified_failure_count": sum(
            item["failure_stage"] is not None for item in analyzed
        ),
        "modified_expected_file_count": modified_expected,
        "modified_expected_file_rate": modified_expected / count if count else 0.0,
        "mutation_restored_count": sum(
            report.get("mutation_restored") is True for report in reports
        ),
        "tasks_with_unexpected_files": sum(
            bool(report.get("unexpected_files")) for report in reports
        ),
        "edit_attempted_task_count": sum(
            report.get("edit_attempted") is True for report in reports
        ),
        "transactional_edit_task_count": sum(
            int(report.get("transactional_edit_attempt_count", 0)) > 0
            for report in reports
        ),
        "legacy_write_task_count": sum(
            int(report.get("legacy_write_attempt_count", 0)) > 0 for report in reports
        ),
        "edit_attempt_count": sum(
            int(report.get("edit_attempt_count", 0)) for report in reports
        ),
        "edit_success_count": sum(
            int(report.get("edit_success_count", 0)) for report in reports
        ),
        "edit_precondition_failure_count": sum(
            int(report.get("edit_precondition_failure_count", 0)) for report in reports
        ),
        "edit_write_failure_count": sum(
            int(report.get("edit_write_failure_count", 0)) for report in reports
        ),
        "edit_rollback_count": sum(
            int(report.get("edit_rollback_count", 0)) for report in reports
        ),
        "edit_error_codes": dict(sorted(error_codes.items())),
        "semantic_search_calls": sum(
            int(report.get("semantic_search_calls", 0)) for report in reports
        ),
        "tool_call_count": sum(
            int(report.get("tool_call_count", 0)) for report in reports
        ),
        "trace_funnel": aggregate_trace_funnel(reports),
        "failure_stages": dict(sorted(failure_stages.items())),
        "failure_domains": dict(sorted(failure_domains.items())),
        "elapsed_seconds": {
            "average": mean(elapsed) if elapsed else 0.0,
            "median": median(elapsed) if elapsed else 0.0,
            "p95": percentile(elapsed, 0.95),
            "maximum": max(elapsed, default=0.0),
            "total": sum(elapsed),
        },
        "failures": failures,
    }


def summarize_reports(
    reports: list[dict[str, Any]], conditions: Iterable[str]
) -> dict[str, Any]:
    ordered_conditions = list(conditions)
    by_condition = {
        condition: _condition_summary([
            report for report in reports if report.get("condition") == condition
        ])
        for condition in ordered_conditions
    }

    by_task: dict[str, dict[str, dict[str, Any]]] = {}
    for report in reports:
        task_id = str(report.get("task_id", ""))
        condition = str(report.get("condition", ""))
        if task_id and condition:
            by_task.setdefault(task_id, {})[condition] = report

    paired = {
        "complete_pair_count": 0,
        "both_success": 0,
        "both_failed": 0,
        "hybrid_only_success": 0,
        "rerank_only_success": 0,
    }
    if {"hybrid", "rerank"}.issubset(ordered_conditions):
        for pair in by_task.values():
            if "hybrid" not in pair or "rerank" not in pair:
                continue
            paired["complete_pair_count"] += 1
            hybrid_success = pair["hybrid"].get("success") is True
            rerank_success = pair["rerank"].get("success") is True
            if hybrid_success and rerank_success:
                paired["both_success"] += 1
            elif not hybrid_success and not rerank_success:
                paired["both_failed"] += 1
            elif hybrid_success:
                paired["hybrid_only_success"] += 1
            else:
                paired["rerank_only_success"] += 1

    return {
        "schema_version": 1,
        "report_count": len(reports),
        "conditions": by_condition,
        "paired": paired,
    }


def load_reports(result_root: Path) -> list[dict[str, Any]]:
    import json

    reports = []
    for path in sorted(result_root.glob("A*-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "task_id" in data and "condition" in data:
            reports.append(data)
    return reports
