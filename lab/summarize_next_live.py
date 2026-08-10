#!/usr/bin/env python3
"""Create a sanitized, relationship-checked receipt from ignored live canaries."""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from eval_lib import ARTIFACTS, LAB_ROOT, read_json, sha256_file, write_json


DIRECT_CURRENT = [
    "20260810T221513-combined-candidate-settings-override-correctness-r11",
    "20260810T221613-combined-candidate-settings-override-correctness-r12",
]
DIRECT_CANDIDATE = [
    "20260810T221424-direct-budget-settings-override-correctness-r11",
    "20260810T221713-direct-budget-settings-override-correctness-r12",
]
RED_CANDIDATE = [
    "20260810T221911-red-before-green-settings-override-correctness-r13",
    "20260810T222009-red-before-green-settings-override-correctness-r14",
]
PERFORMANCE_RUNS = {
    "historical_without_receipt": (
        "20260810T205009-combined-candidate-record-selection-performance-r5"
    ),
    "repository_contract_current_skill": (
        "20260810T223025-combined-candidate-record-selection-receipt-r21"
    ),
    "extra_core_wording": (
        "20260810T222854-benchmark-receipt-record-selection-receipt-r21"
    ),
}
LANE_RAW = (
    ARTIFACTS / "runtime" / "lane-ab-20260810T221426Z" / "summary.json"
)
BOUNDARY_RAW = (
    ARTIFACTS
    / "runtime"
    / "boundary-canaries-20260810T222853Z-4b5e26"
    / "summary-rescored.json"
)
NONBUG_RAW = (
    ARTIFACTS
    / "runtime"
    / "red-first-nonbug-20260810T223017-1c3e52"
    / "summary.json"
)
LIVE_EVIDENCE_KEYS = {
    "environment",
    "subject_skill_sha256",
    "raw_receipts",
    "raw_receipt",
    "boundary_raw_receipt",
    "nonbug_raw_receipt",
}


def percent_change(current: float, candidate: float) -> float:
    return round((candidate - current) / current, 6)


def reduction(current: float, candidate: float) -> float:
    return round((current - candidate) / current, 6)


def file_receipt(path: Path) -> dict[str, Any]:
    return {"sha256": sha256_file(path), "bytes": path.stat().st_size}


