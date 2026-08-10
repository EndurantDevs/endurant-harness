#!/usr/bin/env python3
"""Paired stdlib runtime benchmark for the combined and v5 harnesses."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
COMBINED = (
    ROOT
    / "subjects"
    / "combined-candidate"
    / "endurant-harness"
    / "scripts"
    / "endurant.py"
)
V5 = ROOT / "subjects" / "vnext" / "endurant-harness" / "scripts" / "endurant.py"
BENCHMARK = Path(__file__).resolve()
PROBE_TASK = "Fix select_records duplicate stability"
DEFAULT_PAIRS = 31
DEFAULT_WARMUPS = 3
COMMAND_TIMEOUT_SECONDS = 90
MEDIAN_REGRESSION_LIMIT_SECONDS = 0.025
RUNTIMES = {"combined": COMBINED, "v5": V5}
INTENTIONAL_PROBE_DIFFERENCES = [
    "candidate_paths ordering and bounded selection",
    "task_symbols",
    "contract profile_sha256 values",
    "candidate-derived warning, truncated, and incomplete values",
]


Observation = dict[str, Any]
PairValidator = Callable[[Observation, Observation], dict[str, Any]]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def _rounded(value: float) -> float:
    return round(value, 9)


def _summary(samples: list[float]) -> dict[str, Any]:
    return {
        "samples_seconds": [_rounded(value) for value in samples],
        "p50_seconds": _rounded(statistics.median(samples)),
        "p95_seconds": _rounded(_percentile(samples, 0.95)),
        "min_seconds": _rounded(min(samples)),
        "max_seconds": _rounded(max(samples)),
    }


def _fixed_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "TZ": "UTC",
        }
    )
    return environment


def _run(argv: list[str], cwd: Path, environment: dict[str, str]) -> Observation:
    started = time.perf_counter_ns()
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        return {
            "seconds": (time.perf_counter_ns() - started) / 1_000_000_000,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout if isinstance(error.stdout, bytes) else b""
        stderr = error.stderr if isinstance(error.stderr, bytes) else b""
        return {
            "seconds": (time.perf_counter_ns() - started) / 1_000_000_000,
            "returncode": None,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": True,
        }


def _json_stdout(observation: Observation) -> tuple[Any | None, str | None]:
    try:
        return json.loads(observation["stdout"]), None
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, f"{type(error).__name__}: {error}"


def _observation_receipt(observation: Observation) -> dict[str, Any]:
    return {
        "seconds": _rounded(float(observation["seconds"])),
        "returncode": observation["returncode"],
        "timed_out": bool(observation["timed_out"]),
        "stdout_bytes": len(observation["stdout"]),
        "stdout_sha256": _sha256_bytes(observation["stdout"]),
        "stderr_bytes": len(observation["stderr"]),
        "stderr_sha256": _sha256_bytes(observation["stderr"]),
    }


def _template_pair(combined: Observation, v5: Observation) -> dict[str, Any]:
    combined_json, combined_error = _json_stdout(combined)
    v5_json, v5_error = _json_stdout(v5)
    exact = combined["stdout"] == v5["stdout"] and combined["stderr"] == v5["stderr"]
    return {
        "semantic_equal": exact and combined_error is None and v5_error is None,
        "stdout_exact": combined["stdout"] == v5["stdout"],
        "stderr_exact": combined["stderr"] == v5["stderr"],
        "combined_json_sha256": (
            _canonical_sha256(combined_json) if combined_error is None else None
        ),
        "v5_json_sha256": _canonical_sha256(v5_json) if v5_error is None else None,
        "combined_parse_error": combined_error,
        "v5_parse_error": v5_error,
    }


def _contract_without_hashes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            normalized.append({"invalid": type(item).__name__})
            continue
        normalized.append(
            {key: item[key] for key in sorted(item) if key != "profile_sha256"}
        )
    return normalized


def _stable_probe_warning(value: Any) -> bool:
    return value not in {
        "candidate search output truncated",
        "candidate path list truncated",
    }


def _normalized_probe(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    normalized = dict(payload)
    normalized.pop("candidate_paths", None)
    normalized.pop("task_symbols", None)
    normalized.pop("provenance", None)
    normalized.pop("truncated", None)
    normalized["contracts"] = _contract_without_hashes(payload.get("contracts", []))
    warnings = [
        item
        for item in payload.get("warnings", [])
        if _stable_probe_warning(item)
    ]
    normalized["warnings"] = warnings
    normalized["incomplete"] = bool(warnings)
    return normalized


def _probe_details(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"payload_type": type(payload).__name__}
    candidates = payload.get("candidate_paths", [])
    symbols = payload.get("task_symbols", [])
    contracts = payload.get("contracts", [])
    profile_hashes = [
        item.get("profile_sha256")
        for item in contracts
        if isinstance(item, dict) and isinstance(item.get("profile_sha256"), str)
    ] if isinstance(contracts, list) else []
    return {
        "candidate_count": len(candidates) if isinstance(candidates, list) else None,
        "candidate_paths_sha256": (
            _canonical_sha256(candidates) if isinstance(candidates, list) else None
        ),
        "task_symbols": symbols if isinstance(symbols, list) else None,
        "profile_sha256_values": profile_hashes,
        "raw_truncated": payload.get("truncated"),
        "raw_incomplete": payload.get("incomplete"),
        "raw_warnings": payload.get("warnings"),
    }


def _probe_pair(combined: Observation, v5: Observation) -> dict[str, Any]:
    combined_json, combined_error = _json_stdout(combined)
    v5_json, v5_error = _json_stdout(v5)
    combined_normalized = _normalized_probe(combined_json)
    v5_normalized = _normalized_probe(v5_json)
    equal = (
        combined_error is None
        and v5_error is None
        and combined_normalized is not None
        and combined_normalized == v5_normalized
    )
    return {
        "semantic_equal": equal,
        "combined_normalized_sha256": (
            _canonical_sha256(combined_normalized)
            if combined_normalized is not None
            else None
        ),
        "v5_normalized_sha256": (
            _canonical_sha256(v5_normalized) if v5_normalized is not None else None
        ),
        "combined_parse_error": combined_error,
        "v5_parse_error": v5_error,
        "combined_intentional_fields": _probe_details(combined_json),
        "v5_intentional_fields": _probe_details(v5_json),
    }


def _normalized_runner(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    stages: list[dict[str, Any]] = []
    raw_stages = payload.get("stages")
    if not isinstance(raw_stages, list):
        return None
    for stage in raw_stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("commands"), list):
            return None
        commands: list[dict[str, Any]] = []
        for command in stage["commands"]:
            if not isinstance(command, dict):
                return None
            commands.append(
                {
                    key: command.get(key)
                    for key in (
                        "command_id",
                        "stage",
                        "evidence",
                        "status",
                        "exit_code",
                        "expected_exit_codes",
                        "cwd",
                        "display_command",
                        "tail",
                        "error",
                    )
                }
            )
        stages.append(
            {
                "name": stage.get("name"),
                "parallel": stage.get("parallel"),
                "run_if": stage.get("run_if"),
                "status": stage.get("status"),
                "commands": commands,
            }
        )
    return {
        "name": payload.get("name"),
        "require_behavior_evidence": payload.get("require_behavior_evidence"),
        "status": payload.get("status"),
        "interrupted": payload.get("interrupted", False),
        "evidence_summary": payload.get("evidence_summary"),
        "proof_errors": payload.get("proof_errors", []),
        "stages": stages,
    }


def _runner_pair(combined: Observation, v5: Observation) -> dict[str, Any]:
    combined_json, combined_error = _json_stdout(combined)
    v5_json, v5_error = _json_stdout(v5)
    combined_normalized = _normalized_runner(combined_json)
    v5_normalized = _normalized_runner(v5_json)
    status_equal = (
        isinstance(combined_json, dict)
        and isinstance(v5_json, dict)
        and combined_json.get("status") == v5_json.get("status")
    )
    equal = (
        combined_error is None
        and v5_error is None
        and combined_normalized is not None
        and combined_normalized == v5_normalized
    )
    return {
        "semantic_equal": equal,
        "status_equal": status_equal,
        "combined_status": (
            combined_json.get("status") if isinstance(combined_json, dict) else None
        ),
        "v5_status": v5_json.get("status") if isinstance(v5_json, dict) else None,
        "combined_normalized_sha256": (
            _canonical_sha256(combined_normalized)
            if combined_normalized is not None
            else None
        ),
        "v5_normalized_sha256": (
            _canonical_sha256(v5_normalized) if v5_normalized is not None else None
        ),
        "combined_parse_error": combined_error,
        "v5_parse_error": v5_error,
    }


def _failure_receipt(
    surface: str,
    runtime: str,
    phase: str,
    index: int,
    observation: Observation,
) -> dict[str, Any] | None:
    if observation["returncode"] == 0 and not observation["timed_out"]:
        return None
    tail = observation["stderr"].decode("utf-8", errors="replace")[-500:]
    tail = tail.replace(str(ROOT), "<repo>")
    return {
        "surface": surface,
        "runtime": runtime,
        "phase": phase,
        "index": index,
        "returncode": observation["returncode"],
        "timed_out": observation["timed_out"],
        "stderr_tail": tail,
    }


def _benchmark_surface(
    *,
    name: str,
    argv: Callable[[str], list[str]],
    cwd: Path,
    pairs: int,
    warmups: int,
    environment: dict[str, str],
    validator: PairValidator,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    for index in range(warmups):
        order = ["combined", "v5"] if index % 2 == 0 else ["v5", "combined"]
        for runtime in order:
            observation = _run(argv(runtime), cwd, environment)
            failure = _failure_receipt(name, runtime, "warmup", index, observation)
            if failure is not None:
                failures.append(failure)

    samples: dict[str, list[float]] = {"combined": [], "v5": []}
    rows: list[dict[str, Any]] = []
    for index in range(pairs):
        order = ["combined", "v5"] if index % 2 == 0 else ["v5", "combined"]
        observed: dict[str, Observation] = {}
        for runtime in order:
            observation = _run(argv(runtime), cwd, environment)
            observed[runtime] = observation
            samples[runtime].append(float(observation["seconds"]))
            failure = _failure_receipt(name, runtime, "measured", index, observation)
            if failure is not None:
                failures.append(failure)
        validation = validator(observed["combined"], observed["v5"])
        combined_seconds = float(observed["combined"]["seconds"])
        v5_seconds = float(observed["v5"]["seconds"])
        rows.append(
            {
                "index": index,
                "order": order,
                "combined": _observation_receipt(observed["combined"]),
                "v5": _observation_receipt(observed["v5"]),
                "v5_minus_combined_seconds": _rounded(v5_seconds - combined_seconds),
                "validation": validation,
            }
        )

    combined_summary = _summary(samples["combined"])
    v5_summary = _summary(samples["v5"])
    median_delta = float(v5_summary["p50_seconds"]) - float(
        combined_summary["p50_seconds"]
    )
    p95_delta = float(v5_summary["p95_seconds"]) - float(
        combined_summary["p95_seconds"]
    )
    combined_median = float(combined_summary["p50_seconds"])
    combined_p95 = float(combined_summary["p95_seconds"])
    comparison = {
        "median_delta_seconds": _rounded(median_delta),
        "median_absolute_delta_seconds": _rounded(abs(median_delta)),
        "median_change_fraction": _rounded(median_delta / combined_median),
        "p95_delta_seconds": _rounded(p95_delta),
        "p95_absolute_delta_seconds": _rounded(abs(p95_delta)),
        "p95_change_fraction": _rounded(p95_delta / combined_p95),
        "v5_median_regression_limit_seconds": MEDIAN_REGRESSION_LIMIT_SECONDS,
        "v5_median_regression_within_limit": (
            median_delta <= MEDIAN_REGRESSION_LIMIT_SECONDS
        ),
    }
    return (
        {
            "pairs": pairs,
            "warmups_per_runtime": warmups,
            "alternating_order": True,
            "raw_pairs": rows,
            "combined": combined_summary,
            "v5": v5_summary,
            "comparison": comparison,
            "exit_parity": all(
                row["combined"]["returncode"] == row["v5"]["returncode"]
                for row in rows
            ),
            "semantic_parity": all(
                row["validation"].get("semantic_equal") is True for row in rows
            ),
        },
        failures,
    )


def _tool_version(argv: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            env=_fixed_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=5,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else None


def _git_output(*arguments: str) -> bytes | None:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=ROOT,
            env=_fixed_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _source_hashes() -> dict[str, str]:
    return {
        "lab/benchmark_v5_runtime.py": _sha256_file(BENCHMARK),
        "subjects/combined-candidate/endurant-harness/scripts/endurant.py": _sha256_file(
            COMBINED
        ),
        "subjects/vnext/endurant-harness/scripts/endurant.py": _sha256_file(V5),
    }


def _runner_plan() -> dict[str, Any]:
    return {
        "name": "v5-runtime-one-command",
        "cwd": ".",
        "default_timeout": 30,
        "stages": [
            {
                "name": "one-command",
                "parallel": False,
                "commands": [
                    {
                        "id": "stdlib-smoke",
                        "argv": [
                            sys.executable,
                            "-S",
                            "-c",
                            "print('runtime-ok')",
                        ],
                        "evidence": "diagnostic",
                    }
                ],
            }
        ],
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    destination = path.expanduser()
    if not destination.is_absolute():
        destination = (Path.cwd() / destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_value = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_value)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pairs",
        type=_positive,
        default=DEFAULT_PAIRS,
        help=f"measured pairs per surface (default: {DEFAULT_PAIRS})",
    )
    parser.add_argument(
        "--warmups",
        type=_positive,
        default=DEFAULT_WARMUPS,
        help=f"warmups per runtime and surface (default: {DEFAULT_WARMUPS})",
    )
    parser.add_argument("--output", type=Path, help="optional atomically written JSON receipt")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    missing = [str(path) for path in RUNTIMES.values() if not path.is_file()]
    if missing:
        raise SystemExit(f"missing runtime: {', '.join(missing)}")

    environment = _fixed_environment()
    plan = _runner_plan()
    source_before = _source_hashes()
    all_failures: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="endurant-v5-runtime-") as temporary_value:
        temporary = Path(temporary_value)
        runner_root = temporary / "runner-root"
        runner_root.mkdir()
        plan_path = temporary / "one-command-plan.json"
        plan_path.write_bytes(_canonical_bytes(plan) + b"\n")
        log_roots = {name: temporary / f"{name}-logs" for name in RUNTIMES}

        template, failures = _benchmark_surface(
            name="template",
            argv=lambda runtime: [
                sys.executable,
                "-S",
                str(RUNTIMES[runtime]),
                "template",
            ],
            cwd=ROOT,
            pairs=args.pairs,
            warmups=args.warmups,
            environment=environment,
            validator=_template_pair,
        )
        all_failures.extend(failures)

        probe, failures = _benchmark_surface(
            name="probe",
            argv=lambda runtime: [
                sys.executable,
                "-S",
                str(RUNTIMES[runtime]),
                "probe",
                "--repo",
                str(ROOT),
                "--task",
                PROBE_TASK,
                "--format",
                "json",
            ],
            cwd=ROOT,
            pairs=args.pairs,
            warmups=args.warmups,
            environment=environment,
            validator=_probe_pair,
        )
        all_failures.extend(failures)

        runner, failures = _benchmark_surface(
            name="runner",
            argv=lambda runtime: [
                sys.executable,
                "-S",
                str(RUNTIMES[runtime]),
                "run",
                str(plan_path),
                "--repo",
                str(runner_root),
                "--format",
                "json",
                "--log-dir",
                str(log_roots[runtime]),
            ],
            cwd=ROOT,
            pairs=args.pairs,
            warmups=args.warmups,
            environment=environment,
            validator=_runner_pair,
        )
        all_failures.extend(failures)

    source_after = _source_hashes()
    surfaces = {"template": template, "probe": probe, "runner": runner}
    median_gates = {
        name: bool(surface["comparison"]["v5_median_regression_within_limit"])
        for name, surface in surfaces.items()
    }
    runner_status_parity = all(
        row["validation"].get("status_equal") is True
        for row in runner["raw_pairs"]
    )
    gates = {
        "no_command_failure": not all_failures,
        "exit_parity": all(surface["exit_parity"] for surface in surfaces.values()),
        "template_exact": template["semantic_parity"],
        "probe_semantic_parity_with_documented_exceptions": probe["semantic_parity"],
        "runner_status_parity": runner_status_parity,
        "runner_semantic_parity": runner["semantic_parity"],
        "source_inputs_unchanged_during_run": source_before == source_after,
        "v5_template_median_regression_within_25ms": median_gates["template"],
        "v5_probe_median_regression_within_25ms": median_gates["probe"],
        "v5_runner_median_regression_within_25ms": median_gates["runner"],
    }
    git_head = _git_output("rev-parse", "HEAD")
    git_status = _git_output("status", "--porcelain=v1", "--untracked-files=normal")
    clock = time.get_clock_info("perf_counter")
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "benchmark": "endurant-v5-runtime",
        "configuration": {
            "pairs": args.pairs,
            "warmups_per_runtime_and_surface": args.warmups,
            "default_pairs": DEFAULT_PAIRS,
            "alternating_order": True,
            "command_timeout_seconds": COMMAND_TIMEOUT_SECONDS,
            "v5_median_regression_limit_seconds": MEDIAN_REGRESSION_LIMIT_SECONDS,
            "probe_task": PROBE_TASK,
            "intentional_probe_differences": INTENTIONAL_PROBE_DIFFERENCES,
        },
        "environment": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "python_executable_name": Path(sys.executable).name,
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "git_version": _tool_version(["git", "--version"]),
            "ripgrep_version": _tool_version(["rg", "--version"]),
            "perf_counter_implementation": clock.implementation,
            "perf_counter_resolution_seconds": clock.resolution,
            "fixed_environment": {
                key: environment[key]
                for key in ("LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED", "TZ")
            },
        },
        "source": {
            "input_sha256_before": source_before,
            "input_sha256_after": source_after,
            "git_head": git_head.decode("ascii", errors="replace").strip()
            if git_head is not None
            else None,
            "git_status_sha256": _sha256_bytes(git_status)
            if git_status is not None
            else None,
            "probe_task_sha256": _sha256_bytes(PROBE_TASK.encode("utf-8")),
            "runner_plan_sha256": _canonical_sha256(plan),
        },
        "surfaces": surfaces,
        "command_failures": all_failures,
        "gates": gates,
        "passed": all(gates.values()),
    }
    if args.output is not None:
        _atomic_write(args.output, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
