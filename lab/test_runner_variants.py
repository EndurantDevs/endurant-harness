#!/usr/bin/env python3
"""Deterministic correctness and overhead checks for isolated runner variants."""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from eval_lib import ARTIFACTS, LAB_ROOT, write_json


CURRENT = LAB_ROOT / "subjects" / "current" / "endurant-harness" / "scripts" / "endurant.py"
VARIANTS = {
    name: LAB_ROOT / "subjects" / name / "endurant-harness" / "scripts" / "endurant.py"
    for name in (
        "runner-assertions",
        "runner-fingerprint",
        "runner-deadline",
        "combined-candidate",
    )
}


def execute(
    script: Path,
    plan: Path,
    root: Path,
    log_dir: Path,
    *,
    expected_exit: int,
) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            str(script),
            "run",
            str(plan),
            "--repo",
            str(root),
            "--format",
            "json",
            "--log-dir",
            str(log_dir),
        ],
        cwd=root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    duration = time.perf_counter() - started
    if completed.returncode != expected_exit:
        raise AssertionError(
            f"expected exit {expected_exit}, got {completed.returncode}: "
            f"{completed.stderr[-2000:]} {completed.stdout[-2000:]}"
        )
    return duration, json.loads(completed.stdout)


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def overhead(script: Path, root: Path, plan: Path, logs: Path) -> dict[str, Any]:
    for _ in range(3):
        execute(CURRENT, plan, root, logs / "current", expected_exit=0)
        execute(script, plan, root, logs / "candidate", expected_exit=0)
    current_samples: list[float] = []
    candidate_samples: list[float] = []
    for index in range(25):
        order = ((CURRENT, current_samples, "current"), (script, candidate_samples, "candidate"))
        if index % 2:
            order = tuple(reversed(order))
        for selected, bucket, name in order:
            duration, _ = execute(selected, plan, root, logs / name, expected_exit=0)
            bucket.append(duration)
    current_p50 = percentile(current_samples, 0.50)
    candidate_p50 = percentile(candidate_samples, 0.50)
    return {
        "current_p50_seconds": round(current_p50, 9),
        "candidate_p50_seconds": round(candidate_p50, 9),
        "candidate_minus_current_seconds": round(candidate_p50 - current_p50, 9),
        "overhead_fraction": round((candidate_p50 - current_p50) / current_p50, 6),
    }


def simple_plan(path: Path) -> None:
    write_json(
        path,
        {
            "name": "overhead",
            "stages": [
                {
                    "name": "one",
                    "commands": [
                        {"id": "true", "argv": ["/usr/bin/true"], "evidence": "diagnostic"}
                    ],
                }
            ],
        },
    )


def test_assertions(root: Path, artifacts: Path) -> dict[str, Any]:
    script = VARIANTS["runner-assertions"]
    zero_plan = artifacts / "zero-tests.json"
    write_json(
        zero_plan,
        {
            "name": "reject-zero-tests",
            "stages": [
                {
                    "name": "tests",
                    "commands": [
                        {
                            "id": "tests",
                            "argv": [sys.executable, "-S", "-c", "print('Ran 0 tests')"],
                            "evidence": "behavior",
                            "must_not_match": r"Ran 0 tests",
                        }
                    ],
                }
            ],
        },
    )
    _, rejected = execute(script, zero_plan, root, artifacts / "zero-logs", expected_exit=1)
    positive_plan = artifacts / "positive-tests.json"
    write_json(
        positive_plan,
        {
            "name": "accept-real-tests",
            "stages": [
                {
                    "name": "tests",
                    "commands": [
                        {
                            "id": "tests",
                            "argv": [sys.executable, "-S", "-c", "print('Ran 3 tests')"],
                            "evidence": "behavior",
                            "must_match": r"Ran [1-9][0-9]* tests",
                        }
                    ],
                }
            ],
        },
    )
    _, accepted = execute(script, positive_plan, root, artifacts / "positive-logs", expected_exit=0)
    return {
        "zero_test_rejected": rejected["status"] == "failed",
        "real_test_accepted": accepted["status"] == "passed",
    }


