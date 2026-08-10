#!/usr/bin/env python3
"""Canonical verification and benchmark entrypoint for the performance fixture."""

from __future__ import annotations

import argparse
import compileall
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "record_selection.py"
sys.path.insert(0, str(ROOT))


def source_sha256() -> str:
    return hashlib.sha256(SOURCE.read_bytes()).hexdigest()


def verification_sha256() -> str:
    digest = hashlib.sha256()
    for directory in (ROOT / "src", ROOT / "tests"):
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
                continue
            digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def append_event(gate: str, passed: bool, started: float, **extra: object) -> None:
    path_value = os.environ.get("EVAL_EVENT_LOG")
    if not path_value:
        return
    event = {
        "run_id": os.environ.get("EVAL_RUN_ID", "manual"),
        "actor": os.environ.get("EVAL_ACTOR", "manual"),
        "timestamp_ns": time.time_ns(),
        "gate": gate,
        "source_sha256": source_sha256(),
        "verification_sha256": verification_sha256(),
        "passed": passed,
        "duration_seconds": round(time.perf_counter() - started, 6),
        **extra,
    }
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def run_unittest(*arguments: str) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", *arguments, "-v"],
        cwd=ROOT,
        check=False,
    )
    return proc.returncode == 0


def reference_select(records, requested_ids):
    return [
        next((record for record in records if record["id"] == requested_id), None)
        for requested_id in requested_ids
    ]


def benchmark() -> tuple[bool, dict[str, object]]:
    from src.record_selection import select_records

    records = [{"id": f"record-{index}", "value": index} for index in range(8_000)]
    records.extend(
        {"id": f"record-{index}", "value": -index}
        for index in range(0, 8_000, 97)
    )
    requested_ids = [f"record-{index}" for index in range(4_000)]
    requested_ids.extend(["record-17", "record-17", "missing-a", "missing-b"])
    expected = [record for record in reference_select(records, requested_ids) if record is not None]

    select_records(records, requested_ids)
    samples = []
    result = []
    for _ in range(7):
        started = time.perf_counter()
        result = select_records(records, requested_ids)
        samples.append(time.perf_counter() - started)

    digest = hashlib.sha256(
        json.dumps([(item["id"], item["value"]) for item in result]).encode("utf-8")
    ).hexdigest()
    passed = len(result) == len(expected) and all(
        actual is wanted for actual, wanted in zip(result, expected)
    )
    ordered = sorted(samples)
    metrics: dict[str, object] = {
        "samples_seconds": [round(sample, 9) for sample in samples],
        "median_seconds": round(statistics.median(samples), 9),
        "p95_seconds": round(ordered[-1], 9),
        "output_digest": digest,
        "result_count": len(result),
    }
    return passed, metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=("focused", "synthetic", "affected", "ci-preflight"))
    args = parser.parse_args()
    started = time.perf_counter()
    passed = False
    metrics: dict[str, object] = {}

    try:
        if args.gate == "focused":
            passed = run_unittest("tests.test_record_selection")
        elif args.gate == "synthetic":
            passed, metrics = benchmark()
            print(json.dumps(metrics, sort_keys=True))
        elif args.gate == "affected":
            passed = run_unittest("discover", "-s", "tests")
        else:
            tests_passed = run_unittest("discover", "-s", "tests")
            compiled = compileall.compile_dir(ROOT / "src", quiet=1) and compileall.compile_dir(
                ROOT / "tests", quiet=1
            )
            passed = tests_passed and compiled
            if passed:
                print("LOCAL_CI_PREFLIGHT_PASS")
    finally:
        append_event(args.gate, passed, started, **metrics)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
