#!/usr/bin/env python3
"""Retest the limited Rust runtime spike against the combined candidate."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from eval_lib import ARTIFACTS, LAB_ROOT, read_json, sha256_file, write_json


PYTHON_RUNTIME = (
    LAB_ROOT
    / "subjects"
    / "combined-candidate"
    / "endurant-harness"
    / "scripts"
    / "endurant.py"
)
CRATE = LAB_ROOT / "subjects" / "rust-runtime" / "runtime-spike"
RUST_SOURCE = CRATE / "src" / "main.rs"
PYTHON_SCAN = LAB_ROOT / "lab" / "python_scan_kernel.py"
MODEL_RUNS = ARTIFACTS / "benchmarks" / "model-runs.json"
BENCHMARK_SCRIPT = Path(__file__).resolve()
INPUT_PATHS = {
    "artifacts/benchmarks/model-runs.json": MODEL_RUNS,
    "lab/benchmark_rust_runtime.py": BENCHMARK_SCRIPT,
    "lab/check_results.py": LAB_ROOT / "lab" / "check_results.py",
    "lab/eval_lib.py": LAB_ROOT / "lab" / "eval_lib.py",
    "lab/python_scan_kernel.py": PYTHON_SCAN,
    "subjects/combined-candidate/endurant-harness/scripts/endurant.py": PYTHON_RUNTIME,
    "subjects/rust-runtime/runtime-spike/Cargo.lock": CRATE / "Cargo.lock",
    "subjects/rust-runtime/runtime-spike/Cargo.toml": CRATE / "Cargo.toml",
    "subjects/rust-runtime/runtime-spike/src/main.rs": RUST_SOURCE,
    "subjects/rust-runtime/runtime-spike/template.json": CRATE / "template.json",
}


def run_raw(
    argv: list[str], cwd: Path = LAB_ROOT
) -> tuple[float, subprocess.CompletedProcess[bytes]]:
    started = time.perf_counter()
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return time.perf_counter() - started, completed


def invoke(argv: list[str], cwd: Path = LAB_ROOT) -> tuple[float, bytes, bytes]:
    duration, completed = run_raw(argv, cwd)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {argv!r}\n"
            + completed.stderr.decode("utf-8", errors="replace")[-2000:]
        )
    return duration, completed.stdout, completed.stderr


def version(argv: list[str]) -> str:
    return invoke(argv)[1].decode("utf-8", errors="replace").strip()


def percentile(samples: list[float | int], fraction: float) -> float | int:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def summarize(samples: list[float]) -> dict[str, Any]:
    return {
        "samples_seconds": [round(value, 9) for value in samples],
        "p50_seconds": round(statistics.median(samples), 9),
        "p95_seconds": round(float(percentile(samples, 0.95)), 9),
        "max_seconds": round(max(samples), 9),
    }


def paired(
    python_argv: list[str], rust_argv: list[str], *, repetitions: int, warmups: int = 5
) -> dict[str, Any]:
    for _ in range(warmups):
        invoke(python_argv)
        invoke(rust_argv)
    python_samples: list[float] = []
    rust_samples: list[float] = []
    paired_deltas: list[float] = []
    for index in range(repetitions):
        order = (("python", python_argv), ("rust", rust_argv))
        if index % 2:
            order = tuple(reversed(order))
        pair: dict[str, float] = {}
        for name, argv in order:
            duration, _, _ = invoke(argv)
            (python_samples if name == "python" else rust_samples).append(duration)
            pair[name] = duration
        paired_deltas.append(pair["python"] - pair["rust"])
    python_summary = summarize(python_samples)
    rust_summary = summarize(rust_samples)
    python_p50 = float(python_summary["p50_seconds"])
    rust_p50 = float(rust_summary["p50_seconds"])
    return {
        "repetitions": repetitions,
        "warmups_per_runtime": warmups,
        "alternating_order": True,
        "python": python_summary,
        "rust": rust_summary,
        "paired_deltas_seconds": [round(value, 9) for value in paired_deltas],
        "median_saved_seconds": round(statistics.median(paired_deltas), 9),
        "median_speedup_ratio": round(python_p50 / rust_p50, 3) if rust_p50 else None,
    }


def create_scan_fixtures(root: Path) -> dict[str, tuple[Path, int, int]]:
    heavy = root / "heavy"
    (heavy / ".github" / "workflows").mkdir(parents=True)
    (heavy / ".github" / "workflows" / "ci.yml").write_text(
        "name: ci\n", encoding="utf-8"
    )
    for package_index in range(30):
        package = heavy / f"package-{package_index:02d}"
        source = package / "src"
        ignored_target = package / "target"
        source.mkdir(parents=True)
        ignored_target.mkdir()
        (package / "pyproject.toml").write_text(
            "[project]\nname='fixture'\n", encoding="utf-8"
        )
        for file_index in range(100):
            (source / f"module_{file_index:03d}.py").write_text(
                f"VALUE = {file_index}\n", encoding="utf-8"
            )
        for file_index in range(10):
            (ignored_target / f"ignored-{file_index:02d}.txt").write_text(
                "ignored\n", encoding="utf-8"
            )

    edge = root / "edge"
    for relative in (
        ".github/workflows/CI.yaml",
        ".buildkite/pipeline.yml",
        ".circleci/config.yaml",
        "Alpha/Cargo.toml",
        "Alpha/requirements-dev.txt",
        "alpha/solution.sln",
        "alpha/app.csproj",
        "alpha/.sln",
        "alpha/.csproj",
        "alpha/requirements-.txt",
        "alpha/requirements🔥.txt",
        "Zeta/level-1/level-2/pyproject.toml",
        "Zeta/level-1/level-2/level-3/Cargo.toml",
    ):
        path = edge / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    for ignored in (
        ".worktrees",
        ".task-worktrees",
        ".venvs",
        ".codex_tmp",
        ".codex-tmp",
        ".direnv",
        ".nox",
        "__pycache__",
    ):
        path = edge / ignored / "Cargo.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ignored\n", encoding="utf-8")
    symlink = edge / "alpha" / "linked-Cargo.toml"
    try:
        symlink.symlink_to(Path("..") / "Alpha" / "Cargo.toml")
    except OSError as error:
        raise RuntimeError(f"could not create scan-parity symlink: {error}") from error

    return {
        "heavy": (heavy, 3, 40),
        "edge_full": (edge, 2, 50),
        "edge_limited": (edge, 3, 4),
    }


def fixture_manifest(root: Path) -> str:
    entries: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append({"path": relative, "type": "symlink", "target": os.readlink(path)})
        elif path.is_file():
            entries.append({"path": relative, "type": "file", "sha256": sha256_file(path)})
        elif path.is_dir():
            entries.append({"path": relative, "type": "directory"})
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def write_plan(path: Path, count: int) -> None:
    commands = [
        {"id": f"true-{index:02d}", "argv": ["/usr/bin/true"], "evidence": "diagnostic"}
        for index in range(count)
    ]
    write_json(
        path,
        {
            "name": f"spawn-{count}",
            "cwd": ".",
            "default_timeout": 30,
            "stages": [{"name": "commands", "parallel": False, "commands": commands}],
        },
    )


TIME_VALUE = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")
RESOURCE_KEYS = {
    "maximum resident set size": "max_rss_bytes",
    "page faults": "page_faults",
    "voluntary context switches": "voluntary_context_switches",
    "involuntary context switches": "involuntary_context_switches",
    "instructions retired": "instructions_retired",
    "cycles elapsed": "cycles_elapsed",
}


def resource_profile(argv: list[str], *, repetitions: int = 10) -> dict[str, Any]:
    time_tool = Path("/usr/bin/time")
    if not time_tool.is_file():
        return {"available": False, "reason": "/usr/bin/time is unavailable"}
    samples: dict[str, list[int]] = {value: [] for value in RESOURCE_KEYS.values()}
    for _ in range(repetitions):
        completed = subprocess.run(
            [str(time_tool), "-lp", *argv],
            cwd=LAB_ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            return {"available": False, "reason": "resource command failed"}
        for line in completed.stderr.splitlines():
            match = TIME_VALUE.match(line)
            if match and match.group(2) in RESOURCE_KEYS:
                samples[RESOURCE_KEYS[match.group(2)]].append(int(match.group(1)))
    if any(len(values) != repetitions for values in samples.values()):
        return {"available": False, "reason": "unexpected /usr/bin/time output"}
    return {
        "available": True,
        "repetitions": repetitions,
        "cache_state": "page-cache-warm; no reboot or purge",
        "metrics": {
            name: {
                "p50": statistics.median(values),
                "p95": int(percentile(values, 0.95)),
            }
            for name, values in samples.items()
        },
    }


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_revision() -> str:
    return version(["git", "rev-parse", "HEAD"])


def target_host(rustc_verbose: str) -> str | None:
    for line in rustc_verbose.splitlines():
        if line.startswith("host: "):
            return line.removeprefix("host: ")
    return None


def cargo_test_receipt(target: Path) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        ["cargo", "test", "--locked", "--target-dir", str(target)],
        cwd=CRATE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    duration = time.perf_counter() - started
    output = completed.stdout + completed.stderr
    passed_counts = [
        int(value)
        for value in re.findall(rb"test result: ok\. (\d+) passed;", output)
    ]
    receipt = {
        "passed": completed.returncode == 0 and sum(passed_counts) >= 2,
        "exit_code": completed.returncode,
        "tests_passed": sum(passed_counts),
        "seconds": round(duration, 6),
        "output_sha256": digest_bytes(output),
    }
    if not receipt["passed"]:
        raise RuntimeError(output.decode("utf-8", errors="replace")[-4000:])
    return receipt


def main() -> int:
    benchmark_root = ARTIFACTS / "benchmarks"
    benchmark_root.mkdir(parents=True, exist_ok=True)
    rustc_verbose = version(["rustc", "-vV"])
    with tempfile.TemporaryDirectory(prefix="endurant-rust-retest-") as workspace_directory:
        workspace = Path(workspace_directory)
        one_plan = workspace / "one-command-plan.json"
        twelve_plan = workspace / "twelve-command-plan.json"
        write_plan(one_plan, 1)
        write_plan(twelve_plan, 12)
        logs = workspace / "runtime-logs"
        logs.mkdir()
        fixtures_root = workspace / "fixtures"
        fixtures_root.mkdir()
        fixtures = create_scan_fixtures(fixtures_root)
        fixture_manifests = {
            name: fixture_manifest(path)
            for name, path in {
                "heavy": fixtures["heavy"][0],
                "edge": fixtures["edge_full"][0],
            }.items()
        }

        rust_tests = cargo_test_receipt(workspace / "test-target")
        target = workspace / "release-target"
        build_started = time.perf_counter()
        build = subprocess.run(
            [
                "cargo",
                "build",
                "--release",
                "--locked",
                "--target-dir",
                str(target),
            ],
            cwd=CRATE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        build_seconds = time.perf_counter() - build_started
        if build.returncode != 0:
            raise RuntimeError(build.stderr.decode("utf-8", errors="replace"))
        binary = target / "release" / "endurant-runtime-spike"

        python_template = ["python3", "-S", str(PYTHON_RUNTIME), "template"]
        rust_template = [str(binary), "template"]
        _, python_template_output, _ = invoke(python_template)
        _, rust_template_output, _ = invoke(rust_template)

        scan_parity: list[dict[str, Any]] = []
        for name, (fixture, max_depth, max_items) in fixtures.items():
            python_scan = [
                "python3",
                "-S",
                str(PYTHON_SCAN),
                str(fixture),
                str(max_depth),
                str(max_items),
            ]
            rust_scan = [
                str(binary),
                "scan",
                str(fixture),
                str(max_depth),
                str(max_items),
            ]
            _, python_output, _ = invoke(python_scan)
            _, rust_output, _ = invoke(rust_scan)
            python_json = json.loads(python_output)
            rust_json = json.loads(rust_output)
            scan_parity.append(
                {
                    "case": name,
                    "max_depth": max_depth,
                    "max_items": max_items,
                    "equal": python_json == rust_json,
                    "python_sha256": digest_bytes(python_output),
                    "rust_sha256": digest_bytes(rust_output),
                }
            )

        heavy, heavy_depth, heavy_items = fixtures["heavy"]
        python_scan = [
            "python3",
            "-S",
            str(PYTHON_SCAN),
            str(heavy),
            str(heavy_depth),
            str(heavy_items),
        ]
        rust_scan = [
            str(binary),
            "scan",
            str(heavy),
            str(heavy_depth),
            str(heavy_items),
        ]
        python_run_one = [
            "python3",
            "-S",
            str(PYTHON_RUNTIME),
            "run",
            str(one_plan),
            "--repo",
            str(LAB_ROOT),
            "--format",
            "json",
            "--log-dir",
            str(logs),
        ]
        python_run_twelve = [
            "python3",
            "-S",
            str(PYTHON_RUNTIME),
            "run",
            str(twelve_plan),
            "--repo",
            str(LAB_ROOT),
            "--format",
            "json",
            "--log-dir",
            str(logs),
        ]

        full_cli_commands = {
            "probe": (
                [
                    "python3",
                    "-S",
                    str(PYTHON_RUNTIME),
                    "probe",
                    "--repo",
                    str(LAB_ROOT),
                    "--task",
                    "Rust parity probe",
                    "--format",
                    "json",
                ],
                [
                    str(binary),
                    "probe",
                    "--repo",
                    str(LAB_ROOT),
                    "--task",
                    "Rust parity probe",
                    "--format",
                    "json",
                ],
            ),
            "run": (
                python_run_one,
                [
                    str(binary),
                    "run",
                    str(one_plan),
                    "--repo",
                    str(LAB_ROOT),
                    "--format",
                    "json",
                    "--log-dir",
                    str(logs),
                ],
            ),
            "fingerprint": (
                [
                    "python3",
                    "-S",
                    str(PYTHON_RUNTIME),
                    "fingerprint",
                    "--repo",
                    str(LAB_ROOT),
                ],
                [str(binary), "fingerprint", "--repo", str(LAB_ROOT)],
            ),
        }
        full_cli: dict[str, dict[str, Any]] = {}
        for command, (python_argv, rust_argv) in full_cli_commands.items():
            _, python_result = run_raw(python_argv)
            _, rust_result = run_raw(rust_argv)
            full_cli[command] = {
                "candidate_exit_code": python_result.returncode,
                "rust_exit_code": rust_result.returncode,
                "rust_accepts_valid_candidate_invocation": rust_result.returncode == 0,
                "equivalent_success": (
                    python_result.returncode == rust_result.returncode == 0
                ),
            }

        results: dict[str, Any] = {
            "schema_version": 2,
            "scope": (
                "Current combined-candidate startup, exact template rendering, and a bounded "
                "non-Git scan-kernel matrix. Process-spawn measurements are deliberately optimistic "
                "ceilings because the Rust spike does not implement runner semantics."
            ),
            "source": {
                "candidate": "subjects/combined-candidate/endurant-harness/scripts/endurant.py",
                "candidate_git_revision": source_revision(),
                "candidate_sha256": sha256_file(PYTHON_RUNTIME),
                "rust_source": "subjects/rust-runtime/runtime-spike/src/main.rs",
                "rust_source_sha256": sha256_file(RUST_SOURCE),
                "input_sha256": {
                    relative: sha256_file(path) for relative, path in INPUT_PATHS.items()
                },
            },
            "environment": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "rustc": rustc_verbose.splitlines()[0],
                "cargo": version(["cargo", "--version"]),
                "rust_host": target_host(rustc_verbose),
            },
            "build": {
                "kind": "clean release build in a fresh target directory",
                "seconds": round(build_seconds, 6),
                "binary_bytes": binary.stat().st_size,
                "binary_sha256": sha256_file(binary),
                "third_party_crates": max(
                    0,
                    (CRATE / "Cargo.lock").read_text(encoding="utf-8").count("[[package]]")
                    - 1,
                ),
                "tested_targets": [target_host(rustc_verbose)],
                "cross_platform_parity_verified": False,
            },
            "rust_tests": rust_tests,
            "parity": {
                "template_exact": python_template_output == rust_template_output,
                "limited_non_git_scan_matrix": scan_parity,
                "limited_non_git_scan_exact": all(case["equal"] for case in scan_parity),
                "full_cli": full_cli,
                "full_cli_parity_implemented": all(
                    result["equivalent_success"] for result in full_cli.values()
                ),
            },
            "template": paired(python_template, rust_template, repetitions=50),
            "scan_kernel": paired(python_scan, rust_scan, repetitions=30),
            "one_command_optimistic_ceiling": paired(
                python_run_one, [str(binary), "spawn", "1"], repetitions=30
            ),
            "twelve_command_optimistic_ceiling": paired(
                python_run_twelve, [str(binary), "spawn", "12"], repetitions=20
            ),
            "template_resources": {
                "python": resource_profile(python_template),
                "rust": resource_profile(rust_template),
            },
            "generated_inputs": {
                "one_command_plan_sha256": sha256_file(one_plan),
                "twelve_command_plan_sha256": sha256_file(twelve_plan),
                "fixture_manifest_sha256": fixture_manifests,
            },
            "scan_cases": ["heavy", "edge_full", "edge_limited"],
            "uncertainty": (
                "Descriptive, alternating warm paired samples on one machine; no confidence "
                "interval or cold-cache claim. Small-sample maxima are labeled as maxima."
            ),
        }

        optimistic_seconds = round(
            results["scan_kernel"]["median_saved_seconds"]
            + results["one_command_optimistic_ceiling"]["median_saved_seconds"],
            9,
        )
        model_runs = read_json(MODEL_RUNS)
        direct_seconds = float(
            model_runs["ordinary_combined_evaluation"]["combined_candidate"]["median"][
                "duration_seconds"
            ]
        )
        performance_seconds = float(model_runs["dogfood_performance_smoke"]["duration_seconds"])
        results["task_impact"] = {
            "direct_lane_script_invocations": 0,
            "direct_lane_estimated_saved_seconds": 0.0,
            "direct_lane_reference_task_seconds": direct_seconds,
            "escalated_one_scan_one_run_optimistic_ceiling_seconds": optimistic_seconds,
            "escalated_reference_task_seconds": performance_seconds,
            "escalated_reference_task_optimistic_fraction": round(
                optimistic_seconds / performance_seconds, 6
            ),
            "rewrite_adoption_threshold_fraction": 0.15,
        }
        results["recommendation"] = {
            "adopt_full_rust_rewrite": False,
            "reason": (
                "The spike has exact template and limited non-Git scan parity but no probe, run, "
                "or fingerprint parity. Even its deliberately optimistic ceiling is immaterial to "
                "the measured end-to-end task."
            ),
        }
        results["passed"] = bool(
            results["parity"]["template_exact"]
            and results["parity"]["limited_non_git_scan_exact"]
            and results["rust_tests"]["passed"]
            and all(
                result["candidate_exit_code"] == 0
                and result["rust_exit_code"] == 2
                for result in full_cli.values()
            )
            and not results["parity"]["full_cli_parity_implemented"]
            and results["recommendation"]["adopt_full_rust_rewrite"] is False
        )

        write_json(benchmark_root / "rust-runtime.json", results)
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0 if results["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
