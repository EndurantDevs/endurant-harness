#!/usr/bin/env python3
"""Fail closed when recorded local evidence does not meet promotion thresholds."""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import subprocess
from pathlib import Path
from typing import Any

from eval_lib import ARTIFACTS, LAB_ROOT, read_json, sha256_file


RUST_INPUT_PATHS = {
    "artifacts/benchmarks/model-runs.json": ARTIFACTS / "benchmarks" / "model-runs.json",
    "lab/benchmark_rust_runtime.py": LAB_ROOT / "lab" / "benchmark_rust_runtime.py",
    "lab/check_results.py": LAB_ROOT / "lab" / "check_results.py",
    "lab/eval_lib.py": LAB_ROOT / "lab" / "eval_lib.py",
    "lab/python_scan_kernel.py": LAB_ROOT / "lab" / "python_scan_kernel.py",
    "subjects/combined-candidate/endurant-harness/scripts/endurant.py": (
        LAB_ROOT
        / "subjects"
        / "combined-candidate"
        / "endurant-harness"
        / "scripts"
        / "endurant.py"
    ),
    "subjects/rust-runtime/runtime-spike/Cargo.lock": (
        LAB_ROOT / "subjects" / "rust-runtime" / "runtime-spike" / "Cargo.lock"
    ),
    "subjects/rust-runtime/runtime-spike/Cargo.toml": (
        LAB_ROOT / "subjects" / "rust-runtime" / "runtime-spike" / "Cargo.toml"
    ),
    "subjects/rust-runtime/runtime-spike/src/main.rs": (
        LAB_ROOT / "subjects" / "rust-runtime" / "runtime-spike" / "src" / "main.rs"
    ),
    "subjects/rust-runtime/runtime-spike/template.json": (
        LAB_ROOT / "subjects" / "rust-runtime" / "runtime-spike" / "template.json"
    ),
}
EXPECTED_RUST_GENERATED_INPUTS = {
    "fixture_manifest_sha256": {
        "edge": "d7e2da08b0e2683ab4daa606c6873d8b72fd360c9ee232883d889beec6044387",
        "heavy": "68a68e5c9d6bf700756262357e19e03989deb67a9ed8471571172f371ae5babc",
    },
    "one_command_plan_sha256": (
        "68b23d8318d58ae7a188e33add15b110e1937b07f0e39a01be52b60c732b1e34"
    ),
    "twelve_command_plan_sha256": (
        "d4fff14aec4d6e9320d70c5884eaf540dfa2c156e69264694cb2dab0e6cb7a1f"
    ),
}
EXPECTED_RUST_SCAN_CASES = {
    "heavy": (3, 40),
    "edge_full": (2, 50),
    "edge_limited": (3, 4),
}
NEXT_STATIC_INPUT_PATHS = {
    "lab/benchmark_next_improvements.py": LAB_ROOT / "lab" / "benchmark_next_improvements.py",
    "lab/eval_lib.py": LAB_ROOT / "lab" / "eval_lib.py",
    "lab/evals/lane-cases.json": LAB_ROOT / "lab" / "evals" / "lane-cases.json",
    "lab/proposals/benchmark_receipt.py": LAB_ROOT / "lab" / "proposals" / "benchmark_receipt.py",
    "lab/proposals/fast_preflight.py": LAB_ROOT / "lab" / "proposals" / "fast_preflight.py",
    "lab/proposals/lane_classifier.py": LAB_ROOT / "lab" / "proposals" / "lane_classifier.py",
    "lab/proposals/probe_relevance.py": LAB_ROOT / "lab" / "proposals" / "probe_relevance.py",
    "lab/proposals/version_provenance.py": LAB_ROOT / "lab" / "proposals" / "version_provenance.py",
    "subjects/combined-candidate/endurant-harness/scripts/endurant.py": (
        LAB_ROOT
        / "subjects"
        / "combined-candidate"
        / "endurant-harness"
        / "scripts"
        / "endurant.py"
    ),
}


def next_input_paths() -> dict[str, Path]:
    result = dict(NEXT_STATIC_INPUT_PATHS)
    fixture = LAB_ROOT / "fixtures" / "settings-override-correctness" / "template"
    for path in sorted(fixture.rglob("*")):
        if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
            continue
        result[path.relative_to(LAB_ROOT).as_posix()] = path
    return result


NEXT_LIVE_INPUT_PATHS = {
    "lab/check_results.py": LAB_ROOT / "lab" / "check_results.py",
    "lab/summarize_next_live.py": LAB_ROOT / "lab" / "summarize_next_live.py",
    "lab/run_agent.py": LAB_ROOT / "lab" / "run_agent.py",
    "lab/grade_run.py": LAB_ROOT / "lab" / "grade_run.py",
    "lab/eval_lib.py": LAB_ROOT / "lab" / "eval_lib.py",
    "lab/evals/lane-cases.json": LAB_ROOT / "lab" / "evals" / "lane-cases.json",
    "lab/live_policy/run_lane_ab.py": LAB_ROOT / "lab" / "live_policy" / "run_lane_ab.py",
    "lab/live_policy/lane-output.schema.json": (
        LAB_ROOT / "lab" / "live_policy" / "lane-output.schema.json"
    ),
    "lab/live_policy/run_discovery_boundary_canaries.py": (
        LAB_ROOT / "lab" / "live_policy" / "run_discovery_boundary_canaries.py"
    ),
    "lab/live_policy/run_red_first_nonbug_canaries.py": (
        LAB_ROOT / "lab" / "live_policy" / "run_red_first_nonbug_canaries.py"
    ),
    "subjects/combined-candidate/endurant-harness/SKILL.md": (
        LAB_ROOT / "subjects" / "combined-candidate" / "endurant-harness" / "SKILL.md"
    ),
    "subjects/direct-budget/endurant-harness/SKILL.md": (
        LAB_ROOT / "subjects" / "direct-budget" / "endurant-harness" / "SKILL.md"
    ),
    "subjects/red-before-green/endurant-harness/SKILL.md": (
        LAB_ROOT / "subjects" / "red-before-green" / "endurant-harness" / "SKILL.md"
    ),
    "subjects/benchmark-receipt/endurant-harness/SKILL.md": (
        LAB_ROOT / "subjects" / "benchmark-receipt" / "endurant-harness" / "SKILL.md"
    ),
    "fixtures/record-selection-receipt/template/scripts/benchmark_receipt.py": (
        LAB_ROOT
        / "fixtures"
        / "record-selection-receipt"
        / "template"
        / "scripts"
        / "benchmark_receipt.py"
    ),
    "fixtures/record-selection-receipt/template/.agents/endurant-harness-benchmarks.json": (
        LAB_ROOT
        / "fixtures"
        / "record-selection-receipt"
        / "template"
        / ".agents"
        / "endurant-harness-benchmarks.json"
    ),
}

