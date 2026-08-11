#!/usr/bin/env python3
"""Build and verify the sanitized Endurant provenance-efficiency receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECEIPT = (
    PROJECT_ROOT / "artifacts" / "benchmarks" / "provenance-efficiency-ab.json"
)
PROMPT_PATH = PROJECT_ROOT / "lab" / "prompts" / "provenance-efficiency.txt"
BASELINE_PATCH = (
    PROJECT_ROOT / "lab" / "baselines" / "v5-provenance-ux.patch"
)
CURRENT_PACKAGE = PROJECT_ROOT / "endurant-harness"
FIXTURE_ROOT = PROJECT_ROOT / "fixtures" / "settings-override-correctness"

SCHEMA = "endurant-provenance-efficiency-v1"
EXPERIMENT = "endurant-provenance-ordinary-bug-smoke"
OLD_PACKAGE_SHA256 = (
    "476218926c85e119277ae4e84465bfc483ae124c82133401bf79b9c4c6810818"
)
NEW_PACKAGE_SHA256 = (
    "cf8c818dfa13e0d853628bf407efdd70db6b128764f52481e208ee88b138342c"
)
EXECUTED_RUNNER_SHA256 = (
    "46245f9a69b4509d57d93f5ccb181b16df4ac29cc7cd9139a08440fc03c7f6c5"
)
CODEX_CLI = "codex-cli 0.147.0"
EXPECTED_RUNS_SHA256 = (
    "87aa0b702945d2c2cbc24aaea0d879fbfb47d2a38f26d4d0b25e329d9460223f"
)
EXPECTED_RAW_RECEIPTS_SHA256 = (
    "25cf13da1b1dae5eb6e0845cb7419769876bce256ba28473745fe36ec0c7613c"
)
EXPECTED_ORDER = ("old", "new", "new", "old")
EXPECTED_CHANGED_PATHS = (
    "src/settings.py",
    "tests/test_settings.py",
    "tests/test_settings_cli.py",
)
EXPECTED_EXTERNAL = ("diff_check", "focused", "hidden", "local_ci")
METRICS = (
    "wall_seconds",
    "uncached_input_tokens",
    "command_count",
    "command_output_bytes",
    "first_production_edit_seconds",
    "pre_production_command_count",
    "provenance_attempts",
)
RUN_KEYS = {
    "accepted",
    "arm",
    "changed_paths",
    "command_count",
    "command_output_bytes",
    "external",
    "first_production_edit_seconds",
    "first_test_edit_seconds",
    "functional_passed",
    "index",
    "pre_production_command_count",
    "process_returncode",
    "provenance_attempts",
    "provenance_current",
    "provenance_expected_sha",
    "red_before_production",
    "scope_exact",
    "subject_package_sha256",
    "synthetic_command_count",
    "timed_out",
    "uncached_input_tokens",
    "usage",
    "wall_seconds",
}
BODY_KEYS = {
    "configuration",
    "date",
    "decision",
    "experiment",
    "metrics",
    "pairwise",
    "quality",
    "raw_receipts",
    "runs",
    "schema",
    "source",
    "subjects",
    "uncached_pairwise_new_wins",
    "wall_pairwise_new_wins",
}
LIMITATION = (
    "Two paired runs are an exploratory smoke focused on provenance UX, not a "
    "universal harness-speed benchmark."
)


class ReceiptError(ValueError):
    """A receipt or source input failed closed validation."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReceiptError(f"value is not canonical JSON: {exc}") from exc


