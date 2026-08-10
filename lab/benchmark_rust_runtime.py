#!/usr/bin/env python3
"""Measure a Rust runtime spike without mixing in probe-algorithm changes."""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from eval_lib import ARTIFACTS, LAB_ROOT, write_json


CURRENT = LAB_ROOT / "subjects" / "current" / "endurant-harness" / "scripts" / "endurant.py"
CRATE = LAB_ROOT / "subjects" / "rust-runtime" / "runtime-spike"
BINARY = CRATE / "target" / "release" / "endurant-runtime-spike"
PYTHON_SCAN = LAB_ROOT / "lab" / "python_scan_kernel.py"


def invoke(argv: list[str], cwd: Path = LAB_ROOT) -> tuple[float, bytes, bytes]:
    started = time.perf_counter()
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    duration = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {argv!r}\n"
            + completed.stderr.decode("utf-8", errors="replace")[-2000:]
        )
    return duration, completed.stdout, completed.stderr


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def summarize(samples: list[float]) -> dict[str, Any]:
    return {
        "samples_seconds": [round(value, 9) for value in samples],
        "p50_seconds": round(percentile(samples, 0.50), 9),
        "p95_seconds": round(percentile(samples, 0.95), 9),
    }


def paired(
    python_argv: list[str], rust_argv: list[str], *, repetitions: int, warmups: int = 3
) -> dict[str, Any]:
    for _ in range(warmups):
        invoke(python_argv)
        invoke(rust_argv)
    python_samples: list[float] = []
    rust_samples: list[float] = []
    for index in range(repetitions):
        order = (("python", python_argv), ("rust", rust_argv))
        if index % 2:
            order = tuple(reversed(order))
        for name, argv in order:
            duration, _, _ = invoke(argv)
            (python_samples if name == "python" else rust_samples).append(duration)
    python_summary = summarize(python_samples)
    rust_summary = summarize(rust_samples)
    python_p50 = float(python_summary["p50_seconds"])
    rust_p50 = float(rust_summary["p50_seconds"])
    return {
        "python": python_summary,
        "rust": rust_summary,
        "median_saved_seconds": round(python_p50 - rust_p50, 9),
        "median_speedup_ratio": round(python_p50 / rust_p50, 3) if rust_p50 else None,
    }


def create_scan_fixture() -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="rust-scan-fixture-", dir=ARTIFACTS))
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    for package_index in range(30):
        package = root / f"package-{package_index:02d}"
        source = package / "src"
        ignored_target = package / "target"
        source.mkdir(parents=True)
        ignored_target.mkdir()
        (package / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
        for file_index in range(100):
            (source / f"module_{file_index:03d}.py").write_text(
                f"VALUE = {file_index}\n", encoding="utf-8"
            )
        for file_index in range(10):
            (ignored_target / f"ignored-{file_index:02d}.txt").write_text(
                "ignored\n", encoding="utf-8"
            )
    return root


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


def main() -> int:
    build_started = time.perf_counter()
    build = subprocess.run(
        ["cargo", "build", "--release", "--locked"],
        cwd=CRATE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    build_seconds = time.perf_counter() - build_started
    if build.returncode != 0:
        raise RuntimeError(build.stderr.decode("utf-8", errors="replace"))

    fixture = create_scan_fixture()
    benchmark_root = ARTIFACTS / "benchmarks"
    benchmark_root.mkdir(parents=True, exist_ok=True)
    one_plan = benchmark_root / "rust-one-command-plan.json"
    twelve_plan = benchmark_root / "rust-twelve-command-plan.json"
    write_plan(one_plan, 1)
    write_plan(twelve_plan, 12)
    logs = benchmark_root / "runtime-logs"
    logs.mkdir(exist_ok=True)

    python_template = ["python3", "-S", str(CURRENT), "template"]
    rust_template = [str(BINARY), "template"]
    _, python_template_output, _ = invoke(python_template)
    _, rust_template_output, _ = invoke(rust_template)

    python_scan = ["python3", "-S", str(PYTHON_SCAN), str(fixture), "3", "40"]
    rust_scan = [str(BINARY), "scan", str(fixture), "3", "40"]
    _, python_scan_output, _ = invoke(python_scan)
    _, rust_scan_output, _ = invoke(rust_scan)

    python_run_one = [
        "python3", "-S", str(CURRENT), "run", str(one_plan), "--repo", str(LAB_ROOT),
        "--format", "json", "--log-dir", str(logs),
    ]
    python_run_twelve = [
        "python3", "-S", str(CURRENT), "run", str(twelve_plan), "--repo", str(LAB_ROOT),
        "--format", "json", "--log-dir", str(logs),
    ]

    results = {
        "scope": (
            "Startup, exact template rendering, unchanged repository-scan kernel, and a deliberately "
            "optimistic process-spawn upper bound. This is not a full runner replacement or parity claim."
        ),
        "build": {
            "seconds": round(build_seconds, 6),
            "binary_bytes": BINARY.stat().st_size,
            "rustc": invoke(["rustc", "--version"])[1].decode().strip(),
        },
        "parity": {
            "template_exact": python_template_output == rust_template_output,
            "scan_json_equal": json.loads(python_scan_output) == json.loads(rust_scan_output),
        },
        "template": paired(python_template, rust_template, repetitions=40),
        "scan_kernel": paired(python_scan, rust_scan, repetitions=20),
        "one_command_upper_bound": paired(
            python_run_one, [str(BINARY), "spawn", "1"], repetitions=30
        ),
        "twelve_command_upper_bound": paired(
            python_run_twelve, [str(BINARY), "spawn", "12"], repetitions=20
        ),
        "fixture": str(fixture),
    }
    results["estimated_normal_task_upper_bound_seconds"] = round(
        results["scan_kernel"]["median_saved_seconds"]
        + results["one_command_upper_bound"]["median_saved_seconds"],
        9,
    )
    write_json(benchmark_root / "rust-runtime.json", results)
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if all(results["parity"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