def test_fingerprint(root: Path, artifacts: Path) -> dict[str, Any]:
    script = VARIANTS["runner-fingerprint"]
    artifacts.mkdir(parents=True, exist_ok=True)
    repository = artifacts / "fingerprint-repo"
    repository.mkdir()
    (repository / "source.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    subprocess.run(["git", "add", "source.txt"], cwd=repository, check=True)
    (repository / "source.txt").write_text("candidate\n", encoding="utf-8")
    fingerprint = subprocess.run(
        [sys.executable, "-S", str(script), "fingerprint", "--repo", str(repository)],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout.strip()
    plan = artifacts / "fingerprint.json"
    write_json(
        plan,
        {
            "name": "fingerprint",
            "expected_diff_fingerprint": fingerprint,
            "stages": [
                {
                    "name": "proof",
                    "commands": [
                        {"id": "true", "argv": ["/usr/bin/true"], "evidence": "diagnostic"}
                    ],
                }
            ],
        },
    )
    _, accepted = execute(script, plan, repository, artifacts / "fingerprint-pass", expected_exit=0)
    plain_plan = artifacts / "fingerprint-plain.json"
    write_json(
        plain_plan,
        {
            "name": "fingerprint-plain",
            "stages": [
                {
                    "name": "proof",
                    "commands": [
                        {"id": "true", "argv": ["/usr/bin/true"], "evidence": "diagnostic"}
                    ],
                }
            ],
        },
    )
    for _ in range(3):
        execute(script, plain_plan, repository, artifacts / "plain-logs", expected_exit=0)
        execute(script, plan, repository, artifacts / "enabled-logs", expected_exit=0)
    plain_samples: list[float] = []
    enabled_samples: list[float] = []
    for index in range(20):
        order = ((plain_plan, plain_samples, "plain"), (plan, enabled_samples, "enabled"))
        if index % 2:
            order = tuple(reversed(order))
        for selected_plan, bucket, name in order:
            duration, _ = execute(
                script,
                selected_plan,
                repository,
                artifacts / f"{name}-logs",
                expected_exit=0,
            )
            bucket.append(duration)
    plain_p50 = percentile(plain_samples, 0.50)
    enabled_p50 = percentile(enabled_samples, 0.50)
    (repository / "source.txt").write_text("mutated-after-proof-plan\n", encoding="utf-8")
    _, rejected = execute(script, plan, repository, artifacts / "fingerprint-fail", expected_exit=1)
    return {
        "unchanged_diff_accepted": accepted["status"] == "passed",
        "stale_diff_rejected": rejected.get("proof_error") == "diff fingerprint changed before proof",
        "enabled_median_overhead_seconds": round(enabled_p50 - plain_p50, 9),
        "enabled_overhead_fraction": round((enabled_p50 - plain_p50) / plain_p50, 6),
    }


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_deadline(root: Path, artifacts: Path) -> dict[str, Any]:
    script = VARIANTS["runner-deadline"]
    pid_path = artifacts / "child.pid"
    cleanup_path = artifacts / "cleanup.marker"
    worker = (
        "import pathlib,subprocess,time; "
        "p=subprocess.Popen(['/bin/sleep','30']); "
        f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid)); "
        "time.sleep(30)"
    )
    cleanup = f"from pathlib import Path; Path({str(cleanup_path)!r}).write_text('done')"
    plan = artifacts / "deadline.json"
    write_json(
        plan,
        {
            "name": "deadline",
            "proof_deadline_seconds": 1,
            "stages": [
                {
                    "name": "hang",
                    "commands": [
                        {
                            "id": "hang",
                            "argv": [sys.executable, "-S", "-c", worker],
                            "timeout": 30,
                            "evidence": "diagnostic",
                        }
                    ],
                },
                {
                    "name": "cleanup",
                    "run_if": "always",
                    "commands": [
                        {
                            "id": "cleanup",
                            "argv": [sys.executable, "-S", "-c", cleanup],
                            "timeout": 5,
                            "evidence": "cleanup",
                        }
                    ],
                },
            ],
        },
    )
    duration, result = execute(script, plan, root, artifacts / "deadline-logs", expected_exit=1)
    time.sleep(0.2)
    child_pid = int(pid_path.read_text(encoding="utf-8"))
    child_alive = process_alive(child_pid)
    if child_alive:
        os.kill(child_pid, signal.SIGKILL)
    return {
        "duration_seconds": round(duration, 6),
        "bounded_under_four_seconds": duration < 4,
        "cleanup_ran": cleanup_path.read_text(encoding="utf-8") == "done",
        "child_process_terminated": not child_alive,
        "status_failed": result["status"] == "failed",
    }


def main() -> int:
    output_root = (
        ARTIFACTS / "benchmarks" / "runner-variants" / f"run-{time.time_ns()}"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="runner-variant-root-") as root_value:
        root = Path(root_value)
        plan = output_root / "overhead-plan.json"
        simple_plan(plan)
        results = {
            "assertions": test_assertions(root, output_root / "assertions"),
            "fingerprint": test_fingerprint(root, output_root / "fingerprint"),
            "deadline": test_deadline(root, output_root / "deadline"),
            "overhead": {
                name: overhead(script, root, plan, output_root / f"overhead-{name}")
                for name, script in VARIANTS.items()
            },
        }
    checks = [
        results["assertions"]["zero_test_rejected"],
        results["assertions"]["real_test_accepted"],
        results["fingerprint"]["unchanged_diff_accepted"],
        results["fingerprint"]["stale_diff_rejected"],
        results["deadline"]["bounded_under_four_seconds"],
        results["deadline"]["cleanup_ran"],
        results["deadline"]["child_process_terminated"],
        results["deadline"]["status_failed"],
    ]
    results["passed"] = all(checks)
    write_json(ARTIFACTS / "benchmarks" / "runner-variants.json", results)
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if results["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