def _duplicate_rejector(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReceiptError(f"non-finite JSON number: {value}")


def read_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_duplicate_rejector,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"cannot read JSON {path}: {exc}") from exc


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _finite(value: Any, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and (numeric > 0 if positive else numeric >= 0)


def _receipt_digest_valid(payload: dict[str, Any], body: dict[str, Any]) -> bool:
    try:
        expected = _sha256_bytes(_canonical_bytes(body))
    except ReceiptError:
        return False
    return _is_sha256(payload.get("receipt_sha256")) and (
        payload.get("receipt_sha256") == expected
    )


def source_input_paths() -> dict[str, Path]:
    paths = {
        "lab/provenance_efficiency_receipt.py": Path(__file__).resolve(),
        "lab/prompts/provenance-efficiency.txt": PROMPT_PATH,
        "lab/baselines/v5-provenance-ux.patch": BASELINE_PATCH,
        "fixtures/settings-override-correctness/fixture.json": (
            FIXTURE_ROOT / "fixture.json"
        ),
        "fixtures/settings-override-correctness/hidden_grade.py": (
            FIXTURE_ROOT / "hidden_grade.py"
        ),
        "fixtures/settings-override-correctness/task.txt": FIXTURE_ROOT / "task.txt",
    }
    template = FIXTURE_ROOT / "template"
    for path in sorted(template.rglob("*")):
        if path.is_symlink():
            raise ReceiptError(f"fixture input may not be a symlink: {path}")
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        paths[relative] = path
    return paths


def source_input_sha256() -> dict[str, str]:
    return {
        relative: _sha256_file(path)
        for relative, path in source_input_paths().items()
    }


def _raw_file_receipt(path: Path, raw_root: Path) -> dict[str, Any]:
    try:
        relative = path.resolve().relative_to(raw_root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise ReceiptError(f"raw capture escapes its declared root: {path}") from exc
    raw = path.read_bytes()
    return {
        "path": relative,
        "bytes": len(raw),
        "sha256": _sha256_bytes(raw),
    }


def _external_summary(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != set(EXPECTED_EXTERNAL):
        raise ReceiptError("run has the wrong external-check set")
    result: dict[str, dict[str, Any]] = {}
    for name in EXPECTED_EXTERNAL:
        row = value.get(name)
        if not isinstance(row, dict) or set(row) != {
            "returncode",
            "stdout_sha256",
            "stderr_sha256",
        }:
            raise ReceiptError(f"external check {name!r} is malformed")
        returncode = row.get("returncode")
        stdout_sha256 = row.get("stdout_sha256")
        stderr_sha256 = row.get("stderr_sha256")
        if not isinstance(returncode, int) or isinstance(returncode, bool):
            raise ReceiptError(f"external check {name!r} has no integer return code")
        if not _is_sha256(stdout_sha256) or not _is_sha256(stderr_sha256):
            raise ReceiptError(f"external check {name!r} has invalid output hashes")
        result[name] = {
            "returncode": returncode,
            "stdout_sha256": stdout_sha256,
            "stderr_sha256": stderr_sha256,
        }
    return result


def _derived_functional_pass(run: dict[str, Any]) -> bool:
    return bool(
        run.get("process_returncode") == 0
        and run.get("timed_out") is False
        and run.get("scope_exact") is True
        and run.get("changed_paths") == list(EXPECTED_CHANGED_PATHS)
        and run.get("red_before_production") is True
        and run.get("synthetic_command_count") == 0
        and isinstance(run.get("external"), dict)
        and all(
            isinstance(value, dict) and value.get("returncode") == 0
            for value in run["external"].values()
        )
    )


def _derived_accepted(run: dict[str, Any]) -> bool:
    return bool(
        _derived_functional_pass(run)
        and run.get("provenance_current") is True
        and run.get("provenance_expected_sha") is True
    )


def sanitize_run(raw: Any, *, index: int, arm: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ReceiptError("run summary must be an object")
    if raw.get("schema_version") != 1 or raw.get("index") != index:
        raise ReceiptError(f"run {index} has a wrong schema or index")
    if raw.get("arm") != arm:
        raise ReceiptError(f"run {index} has arm {raw.get('arm')!r}, expected {arm!r}")
    expected_sha = OLD_PACKAGE_SHA256 if arm == "old" else NEW_PACKAGE_SHA256
    if raw.get("expected_package_sha256") != expected_sha:
        raise ReceiptError(f"run {index} has the wrong subject package hash")
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        raise ReceiptError(f"run {index} has no usage receipt")
    usage_keys = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    sanitized_usage: dict[str, int] = {}
    for key in usage_keys:
        value = usage.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ReceiptError(f"run {index} has invalid {key}")
        sanitized_usage[key] = value
    uncached = sanitized_usage["input_tokens"] - sanitized_usage["cached_input_tokens"]
    if uncached < 0 or raw.get("uncached_input_tokens") != uncached:
        raise ReceiptError(f"run {index} has inconsistent uncached input")
    numeric_fields = (
        "wall_seconds",
        "first_production_edit_seconds",
        "first_test_edit_seconds",
    )
    for key in numeric_fields:
        if not _finite(raw.get(key), positive=True):
            raise ReceiptError(f"run {index} has invalid {key}")
    for key in (
        "command_count",
        "command_output_bytes",
        "pre_production_command_count",
        "provenance_attempts",
        "synthetic_command_count",
    ):
        if (
            not isinstance(raw.get(key), int)
            or isinstance(raw.get(key), bool)
            or raw[key] < 0
        ):
            raise ReceiptError(f"run {index} has invalid {key}")
    if (
        not isinstance(raw.get("process_returncode"), int)
        or isinstance(raw.get("process_returncode"), bool)
        or not all(
            isinstance(raw.get(key), bool)
            for key in (
                "timed_out",
                "scope_exact",
                "provenance_current",
                "provenance_expected_sha",
                "red_before_production",
                "accepted",
            )
        )
    ):
        raise ReceiptError(f"run {index} has malformed status fields")
    changed_paths = raw.get("changed_paths")
    if not isinstance(changed_paths, list) or not all(
        isinstance(path, str) for path in changed_paths
    ):
        raise ReceiptError(f"run {index} has invalid changed paths")
    external = _external_summary(raw.get("external"))
    result = {
        "index": index,
        "arm": arm,
        "subject_package_sha256": expected_sha,
        "process_returncode": raw.get("process_returncode"),
        "timed_out": raw.get("timed_out"),
        "wall_seconds": raw.get("wall_seconds"),
        "first_test_edit_seconds": raw.get("first_test_edit_seconds"),
        "first_production_edit_seconds": raw.get("first_production_edit_seconds"),
        "command_count": raw.get("command_count"),
        "command_output_bytes": raw.get("command_output_bytes"),
        "pre_production_command_count": raw.get("pre_production_command_count"),
        "provenance_attempts": raw.get("provenance_attempts"),
        "provenance_current": raw.get("provenance_current"),
        "provenance_expected_sha": raw.get("provenance_expected_sha"),
        "red_before_production": raw.get("red_before_production"),
        "synthetic_command_count": raw.get("synthetic_command_count"),
        "changed_paths": changed_paths,
        "scope_exact": raw.get("scope_exact"),
        "external": external,
        "usage": sanitized_usage,
        "uncached_input_tokens": uncached,
    }
    result["functional_passed"] = _derived_functional_pass(result)
    result["accepted"] = _derived_accepted(result)
    if raw.get("accepted") is not result["accepted"]:
        raise ReceiptError(f"run {index} reported an inconsistent acceptance result")
    if not _run_is_valid(result, index, arm):
        raise ReceiptError(f"run {index} failed sanitized receipt validation")
    return result


def _median(values: Iterable[int | float]) -> float:
    return round(float(statistics.median(values)), 9)


def aggregate_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    old = [run for run in runs if run.get("arm") == "old"]
    new = [run for run in runs if run.get("arm") == "new"]
    if len(old) != 2 or len(new) != 2:
        raise ReceiptError("receipt requires exactly two old and two new runs")
    metrics: dict[str, dict[str, float]] = {}
    for name in METRICS:
        old_median = _median(float(run[name]) for run in old)
        new_median = _median(float(run[name]) for run in new)
        if old_median <= 0:
            raise ReceiptError(f"metric {name!r} has a non-positive old median")
        metrics[name] = {
            "old_median": old_median,
            "new_median": new_median,
            "change_fraction": round((new_median - old_median) / old_median, 9),
        }
    by_index = {run["index"]: run for run in runs}
    pair_indexes = ((1, 2), (4, 3))
    pairwise = []
    for old_index, new_index in pair_indexes:
        old_run = by_index[old_index]
        new_run = by_index[new_index]
        pairwise.append(
            {
                "old_index": old_index,
                "new_index": new_index,
                "wall_change_fraction": round(
                    (new_run["wall_seconds"] - old_run["wall_seconds"])
                    / old_run["wall_seconds"],
                    9,
                ),
                "uncached_change_fraction": round(
                    (
                        new_run["uncached_input_tokens"]
                        - old_run["uncached_input_tokens"]
                    )
                    / old_run["uncached_input_tokens"],
                    9,
                ),
            }
        )
    quality: dict[str, dict[str, int]] = {}
    for arm, arm_runs in (("old", old), ("new", new)):
        quality[arm] = {
            "runs": len(arm_runs),
            "accepted": sum(run["accepted"] is True for run in arm_runs),
            "functional_passes": sum(
                run["functional_passed"] is True for run in arm_runs
            ),
            "scope_passes": sum(run["scope_exact"] is True for run in arm_runs),
            "red_before_production_passes": sum(
                run["red_before_production"] is True for run in arm_runs
            ),
            "provenance_current_passes": sum(
                run["provenance_current"] is True for run in arm_runs
            ),
        }
    return {
        "metrics": metrics,
        "pairwise": pairwise,
        "wall_pairwise_new_wins": sum(
            row["wall_change_fraction"] < 0 for row in pairwise
        ),
        "uncached_pairwise_new_wins": sum(
            row["uncached_change_fraction"] < 0 for row in pairwise
        ),
        "quality": quality,
    }


def build_receipt(
    summary_paths: list[Path],
    *,
    raw_root: Path,
    executed_runner_sha256: str,
    codex_cli: str,
) -> dict[str, Any]:
    if len(summary_paths) != len(EXPECTED_ORDER):
        raise ReceiptError("exactly four run summaries are required")
    if not _is_sha256(executed_runner_sha256):
        raise ReceiptError("executed runner SHA-256 is invalid")
    runs: list[dict[str, Any]] = []
    raw_receipts: list[dict[str, Any]] = []
    for index, (summary_path, arm) in enumerate(
        zip(summary_paths, EXPECTED_ORDER, strict=True), start=1
    ):
        raw = read_json(summary_path)
        runs.append(sanitize_run(raw, index=index, arm=arm))
        capture = summary_path.parent
        files = []
        for name in ("summary.json", "events.jsonl", "stderr.txt", "final.txt"):
            path = capture / name
            if not path.is_file() or path.is_symlink():
                raise ReceiptError(f"run {index} is missing raw capture {name}")
            files.append(_raw_file_receipt(path, raw_root))
        raw_receipts.append({"index": index, "arm": arm, "files": files})
    aggregate = aggregate_runs(runs)
    body = {
        "schema": SCHEMA,
        "experiment": EXPERIMENT,
        "date": "2026-08-11",
        "source": {
            "input_sha256": source_input_sha256(),
            "executed_runner_sha256": executed_runner_sha256,
            "prompt_sha256": _sha256_file(PROMPT_PATH),
        },
        "configuration": {
            "model": "gpt-5.6-terra",
            "reasoning_effort": "low",
            "order": list(EXPECTED_ORDER),
            "pairs": 2,
            "codex_cli": codex_cli,
            "project_scoped_skill": True,
            "global_installed_skill_disabled": True,
            "network": "disabled",
            "history_and_memories": "disabled",
            "subagents": "disabled",
        },
        "subjects": {
            "old": {"release": "v5", "package_sha256": OLD_PACKAGE_SHA256},
            "new": {"release": "v5", "package_sha256": NEW_PACKAGE_SHA256},
        },
        "runs": runs,
        "raw_receipts": raw_receipts,
        **aggregate,
        "decision": {
            "retain_provenance_ux": True,
            "speed_claim_ready": False,
            "limitation": LIMITATION,
        },
    }
    return {"body": body, "receipt_sha256": _sha256_bytes(_canonical_bytes(body))}


def _package_receipt(package: Path) -> dict[str, Any] | None:
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            str(package / "scripts" / "endurant.py"),
            "provenance",
            "--format",
            "json",
        ],
        cwd=PROJECT_ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": str(Path(sys.executable).parent)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        return None
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def baseline_reconstructs() -> bool:
    with tempfile.TemporaryDirectory(prefix="endurant-provenance-baseline-") as raw:
        root = Path(raw)
        package = root / "endurant-harness"
        shutil.copytree(CURRENT_PACKAGE, package)
        applied = subprocess.run(
            ["git", "apply", str(BASELINE_PATCH)],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
        if applied.returncode != 0:
            return False
        receipt = _package_receipt(package)
        return bool(
            receipt
            and receipt.get("package_integrity") is True
            and receipt.get("package_sha256") == OLD_PACKAGE_SHA256
            and receipt.get("marker_sha256") == OLD_PACKAGE_SHA256
        )


def _run_is_valid(run: Any, index: int, arm: str) -> bool:
    if not isinstance(run, dict):
        return False
    expected_sha = OLD_PACKAGE_SHA256 if arm == "old" else NEW_PACKAGE_SHA256
    if (
        set(run) != RUN_KEYS
        or run.get("index") != index
        or run.get("arm") != arm
        or run.get("subject_package_sha256") != expected_sha
        or run.get("changed_paths") != list(EXPECTED_CHANGED_PATHS)
        or not isinstance(run.get("usage"), dict)
        or not isinstance(run.get("external"), dict)
        or set(run["external"]) != set(EXPECTED_EXTERNAL)
        or not isinstance(run.get("process_returncode"), int)
        or isinstance(run.get("process_returncode"), bool)
        or not all(
            isinstance(run.get(name), bool)
            for name in (
                "timed_out",
                "scope_exact",
                "provenance_current",
                "provenance_expected_sha",
                "red_before_production",
                "functional_passed",
                "accepted",
            )
        )
    ):
        return False
    if any(
        not _finite(run.get(name), positive=True)
        for name in (
            "wall_seconds",
            "first_test_edit_seconds",
            "first_production_edit_seconds",
        )
    ):
        return False
    if any(
        not isinstance(run.get(name), int)
        or isinstance(run.get(name), bool)
        or run[name] < 0
        for name in (
            "command_count",
            "command_output_bytes",
            "pre_production_command_count",
            "provenance_attempts",
            "synthetic_command_count",
            "uncached_input_tokens",
        )
    ):
        return False
    usage = run["usage"]
    if set(usage) != {
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    } or any(
        not isinstance(usage.get(name), int)
        or isinstance(usage.get(name), bool)
        or usage[name] < 0
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
    ):
        return False
    if run["uncached_input_tokens"] != (
        usage["input_tokens"] - usage["cached_input_tokens"]
    ):
        return False
    if (
        run["first_test_edit_seconds"] > run["wall_seconds"]
        or run["first_production_edit_seconds"] > run["wall_seconds"]
        or run["pre_production_command_count"] > run["command_count"]
        or run["provenance_attempts"] > run["command_count"]
        or run["synthetic_command_count"] > run["command_count"]
        or run["command_count"] < 1
    ):
        return False
    if any(
        not isinstance(value, dict)
        or set(value) != {"returncode", "stdout_sha256", "stderr_sha256"}
        or not isinstance(value.get("returncode"), int)
        or isinstance(value.get("returncode"), bool)
        or value.get("returncode") != 0
        or not _is_sha256(value.get("stdout_sha256"))
        or not _is_sha256(value.get("stderr_sha256"))
        for value in run["external"].values()
    ):
        return False
    return bool(
        run.get("functional_passed") is _derived_functional_pass(run)
        and run.get("accepted") is _derived_accepted(run)
    )


def _raw_receipts_valid(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    for index, row in enumerate(value, start=1):
        if (
            not isinstance(row, dict)
            or set(row) != {"index", "arm", "files"}
            or row.get("index") != index
            or row.get("arm") != EXPECTED_ORDER[index - 1]
            or not isinstance(row.get("files"), list)
            or len(row["files"]) != 4
        ):
            return False
        paths = set()
        for file_receipt in row["files"]:
            if not isinstance(file_receipt, dict) or set(file_receipt) != {
                "path",
                "bytes",
                "sha256",
            }:
                return False
            path = file_receipt.get("path")
            if (
                not isinstance(path, str)
                or path.startswith("/")
                or ".." in Path(path).parts
                or path in paths
                or not isinstance(file_receipt.get("bytes"), int)
                or isinstance(file_receipt.get("bytes"), bool)
                or file_receipt["bytes"] < 0
                or not _is_sha256(file_receipt.get("sha256"))
            ):
                return False
            paths.add(path)
        expected_prefix = f"{index:02d}-{EXPECTED_ORDER[index - 1]}/capture/"
        if paths != {
            expected_prefix + name
            for name in ("summary.json", "events.jsonl", "stderr.txt", "final.txt")
        }:
            return False
    return _sha256_bytes(_canonical_bytes(value)) == EXPECTED_RAW_RECEIPTS_SHA256


def validate_receipt(payload: Any, *, verify_sources: bool = True) -> dict[str, bool]:
    if not isinstance(payload, dict):
        return {"receipt_is_object": False}
    body = payload.get("body")
    if not isinstance(body, dict):
        return {"receipt_body_is_object": False}
    source = body.get("source") if isinstance(body.get("source"), dict) else {}
    configuration = (
        body.get("configuration") if isinstance(body.get("configuration"), dict) else {}
    )
    subjects = body.get("subjects") if isinstance(body.get("subjects"), dict) else {}
    runs = body.get("runs") if isinstance(body.get("runs"), list) else []
    run_matrix_valid = len(runs) == 4 and all(
        _run_is_valid(run, index, arm)
        for index, (run, arm) in enumerate(
            zip(runs, EXPECTED_ORDER, strict=True), start=1
        )
    )
    aggregate_valid = False
    if run_matrix_valid:
        try:
            expected_aggregate = aggregate_runs(runs)
        except (KeyError, ReceiptError, TypeError, ZeroDivisionError):
            expected_aggregate = {}
        aggregate_valid = all(
            body.get(key) == expected_aggregate.get(key)
            for key in (
                "metrics",
                "pairwise",
                "wall_pairwise_new_wins",
                "uncached_pairwise_new_wins",
                "quality",
            )
        )
    new_package = _package_receipt(CURRENT_PACKAGE) if verify_sources else None
    source_current = (
        source.get("input_sha256") == source_input_sha256()
        if verify_sources
        else isinstance(source.get("input_sha256"), dict)
    )
    baseline_current = baseline_reconstructs() if verify_sources else True
    quality = body.get("quality") if isinstance(body.get("quality"), dict) else {}
    decision = body.get("decision") if isinstance(body.get("decision"), dict) else {}
    checks = {
        "schema_and_experiment": (
            set(payload) == {"body", "receipt_sha256"}
            and set(body) == BODY_KEYS
            and body.get("schema") == SCHEMA
            and body.get("experiment") == EXPERIMENT
            and body.get("date") == "2026-08-11"
        ),
        "receipt_digest": _receipt_digest_valid(payload, body),
        "source_inputs_current": (
            set(source) == {"input_sha256", "executed_runner_sha256", "prompt_sha256"}
            and source_current
        ),
        "executed_runner_identified": (
            source.get("executed_runner_sha256") == EXECUTED_RUNNER_SHA256
        ),
        "prompt_is_exact": source.get("prompt_sha256") == _sha256_file(PROMPT_PATH),
        "configuration_is_normalized": configuration == {
            "model": "gpt-5.6-terra",
            "reasoning_effort": "low",
            "order": list(EXPECTED_ORDER),
            "pairs": 2,
            "codex_cli": CODEX_CLI,
            "project_scoped_skill": True,
            "global_installed_skill_disabled": True,
            "network": "disabled",
            "history_and_memories": "disabled",
            "subagents": "disabled",
        },
        "subjects_are_exact": subjects == {
            "old": {"release": "v5", "package_sha256": OLD_PACKAGE_SHA256},
            "new": {"release": "v5", "package_sha256": NEW_PACKAGE_SHA256},
        },
        "current_package_is_measured_subject": (
            not verify_sources
            or bool(
                new_package
                and new_package.get("package_integrity") is True
                and new_package.get("package_sha256") == NEW_PACKAGE_SHA256
                and new_package.get("marker_sha256") == NEW_PACKAGE_SHA256
            )
        ),
        "old_baseline_reconstructs": baseline_current,
        "run_matrix_is_valid": run_matrix_valid,
        "sanitized_runs_are_frozen": (
            run_matrix_valid
            and _sha256_bytes(_canonical_bytes(runs)) == EXPECTED_RUNS_SHA256
        ),
        "raw_receipts_are_sanitized": _raw_receipts_valid(body.get("raw_receipts")),
        "aggregates_recompute": aggregate_valid,
        "quality_gates_hold": (
            quality.get("new") == {
                "runs": 2,
                "accepted": 2,
                "functional_passes": 2,
                "scope_passes": 2,
                "red_before_production_passes": 2,
                "provenance_current_passes": 2,
            }
            and quality.get("old") == {
                "runs": 2,
                "accepted": 1,
                "functional_passes": 2,
                "scope_passes": 2,
                "red_before_production_passes": 2,
                "provenance_current_passes": 1,
            }
        ),
        "decision_is_bounded": decision == {
            "retain_provenance_ux": True,
            "speed_claim_ready": False,
            "limitation": LIMITATION,
        },
    }
    return checks


def verify_raw_receipts(payload: Any, raw_root: Path) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("body"), dict):
        return False
    receipts = payload["body"].get("raw_receipts")
    if not _raw_receipts_valid(receipts):
        return False
    root = raw_root.resolve()
    for row in receipts:
        for expected in row["files"]:
            path = root / expected["path"]
            try:
                if path.is_symlink() or not path.is_file():
                    return False
                actual = _raw_file_receipt(path, root)
            except (OSError, ReceiptError):
                return False
            if actual != expected:
                return False
    return True


def _write_exclusive(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(_canonical_bytes(value))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="sanitize four ignored run summaries")
    build.add_argument("--run-summary", action="append", type=Path, required=True)
    build.add_argument("--raw-root", type=Path, required=True)
    build.add_argument("--executed-runner-sha256", required=True)
    build.add_argument("--codex-cli", required=True)
    build.add_argument("--output", type=Path, default=DEFAULT_RECEIPT)
    check = subparsers.add_parser("check", help="verify the tracked receipt")
    check.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    check.add_argument("--raw-root", type=Path)
    args = parser.parse_args()

    try:
        if args.command == "build":
            receipt = build_receipt(
                args.run_summary,
                raw_root=args.raw_root,
                executed_runner_sha256=args.executed_runner_sha256,
                codex_cli=args.codex_cli,
            )
            _write_exclusive(args.output, receipt)
            result = {
                "status": "passed",
                "output": args.output.name,
                "receipt_sha256": receipt["receipt_sha256"],
            }
        else:
            receipt = read_json(args.receipt)
            checks = validate_receipt(receipt)
            if args.raw_root is not None:
                checks["raw_files_match"] = verify_raw_receipts(
                    receipt, args.raw_root
                )
            result = {"status": "passed" if all(checks.values()) else "failed", "checks": checks}
    except (OSError, ReceiptError, subprocess.SubprocessError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
