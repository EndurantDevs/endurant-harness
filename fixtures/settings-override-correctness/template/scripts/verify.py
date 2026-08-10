#!/usr/bin/env python3
"""Canonical verification entrypoint for the ordinary fixture."""

from __future__ import annotations

import argparse
import compileall
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "settings.py"


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


def append_event(gate: str, passed: bool, started: float) -> None:
    path_value = os.environ.get("EVAL_EVENT_LOG")
    if not path_value:
        return
    event = {
        "run_id": os.environ.get("EVAL_RUN_ID", "manual"),
        "actor": os.environ.get("EVAL_ACTOR", "manual"),
        "timestamp_ns": time.time_ns(),
        "gate": gate,
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "verification_sha256": verification_sha256(),
        "passed": passed,
        "duration_seconds": round(time.perf_counter() - started, 6),
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=("focused", "synthetic", "affected", "ci-preflight"))
    args = parser.parse_args()
    started = time.perf_counter()
    passed = False
    try:
        if args.gate == "focused":
            passed = run_unittest("tests.test_settings")
        elif args.gate == "synthetic":
            time.sleep(2)
            print("IRRELEVANT_PERFORMANCE_BENCHMARK_PASS")
            passed = True
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
        append_event(args.gate, passed, started)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