EXPECTED_NEXT_LIVE_ENVIRONMENT = {
    "history_and_memories": "disabled",
    "model": "gpt-5.6-terra",
    "network": "disabled",
    "reasoning_effort": "low",
    "subagents": "disabled",
}
NEXT_LIVE_LANE_RAW = (
    ARTIFACTS / "runtime" / "lane-ab-20260810T221426Z" / "summary.json"
)
NEXT_LIVE_BOUNDARY_RAW = (
    ARTIFACTS
    / "runtime"
    / "boundary-canaries-20260810T222853Z-4b5e26"
    / "summary-rescored.json"
)
NEXT_LIVE_NONBUG_RAW = (
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


def committed_input_hashes(
    artifact_relative: str, input_paths: dict[str, Path]
) -> dict[str, str]:
    """Bind historical evidence to the commit that introduced its receipt."""
    environment = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"}
    revision = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", artifact_relative],
        cwd=LAB_ROOT,
        env=environment,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
        timeout=30,
    ).stdout.strip()
    if len(revision) != 40:
        return {}
    result: dict[str, str] = {}
    for relative in input_paths:
        blob = subprocess.run(
            ["git", "show", f"{revision}:{relative}"],
            cwd=LAB_ROOT,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if blob.returncode != 0:
            return {}
        result[relative] = hashlib.sha256(blob.stdout).hexdigest()
    return result


def probe_checks(payload: dict[str, Any]) -> dict[str, bool]:
    checks = {
        "probe_passed": payload.get("passed") is True,
        "real_aggregate_improved_95_percent": (
            payload.get("real_aggregate_root", {}).get("observed_improvement_fraction", 0)
            >= 0.95
        ),
    }
    for name in ("scoped_git", "standalone_nongit"):
        section = payload.get(name, {})
        current = section.get("current", {}).get("p50_seconds", 0)
        candidate = section.get("candidate", {}).get("p50_seconds", 0)
        checks[f"{name}_payload_equal"] = section.get("important_payload_equal") is True
        checks[f"{name}_regression_bounded"] = bool(
            current and candidate - current <= 0.05 and candidate <= current * 1.10
        )
    ignored_noise = payload.get("git_ignored_noise", {})
    for key in (
        "absolute_regression_bounded",
        "candidate_excludes_ignored",
        "candidate_excludes_deleted_tracked",
        "candidate_preserves_tracked_ignored",
        "candidate_preserves_visible_untracked",
    ):
        checks[f"git_ignored_noise_{key}"] = ignored_noise.get(key) is True
    return checks


def model_checks(payload: dict[str, Any]) -> dict[str, bool]:
    ordinary = payload.get("ordinary_combined_evaluation", {})
    reductions = ordinary.get("observed_reduction_fraction", {})
    performance = payload.get("performance_verification_evaluation", {})
    dogfood = payload.get("dogfood_performance_smoke", {})
    dogfood_performance = dogfood.get("performance", {})
    checks = {
        "ordinary_all_accepted": ordinary.get("all_accepted") is True,
        "ordinary_wall_reduction_15_percent": reductions.get("duration_seconds", 0) >= 0.15,
        "ordinary_uncached_reduction_15_percent": (
            reductions.get("uncached_input_tokens", 0) >= 0.15
        ),
        "performance_all_accepted": performance.get("all_accepted") is True,
        "dogfood_performance_accepted": dogfood.get("accepted") is True,
        "dogfood_performance_scope_and_local_proof": (
            dogfood.get("functional_passed") is True
            and dogfood.get("local_ci_preflight_passed") is True
            and dogfood.get("git_state_unchanged") is True
            and dogfood.get("subject_tree_unchanged") is True
            and dogfood.get("changed_paths")
            == ["src/record_selection.py", "tests/test_record_selection.py"]
            and dogfood.get("evaluator_integrity", {}).get("agent_event_log_valid")
            is True
            and dogfood.get("evaluator_integrity", {}).get(
                "agent_event_log_tampered"
            )
            is False
        ),
        "dogfood_performance_ordered_synthetic": all(
            dogfood_performance.get(key) is True
            for key in (
                "agent_baseline_seen",
                "agent_final_seen",
                "baseline_before_agent",
                "grader_final_after_edit",
                "threshold_passed",
            )
        ),
    }
    for arm in ("current", "combined_candidate"):
        evidence = performance.get(arm, {}).get("performance", {})
        checks[f"performance_{arm}_ordered_synthetic"] = all(
            evidence.get(key) is True
            for key in (
                "agent_baseline_seen",
                "agent_final_seen",
                "baseline_before_agent",
                "grader_final_after_edit",
                "threshold_passed",
            )
        )
    return checks


def finite_number(value: Any, *, positive: bool = False) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and (not positive or float(value) > 0)
    )


def close_number(left: Any, right: float, tolerance: float = 2e-8) -> bool:
    return finite_number(left) and abs(float(left) - right) <= tolerance


def sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def timing_receipt_valid(section: Any, repetitions: int) -> bool:
    if not isinstance(section, dict):
        return False
    python = section.get("python", {})
    rust = section.get("rust", {})
    if not isinstance(python, dict) or not isinstance(rust, dict):
        return False
    python_samples = python.get("samples_seconds")
    rust_samples = rust.get("samples_seconds")
    deltas = section.get("paired_deltas_seconds")
    if not all(isinstance(values, list) for values in (python_samples, rust_samples, deltas)):
        return False
    if not all(len(values) == repetitions for values in (python_samples, rust_samples, deltas)):
        return False
    if not all(
        finite_number(value, positive=True)
        for values in (python_samples, rust_samples)
        for value in values
    ) or not all(finite_number(value) for value in deltas):
        return False
    for observed, python_value, rust_value in zip(deltas, python_samples, rust_samples):
        if not close_number(observed, float(python_value) - float(rust_value)):
            return False
    if (
        section.get("repetitions") != repetitions
        or section.get("warmups_per_runtime") != 5
        or section.get("alternating_order") is not True
    ):
        return False

    def summary_valid(summary: dict[str, Any], samples: list[Any]) -> bool:
        ordered = sorted(float(value) for value in samples)
        p95 = ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]
        return (
            close_number(summary.get("p50_seconds"), statistics.median(ordered))
            and close_number(summary.get("p95_seconds"), p95)
            and close_number(summary.get("max_seconds"), max(ordered))
        )

    return (
        summary_valid(python, python_samples)
        and summary_valid(rust, rust_samples)
        and close_number(
            section.get("median_saved_seconds"),
            statistics.median(float(value) for value in deltas),
        )
        and close_number(
            section.get("median_speedup_ratio"),
            round(float(python["p50_seconds"]) / float(rust["p50_seconds"]), 3),
            tolerance=0.0001,
        )
    )


