#!/usr/bin/env python3
"""Fail closed when recorded local evidence does not meet promotion thresholds."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from eval_lib import ARTIFACTS, LAB_ROOT, read_json


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
        **publication_checks(),
    }
    result = {"passed": all(checks.values()), "checks": checks}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
