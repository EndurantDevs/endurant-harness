#!/usr/bin/env python3
"""Run deterministic A/B tests for the next Endurant Harness proposals."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from eval_lib import ARTIFACTS, LAB_ROOT, read_json, sha256_file, write_json
from proposals import benchmark_receipt, fast_preflight, lane_classifier, probe_relevance
from proposals.version_provenance import receipt as provenance_receipt


CANDIDATE_SCRIPT = (
    LAB_ROOT
    / "subjects"
    / "combined-candidate"
    / "endurant-harness"
    / "scripts"
    / "endurant.py"
)
SETTINGS_FIXTURE = LAB_ROOT / "fixtures" / "settings-override-correctness" / "template"
LANE_CASES = LAB_ROOT / "lab" / "evals" / "lane-cases.json"
STATIC_INPUT_PATHS = [
    LAB_ROOT / "lab" / "benchmark_next_improvements.py",
    LAB_ROOT / "lab" / "eval_lib.py",
    LANE_CASES,
    LAB_ROOT / "lab" / "proposals" / "benchmark_receipt.py",
    LAB_ROOT / "lab" / "proposals" / "fast_preflight.py",
    LAB_ROOT / "lab" / "proposals" / "lane_classifier.py",
    LAB_ROOT / "lab" / "proposals" / "probe_relevance.py",
    LAB_ROOT / "lab" / "proposals" / "version_provenance.py",
    CANDIDATE_SCRIPT,
]


def input_manifest() -> dict[str, str]:
    paths = list(STATIC_INPUT_PATHS)
    paths.extend(
        path
        for path in sorted(SETTINGS_FIXTURE.rglob("*"))
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
    )
    return {
        path.relative_to(LAB_ROOT).as_posix(): sha256_file(path)
        for path in sorted(set(paths))
    }


def load_runtime() -> ModuleType:
    spec = importlib.util.spec_from_file_location("next_eval_endurant", CANDIDATE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load candidate runtime")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def invoke(argv: list[str], root: Path) -> tuple[float, subprocess.CompletedProcess[str]]:
    started = time.perf_counter()
    completed = subprocess.run(
        argv,
        cwd=root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return time.perf_counter() - started, completed


def median_ms(values: list[float]) -> float:
    return round(statistics.median(values) * 1000, 6)


def create_probe_fixture(root: Path) -> list[dict[str, str]]:
    symbols = {
        "python": [
            "select_records",
            "normalize_plan_rows",
            "encode_receipt_digest",
            "resolve_active_profile",
        ],
        "rust": [
            "parse_record_batch",
            "verify_plan_digest",
            "collect_git_paths",
            "enforce_deadline_budget",
        ],
        "typescript": [
            "selectRecords",
            "normalizePlanRows",
            "encodeReceiptDigest",
            "resolveActiveProfile",
        ],
    }
    extensions = {"python": "py", "rust": "rs", "typescript": "ts"}
    cases: list[dict[str, str]] = []
    for language, names in symbols.items():
        for symbol in names:
            extension = extensions[language]
            source = root / language / "src" / f"{symbol}.{extension}"
            test = root / language / "tests" / f"test_{symbol}.{extension}"
            source.parent.mkdir(parents=True, exist_ok=True)
            test.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(f"function {symbol}\n", encoding="utf-8")
            test.write_text(f"focused test imports {symbol}\n", encoding="utf-8")
            noise = root / "benchmarks" / "endurant-harness" / language / symbol
            noise.mkdir(parents=True, exist_ok=True)
            for index in range(12):
                (noise / f"noise-{index:02d}.md").write_text(
                    "duplicate stability behavior without changing the api\n",
                    encoding="utf-8",
                )
            cases.append(
                {
                    "symbol": symbol,
                    "task": f"Fix `{symbol}` duplicate stability behavior without changing its API",
                    "source": source.relative_to(root).as_posix(),
                    "test": test.relative_to(root).as_posix(),
                }
            )
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Synthetic Evaluator",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "synthetic probe fixture",
        ],
        cwd=root,
        check=True,
    )
    return cases


def build_probe(
    runtime: ModuleType,
    root: Path,
    task: str,
    candidate: bool,
) -> tuple[float, dict[str, Any], int, int]:
    original = runtime._candidate_paths
    if candidate:
        runtime._candidate_paths = lambda selected_root, selected_task, max_items: (
            probe_relevance.candidate_paths(
                runtime,
                selected_root,
                selected_task,
                max_items,
                fallback=original,
            )
        )
    args = argparse.Namespace(
        repo=str(root),
        task=task,
        max_depth=3,
        max_items=10,
        instruction_bytes=5000,
    )
    try:
        started = time.perf_counter()
        payload = runtime.build_probe(args)
        duration = time.perf_counter() - started
    finally:
        runtime._candidate_paths = original
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    candidate_bytes = len(json.dumps(payload["candidate_paths"]).encode("utf-8"))
    return duration, payload, len(encoded), candidate_bytes


def evaluate_probe(runtime: ModuleType, root: Path) -> dict[str, Any]:
    cases = create_probe_fixture(root)
    current_times: list[float] = []
    candidate_times: list[float] = []
    current_bytes: list[int] = []
    candidate_bytes: list[int] = []
    current_path_bytes: list[int] = []
    candidate_path_bytes: list[int] = []
    rows: list[dict[str, Any]] = []
    for case_index, case in enumerate(cases):
        build_probe(runtime, root, case["task"], False)
        build_probe(runtime, root, case["task"], True)
        current_payload: dict[str, Any] = {}
        candidate_payload: dict[str, Any] = {}
        for repeat in range(7):
            order = [False, True] if (case_index + repeat) % 2 == 0 else [True, False]
            for arm in order:
                duration, payload, output_bytes, path_bytes = build_probe(
                    runtime, root, case["task"], arm
                )
                if arm:
                    candidate_times.append(duration)
                    candidate_bytes.append(output_bytes)
                    candidate_path_bytes.append(path_bytes)
                    candidate_payload = payload
                else:
                    current_times.append(duration)
                    current_bytes.append(output_bytes)
                    current_path_bytes.append(path_bytes)
                    current_payload = payload
        def ranks(payload: dict[str, Any]) -> tuple[int | None, int | None, int, int]:
            paths = [path.removeprefix("./") for path in payload["candidate_paths"]]
            source_rank = paths.index(case["source"]) + 1 if case["source"] in paths else None
            test_rank = paths.index(case["test"]) + 1 if case["test"] in paths else None
            noise = sum("endurant-harness" in path for path in paths)
            return source_rank, test_rank, noise, len(paths)
        current_rank = ranks(current_payload)
        candidate_rank = ranks(candidate_payload)
        rows.append(
            {
                "symbol": case["symbol"],
                "current_source_rank": current_rank[0],
                "current_test_rank": current_rank[1],
                "candidate_source_rank": candidate_rank[0],
                "candidate_test_rank": candidate_rank[1],
                "current_noise": current_rank[2],
                "candidate_noise": candidate_rank[2],
                "current_paths": current_rank[3],
                "candidate_paths": candidate_rank[3],
            }
        )
    current_top3 = sum(
        row["current_source_rank"] in {1, 2, 3} and row["current_test_rank"] in {1, 2, 3}
        for row in rows
    )
    candidate_top3 = sum(
        row["candidate_source_rank"] in {1, 2, 3}
        and row["candidate_test_rank"] in {1, 2, 3}
        for row in rows
    )
    current_noise = sum(row["current_noise"] for row in rows)
    candidate_noise = sum(row["candidate_noise"] for row in rows)
    current_paths = sum(row["current_paths"] for row in rows)
    candidate_paths = sum(row["candidate_paths"] for row in rows)
    return {
        "cases": len(rows),
        "repeats_per_case": 7,
        "source_and_test_top3": {"current": current_top3, "candidate": candidate_top3},
        "harness_noise_fraction": {
            "current": round(current_noise / current_paths, 6) if current_paths else 0,
            "candidate": round(candidate_noise / candidate_paths, 6) if candidate_paths else 0,
        },
        "path_count": {"current": current_paths, "candidate": candidate_paths},
        "full_probe_p50_ms": {
            "current": median_ms(current_times),
            "candidate": median_ms(candidate_times),
        },
        "full_probe_bytes_p50": {
            "current": statistics.median(current_bytes),
            "candidate": statistics.median(candidate_bytes),
        },
        "candidate_path_bytes_p50": {
            "current": statistics.median(current_path_bytes),
            "candidate": statistics.median(candidate_path_bytes),
        },
        "rows": rows,
        "gates": {
            "top3_recall_95_percent": candidate_top3 / len(rows) >= 0.95,
            "zero_unrelated_harness_noise": candidate_noise == 0,
            "candidate_path_bytes_reduce_30_percent": (
                statistics.median(candidate_path_bytes)
                <= statistics.median(current_path_bytes) * 0.70
            ),
            "latency_added_under_25ms": (
                median_ms(candidate_times) - median_ms(current_times) < 25
            ),
        },
    }


def create_ci_fixture(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "generated").mkdir()
    (root / "shared").mkdir()
    (root / "src" / "focused.txt").write_text("pass\n", encoding="utf-8")
    (root / "src" / "clean.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "src" / "typed.py").write_text("VALUE: int = 1\n", encoding="utf-8")
    (root / "generated" / "output.txt").write_text("generated\n", encoding="utf-8")
    digest = __import__("hashlib").sha256((root / "generated" / "output.txt").read_bytes())
    (root / "generated" / "expected.sha256").write_text(
        digest.hexdigest() + "\n", encoding="utf-8"
    )
    (root / "shared" / "status.txt").write_text("pass\n", encoding="utf-8")


def evaluate_fast_preflight(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    profile = {
        "schema_version": 1,
        "checks": {
            "focused": {"argv": ["python3", "scripts/verify.py", "focused"]},
            "synthetic": {"argv": ["python3", "scripts/verify.py", "synthetic"]},
            "lint": {"argv": ["python3", "scripts/verify.py", "lint"]},
            "typecheck": {"argv": ["python3", "scripts/verify.py", "typecheck"]},
            "build": {"argv": ["python3", "scripts/verify.py", "build"]},
            "generated-drift": {"argv": ["python3", "scripts/verify.py", "generated"]},
            "shared-package": {"argv": ["python3", "scripts/verify.py", "shared"]},
        },
        "bundles": {
            "local-ci": {
                "argv": ["python3", "scripts/verify.py", "fast-preflight"],
                "covers": [
                    "focused",
                    "lint",
                    "typecheck",
                    "build",
                    "generated-drift",
                    "shared-package",
                ],
                "receipt": {
                    "required_check_ids": [
                        "focused",
                        "lint",
                        "typecheck",
                        "build",
                        "generated-drift",
                        "shared-package",
                    ]
                },
            }
        },
    }
    original = [{"id": "focused"}, {"id": "local-ci"}, {"id": "synthetic"}]
    selected = fast_preflight.resolve(
        profile,
        required_checks=["focused", "synthetic"],
        bundle_id="local-ci",
        original_commands=original,
    )
    fallback = fast_preflight.resolve(
        None,
        required_checks=["focused"],
        bundle_id="local-ci",
        original_commands=original,
    )

    legacy_times: list[float] = []
    profile_times: list[float] = []
    for repeat in range(31):
        pair_root = root / f"timing-{repeat:02d}"
        orders = ["legacy", "profile"] if repeat % 2 == 0 else ["profile", "legacy"]
        for arm in orders:
            arm_root = pair_root / arm
            shutil.copytree(SETTINGS_FIXTURE, arm_root)
            started = time.perf_counter()
            commands = (
                [
                    [sys.executable, "scripts/verify.py", "focused"],
                    [sys.executable, "scripts/verify.py", "ci-preflight"],
                ]
                if arm == "legacy"
                else [[sys.executable, "scripts/verify.py", "ci-preflight"]]
            )
            for command in commands:
                _, completed = invoke(command, arm_root)
                if completed.returncode != 0:
                    raise RuntimeError(completed.stdout + completed.stderr)
            duration = time.perf_counter() - started
            (legacy_times if arm == "legacy" else profile_times).append(duration)

    clean = root / "ci-clean"
    create_ci_fixture(clean)
    clean_checks = fast_preflight.synthetic_checks(clean)
    final_fingerprint = "synthetic-final-fingerprint"
    required_receipt_ids = profile["bundles"]["local-ci"]["receipt"][
        "required_check_ids"
    ]

    def receipt_for(checks: dict[str, bool]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "profile_sha256": fast_preflight.canonical_sha256(profile),
            "verification_sha256": final_fingerprint,
            "checks": [
                {"id": check_id, "passed": checks[check_id]}
                for check_id in required_receipt_ids
            ],
        }

    clean_receipt_verified = fast_preflight.verify_receipt(
        profile,
        "local-ci",
        receipt_for(clean_checks),
        final_fingerprint,
    )
    mutations: dict[str, Callable[[Path], None]] = {
        "focused": lambda selected_root: (selected_root / "src" / "focused.txt").write_text(
            "fail\n", encoding="utf-8"
        ),
        "lint": lambda selected_root: (selected_root / "src" / "clean.py").write_text(
            "VALUE = 1  \n", encoding="utf-8"
        ),
        "typecheck": lambda selected_root: (selected_root / "src" / "typed.py").write_text(
            'VALUE: int = "wrong"\n', encoding="utf-8"
        ),
        "build": lambda selected_root: (selected_root / "src" / "broken.py").write_text(
            "def broken(:\n", encoding="utf-8"
        ),
        "generated-drift": lambda selected_root: (
            selected_root / "generated" / "output.txt"
        ).write_text("changed\n", encoding="utf-8"),
        "shared-package": lambda selected_root: (
            selected_root / "shared" / "status.txt"
        ).write_text("fail\n", encoding="utf-8"),
    }
    mutation_rows = []
    for name, mutate in mutations.items():
        target = root / f"seed-{name}"
        shutil.copytree(clean, target)
        mutate(target)
        checks = fast_preflight.synthetic_checks(target)
        mutation_rows.append(
            {
                "seed": name,
                "bundle_rejected": not all(checks.values()),
                "receipt_rejected": not fast_preflight.verify_receipt(
                    profile,
                    "local-ci",
                    receipt_for(checks),
                    final_fingerprint,
                ),
                "focused_only_passed": checks["focused"],
            }
        )
    legacy = statistics.median(legacy_times)
    candidate = statistics.median(profile_times)
    return {
        "paired_repeats": 31,
        "legacy_p50_ms": round(legacy * 1000, 6),
        "profile_p50_ms": round(candidate * 1000, 6),
        "proof_slice_reduction_fraction": round((legacy - candidate) / legacy, 6),
        "selected_ids": [item["id"] for item in selected],
        "no_profile_preserves_object": fallback is original,
        "clean_receipt_verified": clean_receipt_verified,
        "clean_checks": clean_checks,
        "seeded_failures": mutation_rows,
        "gates": {
            "covered_proof_reduces_20_percent": (legacy - candidate) / legacy >= 0.20,
            "all_clean_checks_pass": all(clean_checks.values()),
            "all_seeded_failures_caught": all(row["bundle_rejected"] for row in mutation_rows),
            "clean_receipt_verified": clean_receipt_verified,
            "all_seeded_receipts_rejected": all(
                row["receipt_rejected"] for row in mutation_rows
            ),
            "ci_only_failures_missed_by_focused": all(
                row["focused_only_passed"]
                for row in mutation_rows
                if row["seed"] != "focused"
            ),
            "covered_focused_runs_once": [item["id"] for item in selected].count(
                "bundle:local-ci"
            )
            == 1
            and "focused" not in [item["id"] for item in selected],
            "uncovered_synthetic_runs_once": [item["id"] for item in selected].count(
                "synthetic"
            )
            == 1,
            "no_profile_unchanged": fallback is original,
        },
    }


def evaluate_benchmark_receipt(root: Path) -> dict[str, Any]:
    (root / "src").mkdir(parents=True)
    (root / "scripts").mkdir()
    source = root / "src" / "record_selection.py"
    workload = root / "scripts" / "verify.py"
    source.write_text("BASELINE = True\n", encoding="utf-8")
    workload.write_text("WORKLOAD = 'stable'\n", encoding="utf-8")
    profile = {
        "benchmark_id": "record-selection",
        "argv": ["python3", "scripts/verify.py", "synthetic"],
        "cwd": ".",
        "env": {"PYTHONDONTWRITEBYTECODE": "1"},
        "source_files": ["src/record_selection.py"],
        "workload_files": ["scripts/verify.py"],
        "correctness_keys": ["output_digest", "result_count"],
        "metric_schema": {
            "median_seconds": {"unit": "seconds", "direction": "lower"},
            "p95_seconds": {"unit": "seconds", "direction": "lower"},
            "samples_seconds": {"unit": "seconds", "direction": "lower"},
        },
        "primary_metric": "p95_seconds",
        "minimum_improvement_fraction": 0.4,
    }
    baseline_event = {
        "output_digest": "457d0129b7e5",
        "result_count": 4002,
        "metrics": {
            "median_seconds": 0.215,
            "p95_seconds": 0.231281333,
            "samples_seconds": [0.211, 0.219, 0.231281333],
        },
    }
    baseline = benchmark_receipt.build_receipt(profile, baseline_event, root, "baseline")
    baseline_source = baseline["body"]["source"]
    source.write_text("BASELINE = False\n", encoding="utf-8")
    final_event = {
        "output_digest": "457d0129b7e5",
        "result_count": 4002,
        "metrics": {
            "median_seconds": 0.00072,
            "p95_seconds": 0.000790916,
            "samples_seconds": [0.00070, 0.00072, 0.000790916],
        },
    }
    final = benchmark_receipt.build_receipt(profile, final_event, root, "final")
    final_source = final["body"]["source"]
    comparison = benchmark_receipt.compare(
        baseline,
        final,
        observed_baseline_source=baseline_source,
        observed_final_source=final_source,
    )

    mutants: dict[str, Callable[[dict[str, Any], dict[str, Any]], None]] = {
        "argv": lambda before, after: after["body"]["workload"]["argv"].append("--changed"),
        "env": lambda before, after: after["body"]["workload"]["env"].update({"MODE": "x"}),
        "workload": lambda before, after: after["body"]["workload"]["files"].update(
            {"scripts/verify.py": "0" * 64}
        ),
        "correctness": lambda before, after: after["body"]["correctness"].update(
            {"output_digest": "changed"}
        ),
        "metric-key": lambda before, after: after["body"]["metrics"].pop("median_seconds"),
        "threshold": lambda before, after: after["body"].update(
            {"minimum_improvement_fraction": 0.01}
        ),
        "source-observation": lambda before, after: None,
        "envelope": lambda before, after: after.update({"receipt_sha256": "0" * 64}),
    }
    mutation_rows = []
    for name, mutate in mutants.items():
        before = copy.deepcopy(baseline)
        after = copy.deepcopy(final)
        mutate(before, after)
        if name not in {"envelope", "source-observation"}:
            after["receipt_sha256"] = benchmark_receipt.canonical_sha256(after["body"])
        observed_final = {"src/record_selection.py": "0" * 64} if name == "source-observation" else final_source
        try:
            benchmark_receipt.compare(
                before,
                after,
                observed_baseline_source=baseline_source,
                observed_final_source=observed_final,
            )
            rejected = False
        except ValueError:
            rejected = True
        mutation_rows.append({"mutant": name, "rejected": rejected})

    disabled_samples: list[float] = []
    enabled_samples: list[float] = []
    for _ in range(20_001):
        started = time.perf_counter_ns()
        benchmark_receipt.optional_compare(False)
        disabled_samples.append((time.perf_counter_ns() - started) / 1_000_000)
        started = time.perf_counter_ns()
        first = benchmark_receipt.build_receipt(profile, baseline_event, root, "baseline")
        second = benchmark_receipt.build_receipt(profile, final_event, root, "final")
        first["body"]["source"] = baseline_source
        first["receipt_sha256"] = benchmark_receipt.canonical_sha256(first["body"])
        benchmark_receipt.optional_compare(
            True,
            first,
            second,
            observed_baseline_source=baseline_source,
            observed_final_source=final_source,
        )
        enabled_samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "comparison": comparison,
        "receipt_bytes": len(json.dumps(baseline, sort_keys=True).encode("utf-8")),
        "mutants": mutation_rows,
        "microbenchmark_repeats": 20_001,
        "disabled_p50_ms": round(statistics.median(disabled_samples), 6),
        "enabled_p50_ms": round(statistics.median(enabled_samples), 6),
        "enabled_p95_ms": round(sorted(enabled_samples)[19_000], 6),
        "observed_redundant_round_upper_bound_seconds": 6.993179,
        "observed_reference_task_seconds": 81.943234,
        "observed_redundant_round_fraction": round(6.993179 / 81.943234, 6),
        "gates": {
            "realistic_comparison_passes": comparison["passed"] is True,
            "all_mutants_rejected": all(row["rejected"] for row in mutation_rows),
            "enabled_overhead_under_10ms": statistics.median(enabled_samples) < 10,
        },
    }


def evaluate_provenance() -> dict[str, Any]:
    current_release = "vNext-1"
    current_hash = "a" * 64
    cases = [
        ("current", current_release, current_hash, "current"),
        ("stale-release", "vNext-0", current_hash, "stale"),
        ("stale-hash", current_release, "b" * 64, "stale"),
        ("missing-release", None, current_hash, "unknown"),
        ("missing-hash", current_release, None, "unknown"),
        ("tampered-same-version", current_release, "c" * 64, "stale"),
    ]
    rows = []
    samples = []
    for name, loaded_release, loaded_hash, expected in cases:
        result = provenance_receipt(
            current_release=current_release,
            current_package_hash=current_hash,
            loaded_release=loaded_release,
            loaded_package_hash=loaded_hash,
        )
        rows.append(
            {
                "case": name,
                "expected": expected,
                "observed": result["state"],
                "correct": result["state"] == expected,
                "compact_bytes": result["compact_bytes"],
            }
        )
    for _ in range(10_000):
        started = time.perf_counter_ns()
        provenance_receipt(
            current_release=current_release,
            current_package_hash=current_hash,
            loaded_release=current_release,
            loaded_package_hash=current_hash,
        )
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "cases": rows,
        "microbenchmark_repeats": len(samples),
        "p50_ms": round(statistics.median(samples), 6),
        "p95_ms": round(sorted(samples)[9_500], 6),
        "max_compact_bytes": max(row["compact_bytes"] for row in rows),
        "gates": {
            "all_states_correct": all(row["correct"] for row in rows),
            "overhead_under_1ms": statistics.median(samples) < 1,
            "compact_under_80_bytes": max(row["compact_bytes"] for row in rows) <= 80,
            "missing_never_current": all(
                row["observed"] != "current"
                for row in rows
                if row["case"].startswith("missing")
            ),
        },
    }


def evaluate_lane_corpus() -> dict[str, Any]:
    cases = read_json(LANE_CASES)["cases"]
    expected = {case["id"]: case["expected_lane"] for case in cases}
    reference = lane_classifier.score(cases, expected)
    return {
        "case_count": len(cases),
        "direct_cases": reference["direct_total"],
        "hazardous_cases": reference["hazardous_total"],
        "reference_scorer": {
            key: value for key, value in reference.items() if key != "rows"
        },
        "live_model_required": True,
    }


def main() -> int:
    runtime = load_runtime()
    with tempfile.TemporaryDirectory(prefix="endurant-next-evals-") as temp_value:
        temp = Path(temp_value)
        result = {
            "schema_version": 1,
            "scope": "Isolated deterministic proposal tests; live policy results are merged separately.",
            "source": {"input_sha256": input_manifest()},
            "probe_relevance": evaluate_probe(runtime, temp / "probe"),
            "fast_preflight": evaluate_fast_preflight(temp / "preflight"),
            "benchmark_receipt": evaluate_benchmark_receipt(temp / "receipt"),
            "lane_classifier": evaluate_lane_corpus(),
            "version_provenance": evaluate_provenance(),
        }
    result["passed"] = all(
        all(section.get("gates", {}).values())
        for section in result.values()
        if isinstance(section, dict) and "gates" in section
    )
    output = ARTIFACTS / "benchmarks" / "next-improvements.json"
    write_json(output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