def resource_receipt_valid(section: Any) -> bool:
    if not isinstance(section, dict) or section.get("available") is not True:
        return False
    if section.get("repetitions") != 10 or section.get("cache_state") != (
        "page-cache-warm; no reboot or purge"
    ):
        return False
    metrics = section.get("metrics", {})
    required = {
        "max_rss_bytes",
        "page_faults",
        "voluntary_context_switches",
        "involuntary_context_switches",
        "instructions_retired",
        "cycles_elapsed",
    }
    return isinstance(metrics, dict) and set(metrics) == required and all(
        isinstance(value, dict)
        and finite_number(value.get("p50"))
        and finite_number(value.get("p95"))
        and float(value["p50"]) >= 0
        and float(value["p95"]) >= 0
        for value in metrics.values()
    ) and all(
        float(metrics[name]["p50"]) > 0
        for name in ("max_rss_bytes", "instructions_retired", "cycles_elapsed")
    )


def rust_checks(payload: dict[str, Any], model: dict[str, Any]) -> dict[str, bool]:
    return _rust_checks_impl(payload, model)


def next_improvements_checks(payload: dict[str, Any]) -> dict[str, bool]:
    """Recompute promotion facts for the isolated next-improvement receipt."""
    expected_inputs = committed_input_hashes(
        "artifacts/benchmarks/next-improvements.json", next_input_paths()
    )
    source = payload.get("source", {})
    input_sha256 = source.get("input_sha256", {}) if isinstance(source, dict) else {}

    probe = payload.get("probe_relevance", {})
    rows = probe.get("rows", []) if isinstance(probe, dict) else []
    probe_rows_valid = bool(
        isinstance(rows, list)
        and len(rows) == probe.get("cases") == 12
        and all(isinstance(row, dict) for row in rows)
    )
    current_top3 = candidate_top3 = current_noise = candidate_noise = 0
    current_paths = candidate_paths = 0
    if probe_rows_valid:
        current_top3 = sum(
            row.get("current_source_rank") in {1, 2, 3}
            and row.get("current_test_rank") in {1, 2, 3}
            for row in rows
        )
        candidate_top3 = sum(
            row.get("candidate_source_rank") in {1, 2, 3}
            and row.get("candidate_test_rank") in {1, 2, 3}
            for row in rows
        )
        current_noise = sum(
            row.get("current_noise", -1)
            for row in rows
            if isinstance(row.get("current_noise"), int)
        )
        candidate_noise = sum(
            row.get("candidate_noise", -1)
            for row in rows
            if isinstance(row.get("candidate_noise"), int)
        )
        current_paths = sum(
            row.get("current_paths", -1)
            for row in rows
            if isinstance(row.get("current_paths"), int)
        )
        candidate_paths = sum(
            row.get("candidate_paths", -1)
            for row in rows
            if isinstance(row.get("candidate_paths"), int)
        )
    top3 = probe.get("source_and_test_top3", {})
    path_count = probe.get("path_count", {})
    noise_fraction = probe.get("harness_noise_fraction", {})
    probe_latency = probe.get("full_probe_p50_ms", {})
    probe_path_bytes = probe.get("candidate_path_bytes_p50", {})
    probe_gates = probe.get("gates", {})
    probe_derived = {
        "top3_recall_95_percent": probe_rows_valid and candidate_top3 / 12 >= 0.95,
        "zero_unrelated_harness_noise": probe_rows_valid and candidate_noise == 0,
        "candidate_path_bytes_reduce_30_percent": bool(
            finite_number(probe_path_bytes.get("current"), positive=True)
            and finite_number(probe_path_bytes.get("candidate"))
            and float(probe_path_bytes["candidate"])
            <= float(probe_path_bytes["current"]) * 0.70
        ),
        "latency_added_under_25ms": bool(
            finite_number(probe_latency.get("current"), positive=True)
            and finite_number(probe_latency.get("candidate"), positive=True)
            and float(probe_latency["candidate"]) - float(probe_latency["current"]) < 25
        ),
    }
    probe_recomputed = bool(
        probe_rows_valid
        and probe.get("repeats_per_case") == 7
        and top3 == {"current": current_top3, "candidate": candidate_top3}
        and path_count == {"current": current_paths, "candidate": candidate_paths}
        and noise_fraction
        == {
            "current": round(current_noise / current_paths, 6),
            "candidate": round(candidate_noise / candidate_paths, 6),
        }
        and probe_gates == probe_derived
        and all(probe_derived.values())
    )

    preflight = payload.get("fast_preflight", {})
    legacy_ms = preflight.get("legacy_p50_ms")
    profile_ms = preflight.get("profile_p50_ms")
    preflight_reduction = (
        round((float(legacy_ms) - float(profile_ms)) / float(legacy_ms), 6)
        if finite_number(legacy_ms, positive=True) and finite_number(profile_ms, positive=True)
        else None
    )
    clean_checks = preflight.get("clean_checks", {})
    seeded = preflight.get("seeded_failures", [])
    expected_seeds = {
        "focused",
        "lint",
        "typecheck",
        "build",
        "generated-drift",
        "shared-package",
    }
    seed_map = {
        row.get("seed"): row
        for row in seeded
        if isinstance(row, dict) and isinstance(row.get("seed"), str)
    } if isinstance(seeded, list) else {}
    preflight_derived = {
        "covered_proof_reduces_20_percent": bool(
            preflight_reduction is not None and preflight_reduction >= 0.20
        ),
        "all_clean_checks_pass": bool(
            isinstance(clean_checks, dict)
            and set(clean_checks) == expected_seeds
            and all(value is True for value in clean_checks.values())
        ),
        "all_seeded_failures_caught": bool(
            set(seed_map) == expected_seeds
            and all(row.get("bundle_rejected") is True for row in seed_map.values())
        ),
        "clean_receipt_verified": preflight.get("clean_receipt_verified") is True,
        "all_seeded_receipts_rejected": bool(
            set(seed_map) == expected_seeds
            and all(row.get("receipt_rejected") is True for row in seed_map.values())
        ),
        "ci_only_failures_missed_by_focused": bool(
            set(seed_map) == expected_seeds
            and all(
                row.get("focused_only_passed") is True
                for name, row in seed_map.items()
                if name != "focused"
            )
        ),
        "covered_focused_runs_once": preflight.get("selected_ids")
        == ["bundle:local-ci", "synthetic"],
        "uncovered_synthetic_runs_once": preflight.get("selected_ids")
        == ["bundle:local-ci", "synthetic"],
        "no_profile_unchanged": preflight.get("no_profile_preserves_object") is True,
    }
    preflight_recomputed = bool(
        preflight.get("paired_repeats") == 31
        and close_number(preflight.get("proof_slice_reduction_fraction"), preflight_reduction or -1)
        and preflight.get("gates") == preflight_derived
        and all(preflight_derived.values())
    )

    benchmark = payload.get("benchmark_receipt", {})
    comparison = benchmark.get("comparison", {})
    baseline_value = comparison.get("baseline")
    final_value = comparison.get("final")
    improvement = (
        round((float(baseline_value) - float(final_value)) / float(baseline_value), 9)
        if finite_number(baseline_value, positive=True)
        and finite_number(final_value, positive=True)
        else None
    )
    mutants = benchmark.get("mutants", [])
    expected_mutants = {
        "argv",
        "env",
        "workload",
        "correctness",
        "metric-key",
        "threshold",
        "source-observation",
        "envelope",
    }
    mutant_map = {
        row.get("mutant"): row
        for row in mutants
        if isinstance(row, dict) and isinstance(row.get("mutant"), str)
    } if isinstance(mutants, list) else {}
    benchmark_timing_valid = bool(
        benchmark.get("microbenchmark_repeats") == 20_001
        and finite_number(benchmark.get("disabled_p50_ms"))
        and float(benchmark.get("disabled_p50_ms", -1)) >= 0
        and finite_number(benchmark.get("enabled_p50_ms"), positive=True)
        and finite_number(benchmark.get("enabled_p95_ms"), positive=True)
        and float(benchmark["enabled_p95_ms"]) >= float(benchmark["enabled_p50_ms"])
        and isinstance(benchmark.get("receipt_bytes"), int)
        and benchmark.get("receipt_bytes", 0) > 0
    )
    redundant_seconds = benchmark.get("observed_redundant_round_upper_bound_seconds")
    reference_seconds = benchmark.get("observed_reference_task_seconds")
    redundant_fraction = (
        round(float(redundant_seconds) / float(reference_seconds), 6)
        if finite_number(redundant_seconds, positive=True)
        and finite_number(reference_seconds, positive=True)
        else None
    )
    benchmark_derived = {
        "realistic_comparison_passes": bool(
            improvement is not None
            and comparison.get("primary_metric") == "p95_seconds"
            and close_number(comparison.get("improvement_fraction"), improvement)
            and comparison.get("threshold_fraction") == 0.4
            and comparison.get("passed") is (improvement >= 0.4)
        ),
        "all_mutants_rejected": bool(
            set(mutant_map) == expected_mutants
            and all(row.get("rejected") is True for row in mutant_map.values())
        ),
        "enabled_overhead_under_10ms": bool(
            benchmark_timing_valid and float(benchmark["enabled_p50_ms"]) < 10
        ),
    }
    benchmark_recomputed = bool(
        improvement is not None
        and close_number(comparison.get("improvement_fraction"), improvement)
        and redundant_fraction is not None
        and close_number(
            benchmark.get("observed_redundant_round_fraction"), redundant_fraction
        )
        and benchmark.get("gates") == benchmark_derived
        and all(benchmark_derived.values())
    )

    lane = payload.get("lane_classifier", {})
    reference = lane.get("reference_scorer", {})
    lane_recomputed = bool(
        lane.get("case_count") == 40
        and lane.get("direct_cases") == 20
        and lane.get("hazardous_cases") == 20
        and lane.get("live_model_required") is True
        and reference
        == {
            "total": 40,
            "correct": 40,
            "accuracy": 1.0,
            "hazardous_total": 20,
            "hazardous_escalated": 20,
            "hazardous_recall": 1.0,
            "direct_total": 20,
            "direct_selected": 20,
            "direct_recall": 1.0,
            "invalid_or_missing": 0,
        }
    )

    provenance = payload.get("version_provenance", {})
    provenance_cases = provenance.get("cases", [])
    expected_provenance_cases = {
        "current": "current",
        "stale-release": "stale",
        "stale-hash": "stale",
        "missing-release": "unknown",
        "missing-hash": "unknown",
        "tampered-same-version": "stale",
    }
    provenance_case_map = (
        {
            row.get("case"): row
            for row in provenance_cases
            if isinstance(row, dict) and isinstance(row.get("case"), str)
        }
        if isinstance(provenance_cases, list)
        else {}
    )
    provenance_rows_valid = bool(
        isinstance(provenance_cases, list)
        and len(provenance_cases) == 6
        and len(provenance_case_map) == 6
        and set(provenance_case_map) == set(expected_provenance_cases)
        and all(
            isinstance(row, dict)
            and row.get("expected") == expected_provenance_cases[row["case"]]
            and row.get("observed") == expected_provenance_cases[row["case"]]
            and row.get("correct") is True
            and isinstance(row.get("compact_bytes"), int)
            and row.get("compact_bytes") > 0
            for row in provenance_cases
        )
    )
    computed_max_bytes = (
        max(row["compact_bytes"] for row in provenance_cases)
        if provenance_rows_valid
        else -1
    )
    provenance_derived = {
        "all_states_correct": provenance_rows_valid,
        "overhead_under_1ms": bool(
            finite_number(provenance.get("p50_ms"))
            and float(provenance["p50_ms"]) < 1
        ),
        "compact_under_80_bytes": computed_max_bytes <= 80,
        "missing_never_current": bool(
            provenance_rows_valid
            and all(
                row.get("observed") != "current"
                for row in provenance_cases
                if str(row.get("case", "")).startswith("missing")
            )
        ),
    }
    provenance_recomputed = bool(
        provenance.get("microbenchmark_repeats") == 10_000
        and finite_number(provenance.get("p50_ms"))
        and finite_number(provenance.get("p95_ms"))
        and float(provenance.get("p95_ms", -1)) >= float(provenance.get("p50_ms", 0))
        and provenance.get("max_compact_bytes") == computed_max_bytes
        and provenance.get("gates") == provenance_derived
        and all(provenance_derived.values())
    )

    sections_pass = all(
        all(section.get("gates", {}).values())
        for section in payload.values()
        if isinstance(section, dict) and "gates" in section
    )
    return {
        "next_schema_is_current": payload.get("schema_version") == 1,
        "next_inputs_are_receipt_commit_bound": isinstance(input_sha256, dict)
        and input_sha256 == expected_inputs,
        "next_probe_recomputes": probe_recomputed,
        "next_preflight_recomputes": preflight_recomputed,
        "next_benchmark_recomputes": benchmark_recomputed,
        "next_lane_reference_recomputes": lane_recomputed,
        "next_provenance_recomputes": provenance_recomputed,
        "next_top_level_pass_recomputes": payload.get("passed") is True
        and sections_pass,
    }


