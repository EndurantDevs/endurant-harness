#!/usr/bin/env python3
"""Canonical terminal and local-CI oracle for synthetic recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def verification_sha256() -> str:
    digest = hashlib.sha256()
    for relative in ("external/actions.jsonl", "external/state.json"):
        path = ROOT / relative
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def append_event(gate: str, passed: bool, started: float) -> None:
    value = os.environ.get("EVAL_EVENT_LOG")
    if not value:
        return
    event = {
        "actor": os.environ.get("EVAL_ACTOR", "manual"),
        "duration_seconds": round(time.perf_counter() - started, 6),
        "gate": gate,
        "passed": passed,
        "run_id": os.environ.get("EVAL_RUN_ID", "manual"),
        "source_sha256": hashlib.sha256((ROOT / "scripts" / "mock_control.py").read_bytes()).hexdigest(),
        "timestamp_ns": time.time_ns(),
        "verification_sha256": verification_sha256(),
    }
    path = Path(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def terminal() -> bool:
    completed = subprocess.run(
        [sys.executable, "scripts/mock_control.py", "verify"],
        cwd=ROOT,
        check=False,
    )
    return completed.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=("terminal", "focused", "ci-preflight"))
    args = parser.parse_args()
    started = time.perf_counter()
    passed = False
    try:
        passed = terminal()
        if args.gate == "ci-preflight" and passed:
            compiled = subprocess.run(
                [sys.executable, "-m", "compileall", "-q", "scripts"],
                cwd=ROOT,
                check=False,
            )
            passed = compiled.returncode == 0
            if passed:
                print("LOCAL_CI_PREFLIGHT_PASS")
    finally:
        append_event(args.gate, passed, started)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
