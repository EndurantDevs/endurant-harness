#!/usr/bin/env python3
"""Measure controlled efficiency proxies for this skill.

The benchmark compares always-loaded SKILL.md size with the recorded v3
baseline and measures a synthetic workload with four independent commands in
each of three ordered stages. It does not claim universal coding throughput.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def skill_metrics(root: Path) -> dict[str, Any]:
    path = root / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
        "words": len(re.findall(r"\S+", text)),
    }


def load_baseline(candidate: Path, baseline_skill: str | None) -> dict[str, Any]:
    if baseline_skill:
        return skill_metrics(Path(baseline_skill).expanduser().resolve())
    payload = json.loads(
        (candidate / "evals" / "efficiency-baseline.json").read_text(encoding="utf-8")
    )
    return dict(payload["baseline"])


def command_argv(delay: float, token: str) -> list[str]:
    sleep_command = shutil.which("sleep")
    if sleep_command:
        return [sleep_command, f"{delay:.3f}"]
    return [
        sys.executable,
        "-S",
        "-c",
        (
            "import time; "
            f"time.sleep({delay!r}); "
            f"print({token!r})"
        ),
    ]


def make_plan(root: Path, delay: float) -> tuple[dict[str, Any], list[list[str]]]:
    all_commands: list[list[str]] = []
    stages: list[dict[str, Any]] = []
    for stage_index in range(3):
        commands = []
        for command_index in range(4):
            token = f"s{stage_index + 1}c{command_index + 1}"
            argv = command_argv(delay, token)
            all_commands.append(argv)
            commands.append(
                {
                    "id": token,
                    "argv": argv,
                    "timeout": max(5, int(delay * 10)),
                }
            )
        stages.append(
            {
                "name": f"stage-{stage_index + 1}",
                "parallel": True,
                "commands": commands,
            }
        )
    return {"name": "controlled-efficiency", "cwd": str(root), "stages": stages}, all_commands


def run_sequential(commands: list[list[str]], cwd: Path) -> float:
    started = time.perf_counter()
    for argv in commands:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise RuntimeError(f"sequential workload failed: {proc.stdout}")
    return time.perf_counter() - started


def run_batched(candidate: Path, plan_path: Path, cwd: Path, log_dir: Path) -> tuple[float, dict[str, Any]]:
    argv = [
        sys.executable,
        "-S",
        str(candidate / "scripts" / "endurant.py"),
        "run",
        str(plan_path),
        "--repo",
        str(cwd),
        "--format",
        "json",
        "--max-workers",
        "4",
        "--log-dir",
        str(log_dir),
    ]
    started = time.perf_counter()
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        raise RuntimeError(f"batched workload failed: {proc.stderr}\n{proc.stdout}")
    payload = json.loads(proc.stdout)
    if payload.get("status") != "passed":
        raise RuntimeError(f"batched workload reported failure: {payload}")
    return elapsed, payload


def benchmark(candidate: Path, baseline_skill: str | None, repeats: int, delay: float) -> dict[str, Any]:
    candidate_metrics = skill_metrics(candidate)
    baseline_metrics = load_baseline(candidate, baseline_skill)
    footprint_ratio = baseline_metrics["words"] / candidate_metrics["words"]

    sequential: list[float] = []
    batched: list[float] = []
    command_count = 12
    runner_duration: list[float] = []

    with tempfile.TemporaryDirectory(prefix="endurant-efficiency-") as temp_value:
        temp = Path(temp_value)
        plan, commands = make_plan(temp, delay)
        plan_path = temp / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        for index in range(repeats):
            sequential.append(run_sequential(commands, temp))
            elapsed, payload = run_batched(
                candidate,
                plan_path,
                temp,
                temp / f"logs-{index}",
            )
            batched.append(elapsed)
            runner_duration.append(float(payload["duration_seconds"]))

    sequential_median = statistics.median(sequential)
    batched_median = statistics.median(batched)
    speedup = sequential_median / batched_median
    targets = json.loads(
        (candidate / "evals" / "efficiency-baseline.json").read_text(encoding="utf-8")
    )["targets"]

    return {
        "scope": (
            "Controlled proxies only: always-loaded instruction footprint and a synthetic "
            "three-stage workload with four genuinely independent commands per stage."
        ),
        "candidate": candidate_metrics,
        "baseline": baseline_metrics,
        "instruction_reduction_ratio": round(footprint_ratio, 3),
        "workload": {
            "stages": 3,
            "commands_per_stage": 4,
            "commands_total": command_count,
            "delay_per_command_seconds": delay,
            "repeats": repeats,
            "sequential_seconds": [round(value, 3) for value in sequential],
            "batched_seconds": [round(value, 3) for value in batched],
            "runner_internal_seconds": [round(value, 3) for value in runner_duration],
            "sequential_median_seconds": round(sequential_median, 3),
            "batched_median_seconds": round(batched_median, 3),
            "wall_clock_speedup": round(speedup, 3),
            "parent_invocation_compression": command_count,
        },
        "targets": targets,
        "target_3x_met": (
            footprint_ratio >= float(targets["minimum_instruction_reduction_ratio"])
            and speedup >= float(targets["minimum_synthetic_parallel_speedup"])
            and candidate_metrics["words"] <= int(targets["maximum_candidate_words"])
        ),
        "limitations": [
            "The synthetic workload measures safe parallel command execution, not reasoning quality.",
            "Real repositories contain dependencies, cache contention, setup cost, and tasks that cannot be parallelized.",
            "End-to-end impact requires repeated A/B Codex tasks using success, regressions, turns, tokens, corrections, and elapsed time.",
        ],
    }


def render_text(result: dict[str, Any]) -> str:
    workload = result["workload"]
    status = "PASS" if result["target_3x_met"] else "FAIL"
    return "\n".join(
        [
            f"{status}: controlled 3x efficiency target",
            (
                f"instruction_words baseline={result['baseline']['words']} "
                f"candidate={result['candidate']['words']} "
                f"reduction={result['instruction_reduction_ratio']:.3f}x"
            ),
            (
                f"synthetic_wall_clock sequential={workload['sequential_median_seconds']:.3f}s "
                f"batched={workload['batched_median_seconds']:.3f}s "
                f"speedup={workload['wall_clock_speedup']:.3f}x"
            ),
            (
                f"parent_invocations isolated={workload['commands_total']} "
                f"runner=1 compression={workload['parent_invocation_compression']}x"
            ),
            f"scope={result['scope']}",
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "skill_root",
        nargs="?",
        default=str(Path(__file__).resolve().parents[1]),
        help="candidate skill root",
    )
    parser.add_argument("--baseline-skill", help="optional prior skill root; otherwise recorded v3 metrics")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--validate-only", action="store_true", help="validate benchmark wiring without running the timed workload")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 5:
        parser.error("--repeats must be 1..5")
    if not 0.05 <= args.delay <= 2.0:
        parser.error("--delay must be 0.05..2.0 seconds")

    try:
        candidate = Path(args.skill_root).expanduser().resolve()
        if args.validate_only:
            engine = candidate / "scripts" / "endurant.py"
            if not engine.is_file():
                raise FileNotFoundError(f"missing runner: {engine}")
            probe = subprocess.run(
                [sys.executable, "-S", str(engine), "template"],
                cwd=str(candidate),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                encoding="utf-8",
                errors="replace",
            )
            payload = json.loads(probe.stdout) if probe.returncode == 0 else None
            if not isinstance(payload, dict) or not payload.get("stages"):
                raise RuntimeError(f"runner template validation failed: {probe.stderr or probe.stdout}")
            result = {"ok": True, "candidate": str(candidate), "runner": str(engine), "template_stages": len(payload["stages"])}
            if args.format == "json":
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(f"PASS: benchmark wiring runner={engine} template_stages={len(payload['stages'])}")
            return 0
        result = benchmark(
            candidate,
            args.baseline_skill,
            args.repeats,
            args.delay,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2

    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_text(result), end="")
    return 0 if result["target_3x_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