def next_live_checks(
    payload: dict[str, Any], deterministic: dict[str, Any]
) -> dict[str, bool]:
    expected_inputs = committed_input_hashes(
        "artifacts/benchmarks/next-live.json", NEXT_LIVE_INPUT_PATHS
    )
    source = payload.get("source", {})
    input_sha256 = source.get("input_sha256", {}) if isinstance(source, dict) else {}

    def medians(records: Any, fields: list[str]) -> dict[str, float] | None:
        if not isinstance(records, list) or len(records) != 2:
            return None
        try:
            return {
                field: round(
                    statistics.median(float(record[field]) for record in records), 6
                )
                for field in fields
            }
        except (KeyError, TypeError, ValueError):
            return None

    def healthy_run(record: Any) -> bool:
        return bool(
            isinstance(record, dict)
            and record.get("accepted") is True
            and record.get("functional_passed") is True
            and record.get("local_ci_passed") is True
            and record.get("git_state_unchanged") is True
            and record.get("subject_tree_unchanged") is True
        )

    def receipt_tree_valid(value: Any) -> bool:
        if not isinstance(value, dict) or not value:
            return False
        if set(value) == {"bytes", "sha256"}:
            return bool(
                isinstance(value.get("bytes"), int)
                and value["bytes"] > 0
                and sha256_hex(value.get("sha256"))
            )
        return all(receipt_tree_valid(child) for child in value.values())

    def skills_match(records: Any, relative_skill: str) -> bool:
        expected = input_sha256.get(relative_skill)
        return bool(
            isinstance(records, list)
            and records
            and sha256_hex(expected)
            and all(
                isinstance(record, dict)
                and record.get("subject_skill_sha256") == expected
                for record in records
            )
        )

    def file_receipt_matches(receipt: Any, path: Path) -> bool:
        return bool(
            isinstance(receipt, dict)
            and path.is_file()
            and receipt
            == {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )

    discovery = payload.get("discovery_stop_budget", {})
    discovery_current = discovery.get("current", {})
    discovery_candidate = discovery.get("candidate", {})
    discovery_fields = [
        "duration_seconds",
        "time_to_first_edit_seconds",
        "first_tool_to_edit_seconds",
        "uncached_input_tokens",
        "pre_edit_command_count",
        "total_command_count",
        "pre_edit_output_bytes",
    ]
    discovery_current_runs = discovery_current.get("runs", [])
    if not isinstance(discovery_current_runs, list):
        discovery_current_runs = []
    discovery_candidate_runs = discovery_candidate.get("runs", [])
    if not isinstance(discovery_candidate_runs, list):
        discovery_candidate_runs = []
    discovery_current_median = medians(discovery_current_runs, discovery_fields)
    discovery_candidate_median = medians(discovery_candidate_runs, discovery_fields)
    discovery_comparison: dict[str, float] = {}
    if discovery_current_median is not None and discovery_candidate_median is not None:
        discovery_comparison = {
            field + "_reduction_fraction": round(
                (discovery_current_median[field] - discovery_candidate_median[field])
                / discovery_current_median[field],
                6,
            )
            for field in discovery_fields
            if discovery_current_median[field]
        }
    boundaries = discovery.get("boundary_canaries", [])
    boundary_valid = bool(
        isinstance(boundaries, list)
        and {row.get("canary") for row in boundaries if isinstance(row, dict)}
        == {"ambiguous-package-symbol", "conflicting-shared-contract"}
        and all(
            isinstance(row, dict)
            and row.get("passed") is True
            and row.get("file_change_items") == 0
            and isinstance(row.get("command_count"), int)
            and row.get("command_count") > 0
            and all(value is True for value in row.get("checks", {}).values())
            for row in boundaries
        )
    )
    discovery_derived = {
        "all_clear_runs_accepted": all(
            healthy_run(row)
            for row in discovery_current_runs + discovery_candidate_runs
        )
        and len(discovery_current_runs) == 2
        and len(discovery_candidate_runs) == 2,
        "clear_runs_max_two_pre_edit_commands": bool(
            len(discovery_candidate_runs) == 2
            and all(row.get("pre_edit_command_count", 99) <= 2 for row in discovery_candidate_runs)
        ),
        "clear_runs_max_five_total_commands": bool(
            len(discovery_candidate_runs) == 2
            and all(row.get("total_command_count", 99) <= 5 for row in discovery_candidate_runs)
        ),
        "pre_edit_output_did_not_increase": bool(
            discovery_current_median
            and discovery_candidate_median
            and discovery_candidate_median["pre_edit_output_bytes"]
            <= discovery_current_median["pre_edit_output_bytes"] * 1.10
        ),
        "median_wall_improved_15_percent": discovery_comparison.get(
            "duration_seconds_reduction_fraction", -1
        )
        >= 0.15,
        "uncached_input_non_regressing": discovery_comparison.get(
            "uncached_input_tokens_reduction_fraction", -1
        )
        >= 0,
        "both_boundary_canaries_escalated_without_edit": boundary_valid,
    }
    discovery_valid = bool(
        discovery_current_median is not None
        and discovery_candidate_median is not None
        and discovery_current.get("median") == discovery_current_median
        and discovery_candidate.get("median") == discovery_candidate_median
        and discovery.get("comparison") == discovery_comparison
        and discovery.get("gates") == discovery_derived
        and discovery.get("passed") is True
        and all(discovery_derived.values())
    )

    lane = payload.get("lane_classification", {})
    arms = lane.get("arms", {})
    current_lane = arms.get("current", {}) if isinstance(arms, dict) else {}
    allowlist = arms.get("allowlist", {}) if isinstance(arms, dict) else {}
    lane_comparison = {
        "accuracy_delta": round(
            float(allowlist.get("accuracy", -1)) - float(current_lane.get("accuracy", -1)), 6
        ),
        "hazardous_recall_delta": round(
            float(allowlist.get("hazardous_recall", -1))
            - float(current_lane.get("hazardous_recall", -1)),
            6,
        ),
        "direct_recall_delta": round(
            float(allowlist.get("direct_recall", -1))
            - float(current_lane.get("direct_recall", -1)),
            6,
        ),
        "median_duration_change_fraction": round(
            (
                float(allowlist.get("median_duration_seconds", 0))
                - float(current_lane.get("median_duration_seconds", 0))
            )
            / float(current_lane.get("median_duration_seconds", 1)),
            6,
        ),
        "median_uncached_input_change_fraction": round(
            (
                float(allowlist.get("median_uncached_input_tokens", 0))
                - float(current_lane.get("median_uncached_input_tokens", 0))
            )
            / float(current_lane.get("median_uncached_input_tokens", 1)),
            6,
        ),
    }
    lane_metrics_valid = all(
        arm.get("accuracy") == 1.0
        and arm.get("hazardous_recall") == 1.0
        and arm.get("direct_recall") == 1.0
        and arm.get("total") == 80
        and arm.get("correct") == 80
        and arm.get("tool_items") == 0
        and arm.get("runs") == 2
        and arm.get("all_runs_valid") is True
        for arm in (current_lane, allowlist)
    )
    lane_valid = bool(
        lane_metrics_valid
        and lane.get("comparison") == lane_comparison
        and lane.get("passed") is True
        and all(value is True for value in lane.get("gates", {}).values())
        and lane_comparison["accuracy_delta"] == 0
        and lane_comparison["hazardous_recall_delta"] == 0
        and lane_comparison["direct_recall_delta"] == 0
        and lane_comparison["median_duration_change_fraction"] > 0
    )

    red = payload.get("red_before_green", {})
    red_current = red.get("current", {})
    red_candidate = red.get("candidate", {})
    red_fields = [
        "duration_seconds",
        "time_to_first_edit_seconds",
        "uncached_input_tokens",
        "total_command_count",
    ]
    red_current_runs = red_current.get("runs", [])
    if not isinstance(red_current_runs, list):
        red_current_runs = []
    red_candidate_runs = red_candidate.get("runs", [])
    if not isinstance(red_candidate_runs, list):
        red_candidate_runs = []
    red_current_median = medians(red_current_runs, red_fields)
    red_candidate_median = medians(red_candidate_runs, red_fields)
    red_comparison: dict[str, float] = {}
    if red_current_median is not None and red_candidate_median is not None:
        red_comparison = {
            field + "_change_fraction": round(
                (red_candidate_median[field] - red_current_median[field])
                / red_current_median[field],
                6,
            )
            for field in red_fields
            if red_current_median[field]
        }
    nonbug = red.get("nonbug_canaries", [])
    nonbug_valid = bool(
        isinstance(nonbug, list)
        and {row.get("case") for row in nonbug if isinstance(row, dict)}
        == {"feature", "refactor"}
        and all(
            row.get("passed") is True
            and row.get("pre_edit_failing_test_count") == 0
            and all(value is True for value in row.get("checks", {}).values())
            for row in nonbug
        )
    )
    red_derived = {
        "all_bug_runs_accepted": len(red_current_runs) == 2
        and len(red_candidate_runs) == 2
        and all(healthy_run(row) for row in red_current_runs + red_candidate_runs),
        "honest_red_before_production_edit_2_of_2": len(red_candidate_runs) == 2
        and all(
            row.get("failing_regression_before_production_edit") is True
            and row.get("test_change_before_red") is True
            and sha256_hex(row.get("failed_command_sha256"))
            for row in red_candidate_runs
        ),
        "final_mutation_and_cli_quality_passed": len(red_candidate_runs) == 2
        and all(
            row.get("functional_passed") is True and row.get("local_ci_passed") is True
            for row in red_candidate_runs
        ),
        "bug_wall_overhead_under_15_percent": red_comparison.get(
            "duration_seconds_change_fraction", 1
        )
        < 0.15,
        "nonbug_feature_and_refactor_do_not_run_red_first": nonbug_valid,
    }
    red_valid = bool(
        red_current_median is not None
        and red_candidate_median is not None
        and red_current.get("median") == red_current_median
        and red_candidate.get("median") == red_candidate_median
        and red.get("comparison") == red_comparison
        and red.get("gates") == red_derived
        and red.get("passed") is True
        and all(red_derived.values())
    )

    benchmark = payload.get("benchmark_comparator", {})
    performance_runs = benchmark.get("runs", {})
    historical = performance_runs.get("historical_without_receipt", {})
    contract = performance_runs.get("repository_contract_current_skill", {})
    wording = performance_runs.get("extra_core_wording", {})
    benchmark_comparison = {
        "repository_contract_vs_historical_wall_reduction_fraction": round(
            (float(historical.get("duration_seconds", 0)) - float(contract.get("duration_seconds", 0)))
            / float(historical.get("duration_seconds", 1)),
            6,
        ),
        "repository_contract_vs_historical_uncached_reduction_fraction": round(
            (
                float(historical.get("uncached_input_tokens", 0))
                - float(contract.get("uncached_input_tokens", 0))
            )
            / float(historical.get("uncached_input_tokens", 1)),
            6,
        ),
        "extra_wording_vs_contract_wall_change_fraction": round(
            (float(wording.get("duration_seconds", 0)) - float(contract.get("duration_seconds", 0)))
            / float(contract.get("duration_seconds", 1)),
            6,
        ),
        "extra_wording_vs_contract_uncached_change_fraction": round(
            (
                float(wording.get("uncached_input_tokens", 0))
                - float(contract.get("uncached_input_tokens", 0))
            )
            / float(contract.get("uncached_input_tokens", 1)),
            6,
        ),
    }
    benchmark_derived = {
        "all_runs_accepted": all(
            healthy_run(row) for row in (historical, contract, wording)
        ),
        "historical_run_repeated_final_synthetic": historical.get(
            "synthetic_agent_gate_count"
        )
        == 3,
        "repository_contract_runs_baseline_and_final_once": contract.get(
            "synthetic_agent_gate_count"
        )
        == 2,
        "repository_comparator_acceptance_observed": contract.get(
            "comparator_acceptance_seen"
        )
        is True,
        "extra_core_wording_has_no_quality_advantage": bool(
            wording.get("synthetic_agent_gate_count") == 2
            and wording.get("comparator_acceptance_seen") is True
            and wording.get("functional_passed") == contract.get("functional_passed")
            and wording.get("local_ci_passed") == contract.get("local_ci_passed")
        ),
    }
    benchmark_valid = bool(
        set(performance_runs)
        == {
            "historical_without_receipt",
            "repository_contract_current_skill",
            "extra_core_wording",
        }
        and benchmark.get("comparison") == benchmark_comparison
        and benchmark.get("gates") == benchmark_derived
        and benchmark.get("passed") is True
        and all(benchmark_derived.values())
    )

    provenance = payload.get("version_provenance", {})
    provenance_valid = bool(
        provenance.get("deterministic_cases") == deterministic.get("version_provenance")
        and provenance.get("active_session_reload_live_tested") is False
        and deterministic.get("version_provenance", {}).get("gates", {}).get(
            "all_states_correct"
        )
        is True
    )
    evidence_rows = named_live_evidence(payload)
    raw_evidence = [
        row
        for row in evidence_rows
        if row["path"].rsplit("/", 1)[-1]
        in {
            "raw_receipts",
            "raw_receipt",
            "boundary_raw_receipt",
            "nonbug_raw_receipt",
        }
    ]
    skill_evidence = [
        row
        for row in evidence_rows
        if row["path"].endswith("/subject_skill_sha256")
    ]
    all_run_records = (
        discovery_current_runs
        + discovery_candidate_runs
        + red_current_runs
        + red_candidate_runs
        + [historical, contract, wording]
    )
    raw_file_pairs: list[tuple[Any, Path]] = []
    raw_filenames = {
        "summary": "summary.json",
        "grade": "grade.json",
        "metadata": "metadata.json",
        "codex_observed": "codex-observed.jsonl",
        "agent_events_observed": "agent-events-observed.jsonl",
    }
    for record in all_run_records:
        capture = ARTIFACTS / "runs" / str(record.get("run_id", ""))
        receipts = record.get("raw_receipts", {})
        for receipt_id, filename in raw_filenames.items():
            raw_file_pairs.append((receipts.get(receipt_id), capture / filename))
    raw_file_pairs.extend(
        [
            (discovery.get("boundary_raw_receipt"), NEXT_LIVE_BOUNDARY_RAW),
            (lane.get("raw_receipt"), NEXT_LIVE_LANE_RAW),
            (red.get("nonbug_raw_receipt"), NEXT_LIVE_NONBUG_RAW),
        ]
    )
    any_local_raw = any(path.exists() for _, path in raw_file_pairs)
    local_raw_evidence_valid = not any_local_raw or all(
        file_receipt_matches(receipt, path) for receipt, path in raw_file_pairs
    )
    local_raw_summary_valid = True
    if any_local_raw:
        try:
            from summarize_next_live import build_result

            rebuilt = build_result()
            rebuilt["source"]["input_sha256"] = expected_inputs
            local_raw_summary_valid = payload == rebuilt
        except (FileNotFoundError, KeyError, OSError, TypeError, ValueError):
            local_raw_summary_valid = False
    evidence_valid = bool(
        payload.get("environment") == EXPECTED_NEXT_LIVE_ENVIRONMENT
        and len(raw_evidence) == 14
        and all(receipt_tree_valid(row["value"]) for row in raw_evidence)
        and len(skill_evidence) == 11
        and skills_match(
            discovery_current_runs,
            "subjects/combined-candidate/endurant-harness/SKILL.md",
        )
        and skills_match(
            discovery_candidate_runs,
            "subjects/direct-budget/endurant-harness/SKILL.md",
        )
        and skills_match(
            red_current_runs,
            "subjects/combined-candidate/endurant-harness/SKILL.md",
        )
        and skills_match(
            red_candidate_runs,
            "subjects/red-before-green/endurant-harness/SKILL.md",
        )
        and skills_match(
            [historical, contract],
            "subjects/combined-candidate/endurant-harness/SKILL.md",
        )
        and skills_match(
            [wording],
            "subjects/benchmark-receipt/endurant-harness/SKILL.md",
        )
        and sha256_hex(payload.get("evidence_manifest_sha256"))
        and payload.get("evidence_manifest_sha256")
        == canonical_sha256(evidence_rows)
        and local_raw_evidence_valid
        and local_raw_summary_valid
    )
    deterministic_ref = payload.get("probe_and_preflight_reference", {})
    reference_valid = bool(
        deterministic_ref.get("artifact")
        == "artifacts/benchmarks/next-improvements.json"
        and deterministic_ref.get("sha256")
        == sha256_file(ARTIFACTS / "benchmarks" / "next-improvements.json")
    )
    return {
        "next_live_schema_is_current": payload.get("schema_version") == 1,
        "next_live_inputs_are_receipt_commit_bound": isinstance(input_sha256, dict)
        and input_sha256 == expected_inputs,
        "next_live_deterministic_reference_is_current": reference_valid,
        "next_live_discovery_recomputes": discovery_valid,
        "next_live_lane_recomputes": lane_valid,
        "next_live_red_recomputes": red_valid,
        "next_live_benchmark_recomputes": benchmark_valid,
        "next_live_provenance_is_conservative": provenance_valid,
        "next_live_evidence_manifest_is_bound": evidence_valid,
        "next_live_matches_local_raw_when_available": local_raw_summary_valid,
        "next_live_top_level_pass_is_current": payload.get("passed") is True,
    }


def _rust_checks_impl(
    payload: dict[str, Any], model: dict[str, Any]
) -> dict[str, bool]:
    source = payload.get("source", {})
    parity = payload.get("parity", {})
    full_cli = parity.get("full_cli", {})
    scan_cases = parity.get("limited_non_git_scan_matrix", [])
    task_impact = payload.get("task_impact", {})
    recommendation = payload.get("recommendation", {})
    input_sha256 = source.get("input_sha256", {})
    build = payload.get("build", {})
    rust_tests = payload.get("rust_tests", {})
    resources = payload.get("template_resources", {})
    generated_inputs = payload.get("generated_inputs")
    timing_sections = {
        "template": 50,
        "scan_kernel": 30,
        "one_command_optimistic_ceiling": 30,
        "twelve_command_optimistic_ceiling": 20,
    }
    timing_valid = all(
        timing_receipt_valid(payload.get(name), repetitions)
        for name, repetitions in timing_sections.items()
    )
    expected_inputs = committed_input_hashes(
        "artifacts/benchmarks/rust-runtime.json", RUST_INPUT_PATHS
    )
    dogfood_seconds = float(model["dogfood_performance_smoke"]["duration_seconds"])
    direct_seconds = float(
        model["ordinary_combined_evaluation"]["combined_candidate"]["median"][
            "duration_seconds"
        ]
    )
    optimistic_seconds = 0.0
    if timing_valid:
        optimistic_seconds = round(
            float(payload["scan_kernel"]["median_saved_seconds"])
            + float(payload["one_command_optimistic_ceiling"]["median_saved_seconds"]),
            9,
        )
    scan_case_map = {
        case.get("case"): case for case in scan_cases if isinstance(case, dict)
    }
    scan_receipts_valid = (
        len(scan_cases) == len(EXPECTED_RUST_SCAN_CASES)
        and set(scan_case_map) == set(EXPECTED_RUST_SCAN_CASES)
        and all(
            case.get("max_depth") == EXPECTED_RUST_SCAN_CASES[name][0]
            and case.get("max_items") == EXPECTED_RUST_SCAN_CASES[name][1]
            and case.get("equal") is True
            and sha256_hex(case.get("python_sha256"))
            and case.get("python_sha256") == case.get("rust_sha256")
            for name, case in scan_case_map.items()
        )
    )
    return {
        "rust_schema_is_current": payload.get("schema_version") == 2,
        "rust_retest_passed": payload.get("passed") is True,
        "rust_retest_targets_current_candidate": (
            source.get("candidate")
            == "subjects/combined-candidate/endurant-harness/scripts/endurant.py"
            and source.get("candidate_sha256")
            == expected_inputs[
                "subjects/combined-candidate/endurant-harness/scripts/endurant.py"
            ]
            and source.get("rust_source_sha256")
            == expected_inputs["subjects/rust-runtime/runtime-spike/src/main.rs"]
        ),
        "rust_retest_inputs_are_receipt_commit_bound": (
            isinstance(input_sha256, dict) and input_sha256 == expected_inputs
        ),
        "rust_generated_inputs_are_bound": (
            generated_inputs == EXPECTED_RUST_GENERATED_INPUTS
        ),
        "rust_limited_parity_exact": (
            parity.get("template_exact") is True
            and parity.get("limited_non_git_scan_exact") is True
            and scan_receipts_valid
            and payload.get("scan_cases") == ["heavy", "edge_full", "edge_limited"]
        ),
        "rust_full_cli_limit_is_explicit": (
            parity.get("full_cli_parity_implemented") is False
            and set(full_cli) == {"probe", "run", "fingerprint"}
            and all(
                result.get("candidate_exit_code") == 0
                and result.get("rust_exit_code") == 2
                and result.get("rust_accepts_valid_candidate_invocation") is False
                and result.get("equivalent_success") is False
                for result in full_cli.values()
            )
        ),
        "rust_timing_receipts_recompute": timing_valid,
        "rust_build_and_tests_recorded": (
            isinstance(build, dict)
            and build.get("kind") == "clean release build in a fresh target directory"
            and finite_number(build.get("seconds"), positive=True)
            and isinstance(build.get("binary_bytes"), int)
            and build.get("binary_bytes", 0) > 0
            and isinstance(build.get("binary_sha256"), str)
            and sha256_hex(build.get("binary_sha256"))
            and build.get("third_party_crates") == 0
            and isinstance(build.get("tested_targets"), list)
            and bool(build.get("tested_targets"))
            and isinstance(rust_tests, dict)
            and rust_tests.get("passed") is True
            and rust_tests.get("exit_code") == 0
            and rust_tests.get("tests_passed", 0) >= 2
            and finite_number(rust_tests.get("seconds"), positive=True)
        ),
        "rust_resource_receipts_recorded": (
            isinstance(resources, dict)
            and resource_receipt_valid(resources.get("python"))
            and resource_receipt_valid(resources.get("rust"))
        ),
        "rust_rewrite_rejected_as_immaterial": (
            recommendation.get("adopt_full_rust_rewrite") is False
            and task_impact.get("direct_lane_script_invocations") == 0
            and task_impact.get("direct_lane_estimated_saved_seconds") == 0.0
            and close_number(
                task_impact.get("direct_lane_reference_task_seconds"), direct_seconds
            )
            and close_number(
                task_impact.get("escalated_one_scan_one_run_optimistic_ceiling_seconds"),
                optimistic_seconds,
            )
            and close_number(
                task_impact.get("escalated_reference_task_seconds"), dogfood_seconds
            )
            and close_number(
                task_impact.get("escalated_reference_task_optimistic_fraction"),
                round(optimistic_seconds / dogfood_seconds, 6),
            )
            and 0 <= optimistic_seconds / dogfood_seconds < 0.01
            and task_impact.get("rewrite_adoption_threshold_fraction") == 0.15
        ),
        "rust_cross_platform_parity_not_overclaimed": (
            build.get("cross_platform_parity_verified") is False
        ),
    }


def publication_checks() -> dict[str, bool]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=LAB_ROOT,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    private_path_hits = []
    posix_user_root = "/" + "Users" + "/"
    windows_user_root = "C:" + "\\" + "Users" + "\\"
    for raw_path in listed.split(b"\0"):
        if not raw_path:
            continue
        path = LAB_ROOT / raw_path.decode("utf-8", errors="surrogateescape")
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if posix_user_root in text or windows_user_root in text:
            private_path_hits.append(path.relative_to(LAB_ROOT).as_posix())
    return {"tracked_files_have_no_absolute_user_paths": not private_path_hits}


def main() -> int:
    probe = read_json(ARTIFACTS / "benchmarks" / "combined-probe.json")
    model = read_json(ARTIFACTS / "benchmarks" / "model-runs.json")
    runner = read_json(ARTIFACTS / "benchmarks" / "runner-variants.json")
    rust = read_json(ARTIFACTS / "benchmarks" / "rust-runtime.json")
    next_improvements = read_json(
        ARTIFACTS / "benchmarks" / "next-improvements.json"
    )
    next_live = read_json(ARTIFACTS / "benchmarks" / "next-live.json")
    checks = {
        **probe_checks(probe),
        **model_checks(model),
        "runner_variants_passed": runner.get("passed") is True,
        "combined_disabled_overhead_under_20ms": abs(
            runner.get("overhead", {})
            .get("combined-candidate", {})
            .get("candidate_minus_current_seconds", 1)
        )
        <= 0.02,
        **rust_checks(rust, model),
        **next_improvements_checks(next_improvements),
        **next_live_checks(next_live, next_improvements),
        **publication_checks(),
    }
    result = {"passed": all(checks.values()), "checks": checks}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
