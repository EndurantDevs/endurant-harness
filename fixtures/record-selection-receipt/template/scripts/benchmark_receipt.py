#!/usr/bin/env python3
"""Repository-owned, runner-observable benchmark receipt for the fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / ".agents" / "endurant-harness-benchmarks.json"


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_manifest(paths: list[str]) -> dict[str, str]:
    return {
        relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in paths
    }


def receipt_path() -> Path:
    event_log = os.environ.get("EVAL_EVENT_LOG")
    if not event_log:
        raise ValueError("EVAL_EVENT_LOG is required for an external receipt path")
    return Path(event_log).with_name("benchmark-baseline-receipt.json")


def load_profile() -> tuple[dict[str, Any], str]:
    envelope = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    if envelope.get("schema_version") != 1:
        raise ValueError("unsupported benchmark profile")
    profile = envelope["benchmarks"]["record-selection"]
    return profile, canonical_sha256(envelope)


def run_benchmark() -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "scripts/verify.py", "synthetic"],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise ValueError("benchmark did not emit its JSON metric line") from error


def build_receipt(profile: dict[str, Any], profile_hash: str, phase: str) -> dict[str, Any]:
    event = run_benchmark()
    metric_keys = profile["metric_keys"]
    if any(key not in event for key in profile["correctness_keys"] + metric_keys):
        raise ValueError("benchmark output is missing a declared field")
    body = {
        "schema_version": 1,
        "benchmark_id": "record-selection",
        "phase": phase,
        "profile_sha256": profile_hash,
        "source": file_manifest(profile["source_files"]),
        "workload": {
            "argv": profile["argv"],
            "cwd": profile["cwd"],
            "env": profile["env"],
            "files": file_manifest(profile["workload_files"]),
        },
        "correctness": {
            key: event[key] for key in profile["correctness_keys"]
        },
        "metrics": {key: event[key] for key in metric_keys},
        "primary_metric": profile["primary_metric"],
        "direction": profile["direction"],
        "minimum_improvement_fraction": profile[
            "minimum_improvement_fraction"
        ],
    }
    return {"body": body, "receipt_sha256": canonical_sha256(body)}


def validate(receipt: dict[str, Any]) -> dict[str, Any]:
    body = receipt.get("body")
    if not isinstance(body, dict) or receipt.get("receipt_sha256") != canonical_sha256(body):
        raise ValueError("benchmark receipt envelope is invalid")
    return body


def compare(before_receipt: dict[str, Any], after_receipt: dict[str, Any]) -> dict[str, Any]:
    before = validate(before_receipt)
    after = validate(after_receipt)
    if before["phase"] != "baseline" or after["phase"] != "final":
        raise ValueError("benchmark receipt phases are invalid")
    for key in (
        "schema_version",
        "benchmark_id",
        "profile_sha256",
        "workload",
        "correctness",
        "primary_metric",
        "direction",
        "minimum_improvement_fraction",
    ):
        if before[key] != after[key]:
            raise ValueError(f"benchmark receipt changed {key}")
    if before["source"] == after["source"]:
        raise ValueError("benchmark source did not change")
    metric = before["primary_metric"]
    baseline = float(before["metrics"][metric])
    final = float(after["metrics"][metric])
    if baseline <= 0 or final <= 0:
        raise ValueError("benchmark metric must be positive")
    improvement = (baseline - final) / baseline
    threshold = float(before["minimum_improvement_fraction"])
    return {
        "accepted": improvement >= threshold,
        "baseline": baseline,
        "final": final,
        "improvement_fraction": round(improvement, 9),
        "threshold_fraction": threshold,
        "workload_identical": before["workload"] == after["workload"],
        "correctness_identical": before["correctness"] == after["correctness"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("baseline", "final"))
    args = parser.parse_args()
    profile, profile_hash = load_profile()
    current = build_receipt(profile, profile_hash, args.phase)
    path = receipt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if args.phase == "baseline":
        path.write_text(json.dumps(current, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"phase": "baseline", "receipt_sha256": current["receipt_sha256"]}, sort_keys=True))
        return 0
    if not path.is_file():
        raise ValueError("baseline receipt is missing")
    baseline = json.loads(path.read_text(encoding="utf-8"))
    result = compare(baseline, current)
    print(json.dumps(result, sort_keys=True))
    if result["accepted"]:
        print("BENCHMARK_COMPARISON_PASS")
        return 0
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as error:
        print(f"benchmark receipt error: {error}", file=sys.stderr)
        raise SystemExit(2)
