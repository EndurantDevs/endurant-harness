#!/usr/bin/env python3
"""Build a sanitized, reproducible summary from ignored local model-run captures."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from eval_lib import ARTIFACTS, read_json, write_json


FIELDS = (
    "duration_seconds",
    "uncached_input_tokens",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "time_to_first_tool_seconds",
    "time_to_first_edit_seconds",
)


def run_record(capture: Path) -> dict[str, Any]:
    summary = read_json(capture / "summary.json")
    metadata = read_json(capture / "metadata.json")
    agent = summary["agent"]
    grade_path = capture / "grade.json"
    grade = read_json(grade_path) if grade_path.is_file() else (summary.get("grade") or {})
    usage = agent.get("usage", {})
    evaluator_integrity = None
    if "agent_event_log_valid" in grade:
        functional_checks = grade.get("functional", {}).get("checks", {})
        evaluator_integrity = {
            "agent_event_log_valid": grade.get("agent_event_log_valid") is True,
            "agent_event_log_tampered": grade.get("agent_event_log_tampered") is True,
        }
        for key in (
            "unit_regression_detects_bug",
            "cli_regression_detects_bug",
            "cli_regression_invokes_entrypoint",
        ):
            if key in functional_checks:
                evaluator_integrity[key] = functional_checks.get(key) is True
    record = {
        "run_id": summary["run_id"],
        "subject": metadata["subject"],
        "fixture": metadata["fixture"],
        "repeat": metadata["repeat"],
        "accepted": summary.get("passed") is True,
        "duration_seconds": agent.get("duration_seconds"),
        "uncached_input_tokens": agent.get("uncached_input_tokens"),
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": usage.get("cached_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
        "time_to_first_tool_seconds": agent.get("time_to_first_tool_seconds"),
        "time_to_first_edit_seconds": agent.get("time_to_first_edit_seconds"),
        "command_items": agent.get("item_counts", {}).get("command_execution"),
        "evaluator_integrity": evaluator_integrity,
        "functional_passed": grade.get("functional", {}).get("passed"),
        "local_ci_preflight_passed": grade.get("local_ci_preflight_passed"),
        "git_state_unchanged": grade.get("git_state_unchanged"),
        "subject_tree_unchanged": grade.get("subject_tree_unchanged"),
        "changed_paths": grade.get("changed_paths"),
        "performance": summary.get("grade", {}).get("performance", {}),
    }
    return record


def load_runs() -> list[dict[str, Any]]:
    records = []
    for capture in sorted((ARTIFACTS / "runs").iterdir()):
        if (capture / "summary.json").is_file() and (capture / "metadata.json").is_file():
            records.append(run_record(capture))
    return records


def select(
    records: list[dict[str, Any]], fixture: str, subject: str, repeats: set[int]
) -> list[dict[str, Any]]:
    selected = [
        record
        for record in records
        if record["fixture"] == fixture
        and record["subject"] == subject
        and record["repeat"] in repeats
    ]
    selected.sort(key=lambda record: record["repeat"])
    if {record["repeat"] for record in selected} != repeats:
        raise RuntimeError(f"missing {fixture}/{subject} repeats: {sorted(repeats)}")
    return selected


def median_metrics(records: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for field in FIELDS:
        values = [record[field] for record in records]
        if all(isinstance(value, (int, float)) for value in values):
            result[field] = round(float(statistics.median(values)), 6)
    return result


def reductions(current: dict[str, float], candidate: dict[str, float]) -> dict[str, float]:
    result = {}
    for field in FIELDS:
        baseline = current.get(field)
        changed = candidate.get(field)
        if baseline is not None and changed is not None and baseline:
            result[field] = round((baseline - changed) / baseline, 6)
    return result


def compact(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"subject", "fixture", "repeat"} and value is not None
    }


def main() -> int:
    records = load_runs()
    fixture = "settings-override-correctness"
    repeats = {2, 3}
    current = select(records, fixture, "current", repeats)
    combined = select(records, fixture, "combined-candidate", repeats)
    current_median = median_metrics(current)
    combined_median = median_metrics(combined)

    performance_fixture = "record-selection-performance"
    performance_current = select(records, performance_fixture, "current", {2})[0]
    performance_combined = select(
        records, performance_fixture, "combined-candidate", {2}
    )[0]
    final_evaluator_smoke = select(
        records, fixture, "combined-candidate", {4}
    )[0]
    dogfood_performance_smoke = select(
        records, performance_fixture, "combined-candidate", {5}
    )[0]

    metadata = read_json(
        ARTIFACTS / "runs" / combined[0]["run_id"] / "metadata.json"
    )
    result = {
        "environment": {
            "cli": metadata.get("codex_version"),
            "model": metadata.get("model"),
            "reasoning_effort": metadata.get("reasoning_effort"),
            "network": "disabled",
            "subagents": "disabled",
            "history_and_memories": "disabled",
        },
        "ordinary_combined_evaluation": {
            "paired_repeats": sorted(repeats),
            "all_accepted": all(record["accepted"] for record in current + combined),
            "current": {
                "runs": [compact(record) for record in current],
                "median": current_median,
            },
            "combined_candidate": {
                "runs": [compact(record) for record in combined],
                "median": combined_median,
            },
            "observed_reduction_fraction": reductions(current_median, combined_median),
        },
        "performance_verification_evaluation": {
            "paired_repeat": 2,
            "all_accepted": performance_current["accepted"]
            and performance_combined["accepted"],
            "current": compact(performance_current),
            "combined_candidate": compact(performance_combined),
            "observed_reduction_fraction": reductions(
                median_metrics([performance_current]),
                median_metrics([performance_combined]),
            ),
            "interpretation": (
                "Both arms selected unchanged-before/changed-after synthetic proof, "
                "correctness, and local CI. This single pair is quality evidence, not "
                "a universal performance-task wall-time claim."
            ),
        },
        "final_evaluator_smoke": {
            **compact(final_evaluator_smoke),
            "interpretation": (
                "Fresh combined-candidate smoke after runner-observed event ordering, "
                "targeted mutation grading, and CLI-entrypoint instrumentation. It is "
                "not included in the paired speed comparison."
            ),
        },
        "dogfood_performance_smoke": {
            **compact(dogfood_performance_smoke),
            "interpretation": (
                "Fresh final-candidate performance smoke after Git-aware probe "
                "hardening. It is independently graded and is not included in the "
                "paired speed comparison."
            ),
        },
    }
    write_json(ARTIFACTS / "benchmarks" / "model-runs.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