def named_live_evidence(value: Any, path: str = "$") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key in sorted(value):
            child_path = f"{path}/{key}"
            if key in LIVE_EVIDENCE_KEYS:
                rows.append({"path": child_path, "value": value[key]})
            else:
                rows.extend(named_live_evidence(value[key], child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            rows.extend(named_live_evidence(item, f"{path}/{index}"))
    return rows


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def observed_events(capture: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    path = capture / "codex-observed.jsonl"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        wrapped = json.loads(line)
        if isinstance(wrapped, dict):
            events.append(wrapped)
    return events


def capture_record(run_id: str) -> dict[str, Any]:
    capture = ARTIFACTS / "runs" / run_id
    summary = read_json(capture / "summary.json")
    grade = read_json(capture / "grade.json")
    metadata = read_json(capture / "metadata.json")
    agent = summary["agent"]
    first_edit = agent.get("first_edit_monotonic_ns")
    start = agent.get("agent_started_monotonic_ns")
    command_ids: set[str] = set()
    pre_edit_ids: set[str] = set()
    pre_edit_output_bytes = 0
    first_tool = agent.get("time_to_first_tool_seconds")
    for wrapped in observed_events(capture):
        observed = wrapped.get("observed_monotonic_ns")
        event = wrapped.get("event", {})
        item = event.get("item", {}) if isinstance(event, dict) else {}
        if item.get("type") != "command_execution":
            continue
        item_id = str(item.get("id", ""))
        if event.get("type") == "item.started" and item_id:
            command_ids.add(item_id)
            if isinstance(first_edit, int) and isinstance(observed, int) and observed < first_edit:
                pre_edit_ids.add(item_id)
        if (
            event.get("type") == "item.completed"
            and isinstance(first_edit, int)
            and isinstance(observed, int)
            and observed < first_edit
        ):
            pre_edit_output_bytes += len(
                str(item.get("aggregated_output", "")).encode("utf-8")
            )
    return {
        "run_id": run_id,
        "accepted": summary.get("passed") is True,
        "duration_seconds": agent.get("duration_seconds"),
        "time_to_first_edit_seconds": agent.get("time_to_first_edit_seconds"),
        "first_tool_to_edit_seconds": round(
            float(agent["time_to_first_edit_seconds"]) - float(first_tool), 6
        )
        if isinstance(agent.get("time_to_first_edit_seconds"), (int, float))
        and isinstance(first_tool, (int, float))
        else None,
        "uncached_input_tokens": agent.get("uncached_input_tokens"),
        "output_tokens": agent.get("usage", {}).get("output_tokens"),
        "reasoning_output_tokens": agent.get("usage", {}).get(
            "reasoning_output_tokens"
        ),
        "pre_edit_command_count": len(pre_edit_ids),
        "total_command_count": len(command_ids),
        "pre_edit_output_bytes": pre_edit_output_bytes,
        "changed_paths": grade.get("changed_paths"),
        "functional_passed": grade.get("functional", {}).get("passed") is True,
        "local_ci_passed": grade.get("local_ci_preflight_passed") is True,
        "git_state_unchanged": grade.get("git_state_unchanged") is True,
        "subject_tree_unchanged": grade.get("subject_tree_unchanged") is True,
        "subject_skill_sha256": metadata.get("subject_skill_sha256"),
        "raw_receipts": {
            "summary": file_receipt(capture / "summary.json"),
            "grade": file_receipt(capture / "grade.json"),
            "metadata": file_receipt(capture / "metadata.json"),
            "codex_observed": file_receipt(capture / "codex-observed.jsonl"),
            "agent_events_observed": file_receipt(
                capture / "agent-events-observed.jsonl"
            ),
        },
    }


def median_fields(records: list[dict[str, Any]], fields: list[str]) -> dict[str, float]:
    return {
        field: round(statistics.median(float(record[field]) for record in records), 6)
        for field in fields
    }


def discovery_budget(boundaries: dict[str, Any]) -> dict[str, Any]:
    current = [capture_record(run_id) for run_id in DIRECT_CURRENT]
    candidate = [capture_record(run_id) for run_id in DIRECT_CANDIDATE]
    fields = [
        "duration_seconds",
        "time_to_first_edit_seconds",
        "first_tool_to_edit_seconds",
        "uncached_input_tokens",
        "pre_edit_command_count",
        "total_command_count",
        "pre_edit_output_bytes",
    ]
    current_median = median_fields(current, fields)
    candidate_median = median_fields(candidate, fields)
    comparison = {
        field + "_reduction_fraction": reduction(current_median[field], candidate_median[field])
        for field in fields
        if current_median[field]
    }
    boundary_rows = [
        {
            "canary": row["canary"],
            "duration_seconds": row["duration_seconds"],
            "command_count": row["events"]["command_count"],
            "uncached_input_tokens": row["events"]["uncached_input_tokens"],
            "file_change_items": row["events"]["file_change_items"],
            "passed": row["passed"],
            "checks": row["grade"]["checks"],
        }
        for row in boundaries["results"]
    ]
    gates = {
        "all_clear_runs_accepted": all(row["accepted"] for row in candidate),
        "clear_runs_max_two_pre_edit_commands": all(
            row["pre_edit_command_count"] <= 2 for row in candidate
        ),
        "clear_runs_max_five_total_commands": all(
            row["total_command_count"] <= 5 for row in candidate
        ),
        "pre_edit_output_did_not_increase": candidate_median[
            "pre_edit_output_bytes"
        ]
        <= current_median["pre_edit_output_bytes"] * 1.10,
        "median_wall_improved_15_percent": comparison[
            "duration_seconds_reduction_fraction"
        ]
        >= 0.15,
        "uncached_input_non_regressing": comparison[
            "uncached_input_tokens_reduction_fraction"
        ]
        >= 0,
        "both_boundary_canaries_escalated_without_edit": bool(
            boundaries.get("passed") is True
            and len(boundary_rows) == 2
            and all(
                row["passed"] is True and row["file_change_items"] == 0
                for row in boundary_rows
            )
        ),
    }
    return {
        "current": {"runs": current, "median": current_median},
        "candidate": {"runs": candidate, "median": candidate_median},
        "comparison": comparison,
        "boundary_canaries": boundary_rows,
        "boundary_raw_receipt": file_receipt(BOUNDARY_RAW),
        "gates": gates,
        "passed": all(gates.values()),
        "limitations": [
            "Two paired clear-task repeats and one run per safety boundary.",
            "Command count does not bound shell-output volume; the boundary runs still emitted substantial read output.",
        ],
    }


def red_sequence(run_id: str) -> dict[str, Any]:
    capture = ARTIFACTS / "runs" / run_id
    summary = read_json(capture / "summary.json")
    first_edit = summary["agent"].get("first_edit_monotonic_ns")
    test_change_time: int | None = None
    failed_test_time: int | None = None
    failed_command: str | None = None
    for wrapped in observed_events(capture):
        observed = wrapped.get("observed_monotonic_ns")
        event = wrapped.get("event", {})
        item = event.get("item", {}) if isinstance(event, dict) else {}
        if not isinstance(observed, int) or not isinstance(first_edit, int):
            continue
        if event.get("type") == "item.completed" and item.get("type") == "file_change":
            paths = [str(change.get("path", "")) for change in item.get("changes", [])]
            if paths and all("/tests/" in path for path in paths) and observed < first_edit:
                test_change_time = observed
        if event.get("type") == "item.completed" and item.get("type") == "command_execution":
            command = str(item.get("command", ""))
            output = str(item.get("aggregated_output", ""))
            test_like = "unittest" in command or "verify.py focused" in command
            if (
                test_like
                and item.get("exit_code") not in {None, 0}
                and observed < first_edit
                and ("FAIL" in output or "FAILED" in output)
            ):
                failed_test_time = observed
                failed_command = hashlib.sha256(command.encode("utf-8")).hexdigest()
    ordered = bool(
        test_change_time is not None
        and failed_test_time is not None
        and test_change_time < failed_test_time < first_edit
    )
    record = capture_record(run_id)
    return {
        **record,
        "test_change_before_red": test_change_time is not None,
        "failing_regression_before_production_edit": ordered,
        "failed_command_sha256": failed_command,
    }


def red_before_green(nonbug: dict[str, Any]) -> dict[str, Any]:
    current = [capture_record(run_id) for run_id in DIRECT_CURRENT]
    candidate = [red_sequence(run_id) for run_id in RED_CANDIDATE]
    fields = [
        "duration_seconds",
        "time_to_first_edit_seconds",
        "uncached_input_tokens",
        "total_command_count",
    ]
    current_median = median_fields(current, fields)
    candidate_median = median_fields(candidate, fields)
    comparison = {
        field + "_change_fraction": percent_change(
            current_median[field], candidate_median[field]
        )
        for field in fields
        if current_median[field]
    }
    nonbug_rows = [
        {
            "case": row["case"],
            "duration_seconds": row["agent"]["duration_seconds"],
            "command_count": row["agent"]["command_count"],
            "uncached_input_tokens": row["agent"]["uncached_input_tokens"],
            "pre_edit_failing_test_count": len(
                row["agent"]["pre_edit_failing_test_commands"]
            ),
            "passed": row["passed"],
            "checks": row["checks"],
        }
        for row in nonbug["cases"]
    ]
    gates = {
        "all_bug_runs_accepted": all(row["accepted"] for row in candidate),
        "honest_red_before_production_edit_2_of_2": all(
            row["failing_regression_before_production_edit"] for row in candidate
        ),
        "final_mutation_and_cli_quality_passed": all(
            row["functional_passed"] and row["local_ci_passed"] for row in candidate
        ),
        "bug_wall_overhead_under_15_percent": comparison[
            "duration_seconds_change_fraction"
        ]
        < 0.15,
        "nonbug_feature_and_refactor_do_not_run_red_first": bool(
            nonbug.get("passed") is True
            and len(nonbug_rows) == 2
            and all(
                row["passed"] is True and row["pre_edit_failing_test_count"] == 0
                for row in nonbug_rows
            )
        ),
    }
    return {
        "current": {"runs": current, "median": current_median},
        "candidate": {"runs": candidate, "median": candidate_median},
        "comparison": comparison,
        "nonbug_canaries": nonbug_rows,
        "nonbug_raw_receipt": file_receipt(NONBUG_RAW),
        "gates": gates,
        "passed": all(gates.values()),
        "limitations": [
            "Two bug runs and one run each for feature/refactor boundaries.",
            "Bug wall time improved in this small sample, but time to first production edit rose because the regression was proved first.",
        ],
    }


def performance_record(run_id: str) -> dict[str, Any]:
    record = capture_record(run_id)
    capture = ARTIFACTS / "runs" / run_id
    grade = read_json(capture / "grade.json")
    synthetic_count = grade.get("agent_gate_counts", {}).get("synthetic", 0)
    comparator_seen = False
    for wrapped in observed_events(capture):
        event = wrapped.get("event", {})
        item = event.get("item", {}) if isinstance(event, dict) else {}
        if item.get("type") == "command_execution" and event.get("type") == "item.completed":
            if "BENCHMARK_COMPARISON_PASS" in str(item.get("aggregated_output", "")):
                comparator_seen = True
    return {
        **record,
        "synthetic_agent_gate_count": synthetic_count,
        "comparator_acceptance_seen": comparator_seen,
        "performance": grade.get("performance"),
    }


def benchmark_comparator() -> dict[str, Any]:
    records = {
        name: performance_record(run_id) for name, run_id in PERFORMANCE_RUNS.items()
    }
    historical = records["historical_without_receipt"]
    contract = records["repository_contract_current_skill"]
    wording = records["extra_core_wording"]
    comparison = {
        "repository_contract_vs_historical_wall_reduction_fraction": reduction(
            historical["duration_seconds"], contract["duration_seconds"]
        ),
        "repository_contract_vs_historical_uncached_reduction_fraction": reduction(
            historical["uncached_input_tokens"], contract["uncached_input_tokens"]
        ),
        "extra_wording_vs_contract_wall_change_fraction": percent_change(
            contract["duration_seconds"], wording["duration_seconds"]
        ),
        "extra_wording_vs_contract_uncached_change_fraction": percent_change(
            contract["uncached_input_tokens"], wording["uncached_input_tokens"]
        ),
    }
    gates = {
        "all_runs_accepted": all(record["accepted"] for record in records.values()),
        "historical_run_repeated_final_synthetic": historical[
            "synthetic_agent_gate_count"
        ]
        == 3,
        "repository_contract_runs_baseline_and_final_once": contract[
            "synthetic_agent_gate_count"
        ]
        == 2,
        "repository_comparator_acceptance_observed": contract[
            "comparator_acceptance_seen"
        ],
        "extra_core_wording_has_no_quality_advantage": bool(
            wording["synthetic_agent_gate_count"] == 2
            and wording["comparator_acceptance_seen"]
            and wording["functional_passed"] == contract["functional_passed"]
            and wording["local_ci_passed"] == contract["local_ci_passed"]
        ),
    }
    return {
        "runs": records,
        "comparison": comparison,
        "gates": gates,
        "passed": all(gates.values()),
        "decision": (
            "Pilot the optional repository comparator/profile; do not add the extra "
            "core wording, which was slower and used more uncached input on the same fixture."
        ),
        "limitations": [
            "The repository-contract versus historical comparison is not a randomized pair.",
            "The same-fixture core-wording comparison has one run per arm and is variance-sensitive.",
        ],
    }


def lane_classification(lane: dict[str, Any]) -> dict[str, Any]:
    return {
        "arms": lane["arms"],
        "comparison": lane["comparison"],
        "design": lane["design"],
        "gates": lane["gates"],
        "raw_receipt": file_receipt(LANE_RAW),
        "passed": lane["passed"],
        "decision": (
            "Reject the 98-word explicit allowlist: both arms were perfect, while "
            "the allowlist was slower and used slightly more uncached input."
        ),
        "limitations": lane["limitations"],
    }


def source_manifest() -> dict[str, str]:
    paths = [
        LAB_ROOT / "lab" / "check_results.py",
        LAB_ROOT / "lab" / "summarize_next_live.py",
        LAB_ROOT / "lab" / "run_agent.py",
        LAB_ROOT / "lab" / "grade_run.py",
        LAB_ROOT / "lab" / "eval_lib.py",
        LAB_ROOT / "lab" / "evals" / "lane-cases.json",
        LAB_ROOT / "lab" / "live_policy" / "run_lane_ab.py",
        LAB_ROOT / "lab" / "live_policy" / "lane-output.schema.json",
        LAB_ROOT / "lab" / "live_policy" / "run_discovery_boundary_canaries.py",
        LAB_ROOT / "lab" / "live_policy" / "run_red_first_nonbug_canaries.py",
        LAB_ROOT / "subjects" / "combined-candidate" / "endurant-harness" / "SKILL.md",
        LAB_ROOT / "subjects" / "direct-budget" / "endurant-harness" / "SKILL.md",
        LAB_ROOT / "subjects" / "red-before-green" / "endurant-harness" / "SKILL.md",
        LAB_ROOT / "subjects" / "benchmark-receipt" / "endurant-harness" / "SKILL.md",
        LAB_ROOT
        / "fixtures"
        / "record-selection-receipt"
        / "template"
        / "scripts"
        / "benchmark_receipt.py",
        LAB_ROOT
        / "fixtures"
        / "record-selection-receipt"
        / "template"
        / ".agents"
        / "endurant-harness-benchmarks.json",
    ]
    return {
        path.relative_to(LAB_ROOT).as_posix(): sha256_file(path) for path in paths
    }


def build_result() -> dict[str, Any]:
    lane = read_json(LANE_RAW)
    boundaries = read_json(BOUNDARY_RAW)
    nonbug = read_json(NONBUG_RAW)
    deterministic = read_json(ARTIFACTS / "benchmarks" / "next-improvements.json")
    result = {
        "schema_version": 1,
        "environment": lane["environment"],
        "source": {"input_sha256": source_manifest()},
        "probe_and_preflight_reference": {
            "artifact": "artifacts/benchmarks/next-improvements.json",
            "sha256": sha256_file(
                ARTIFACTS / "benchmarks" / "next-improvements.json"
            ),
        },
        "discovery_stop_budget": discovery_budget(boundaries),
        "lane_classification": lane_classification(lane),
        "red_before_green": red_before_green(nonbug),
        "benchmark_comparator": benchmark_comparator(),
        "version_provenance": {
            "deterministic_cases": deterministic["version_provenance"],
            "active_session_reload_live_tested": False,
            "decision": (
                "Adopt current/stale/unknown receipt semantics only when the loaded "
                "release and package hash are supplied; do not claim an active task reloaded."
            ),
        },
    }
    result["evidence_manifest_sha256"] = canonical_sha256(
        named_live_evidence(result)
    )
    result["passed"] = all(
        section.get("passed") is True
        for section in result.values()
        if isinstance(section, dict) and "passed" in section
    )
    return result


def main() -> int:
    result = build_result()
    write_json(ARTIFACTS / "benchmarks" / "next-live.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
