#!/usr/bin/env python3
"""Build and verify a deterministic Endurant Harness release archive.

The archive contains regular package files below exactly one
``endurant-harness/`` prefix. Release receipts contain no timestamps or local
paths, so identical package inputs produce byte-identical archives and
receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import stat
import statistics
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE = PROJECT_ROOT / "endurant-harness"
DEFAULT_ARCHIVE = PROJECT_ROOT / "dist" / "endurant-harness-v7.zip"
DEFAULT_RECEIPT = PROJECT_ROOT / "artifacts" / "benchmarks" / "v7-release.json"
DEFAULT_RUNTIME_RECEIPT = (
    PROJECT_ROOT / "artifacts" / "benchmarks" / "v5-runtime.json"
)

SCHEMA = "endurant-harness-release-v1"
PACKAGE_NAME = "endurant-harness"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
REGULAR_MODE = stat.S_IFREG | 0o644
MAX_FILES = 500
MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_RUNTIME_RECEIPT_BYTES = 16 * 1024 * 1024
SHA256_RE = re.compile(r"[0-9a-f]{64}")

RUNTIME_SOURCE_PATHS = (
    "lab/benchmark_v5_runtime.py",
    "subjects/combined-candidate/endurant-harness/scripts/endurant.py",
    "subjects/vnext/endurant-harness/scripts/endurant.py",
)
RUNTIME_SURFACES = ("probe", "runner", "template")
RUNTIME_GATE_NAMES = {
    "exit_parity",
    "no_command_failure",
    "probe_semantic_parity_with_documented_exceptions",
    "runner_semantic_parity",
    "runner_status_parity",
    "source_inputs_unchanged_during_run",
    "template_exact",
    "v5_probe_median_regression_within_25ms",
    "v5_runner_median_regression_within_25ms",
    "v5_template_median_regression_within_25ms",
}
RUNTIME_CONFIGURATION_KEYS = {
    "alternating_order",
    "command_timeout_seconds",
    "default_pairs",
    "intentional_probe_differences",
    "pairs",
    "probe_task",
    "v5_median_regression_limit_seconds",
    "warmups_per_runtime_and_surface",
}
RUNTIME_PROBE_TASK = "Fix select_records duplicate stability"
RUNTIME_REGRESSION_LIMIT_SECONDS = 0.025
RUNTIME_INTENTIONAL_PROBE_DIFFERENCES = [
    "candidate_paths ordering and bounded selection",
    "task_symbols",
    "contract profile_sha256 values",
    "candidate-derived warning, truncated, and incomplete values",
]

FORBIDDEN_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".temp",
    ".tmp",
    "__pycache__",
    "backup",
    "backups",
    "temp",
    "tmp",
}
FORBIDDEN_EXACT_FILE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
}
FORBIDDEN_SUFFIXES = {
    ".bak",
    ".backup",
    ".orig",
    ".pyc",
    ".pyo",
    ".rej",
    ".swo",
    ".swp",
    ".temp",
    ".tmp",
}


class ReleaseError(ValueError):
    """A release input or artifact failed a closed validation gate."""


@dataclass(frozen=True)
class PackageMember:
    relative_path: str
    archive_path: str
    data: bytes
    sha256: str

    def receipt_value(self) -> dict[str, Any]:
        return {
            "path": self.archive_path,
            "sha256": self.sha256,
            "size_bytes": len(self.data),
        }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_json_constant(value: str) -> None:
    raise ReleaseError(f"non-finite JSON number is forbidden: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseError(f"duplicate JSON key is forbidden: {key}")
        value[key] = item
    return value


def _load_json_bytes(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"invalid {label} JSON: {exc}") from exc


def _canonical_json_bytes(value: Any) -> bytes:
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
        raise ReleaseError(f"receipt is not canonical JSON: {exc}") from exc


def _validate_package_root(package: Path) -> Path:
    expanded = package.expanduser()
    try:
        mode = expanded.lstat().st_mode
    except OSError as exc:
        raise ReleaseError(f"cannot inspect package root {expanded}: {exc}") from exc
    if stat.S_ISLNK(mode):
        raise ReleaseError("package root may not be a symlink")
    if not stat.S_ISDIR(mode):
        raise ReleaseError(f"package root is not a directory: {expanded}")
    root = expanded.resolve()
    if root.name != PACKAGE_NAME:
        raise ReleaseError(
            f"package root must be named {PACKAGE_NAME!r}, got {root.name!r}"
        )
    return root


def _is_forbidden_relative(relative: PurePosixPath, *, is_directory: bool) -> str | None:
    for part in relative.parts:
        folded = part.casefold()
        if folded in FORBIDDEN_DIR_NAMES:
            return f"forbidden directory name: {part}"
        if part in {".", ".."} or "\x00" in part or "\\" in part:
            return f"unsafe path component: {part!r}"
        if any(ord(character) < 32 or ord(character) == 127 for character in part):
            return f"control character in path component: {part!r}"
    if is_directory:
        return None
    name = relative.name
    folded_name = name.casefold()
    if re.match(r"^(?:readme|changelog)(?:[._-]|$)", folded_name):
        return f"documentation file is excluded from the package: {name}"
    if name in FORBIDDEN_EXACT_FILE_NAMES:
        return f"generated operating-system file: {name}"
    if folded_name.endswith("~"):
        return f"backup file is forbidden: {name}"
    if any(folded_name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        return f"generated, backup, or temporary file is forbidden: {name}"
    return None


def _scan_directory(root: Path, directory: Path) -> Iterable[tuple[str, bytes]]:
    try:
        entries = sorted(os.scandir(directory), key=lambda item: item.name)
    except OSError as exc:
        raise ReleaseError(f"cannot scan package directory {directory}: {exc}") from exc
    for entry in entries:
        path = Path(entry.path)
        relative = PurePosixPath(path.relative_to(root).as_posix())
        try:
            mode = entry.stat(follow_symlinks=False).st_mode
        except OSError as exc:
            raise ReleaseError(f"cannot inspect package path {relative}: {exc}") from exc
        if stat.S_ISLNK(mode):
            raise ReleaseError(f"package may not contain symlinks: {relative}")
        if stat.S_ISDIR(mode):
            reason = _is_forbidden_relative(relative, is_directory=True)
            if reason:
                raise ReleaseError(f"{relative}: {reason}")
            yield from _scan_directory(root, path)
            continue
        if not stat.S_ISREG(mode):
            raise ReleaseError(f"package contains a non-regular file: {relative}")
        reason = _is_forbidden_relative(relative, is_directory=False)
        if reason:
            raise ReleaseError(f"{relative}: {reason}")
        try:
            before = path.stat(follow_symlinks=False)
            data = path.read_bytes()
            after = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ReleaseError(f"cannot read package file {relative}: {exc}") from exc
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
        )
        if identity_before != identity_after or len(data) != after.st_size:
            raise ReleaseError(f"package file changed while being read: {relative}")
        yield relative.as_posix(), data


def _scan_package(package: Path) -> tuple[PackageMember, ...]:
    root = _validate_package_root(package)
    members: list[PackageMember] = []
    casefolded_paths: set[str] = set()
    total_bytes = 0
    for relative, data in _scan_directory(root, root):
        archive_path = f"{PACKAGE_NAME}/{relative}"
        folded = archive_path.casefold()
        if folded in casefolded_paths:
            raise ReleaseError(f"case-insensitive package path collision: {archive_path}")
        casefolded_paths.add(folded)
        total_bytes += len(data)
        if len(members) >= MAX_FILES:
            raise ReleaseError(f"package exceeds {MAX_FILES} files")
        if total_bytes > MAX_PACKAGE_BYTES:
            raise ReleaseError(f"package exceeds {MAX_PACKAGE_BYTES} bytes")
        members.append(
            PackageMember(
                relative_path=relative,
                archive_path=archive_path,
                data=data,
                sha256=_sha256_bytes(data),
            )
        )
    if not members:
        raise ReleaseError("package contains no files")
    if "endurant-harness/SKILL.md" not in {item.archive_path for item in members}:
        raise ReleaseError("package is missing SKILL.md")
    if "endurant-harness/scripts/endurant.py" not in {
        item.archive_path for item in members
    }:
        raise ReleaseError("package is missing scripts/endurant.py")
    return tuple(sorted(members, key=lambda item: item.archive_path))


def _invoke_provenance(package: Path) -> dict[str, Any]:
    root = _validate_package_root(package)
    runtime = root / "scripts" / "endurant.py"
    try:
        mode = runtime.lstat().st_mode
    except OSError as exc:
        raise ReleaseError(f"cannot inspect provenance runtime: {exc}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ReleaseError("provenance runtime must be a regular, non-symlink file")
    try:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            [sys.executable, "-S", str(runtime), "provenance", "--format", "json"],
            cwd=str(root.parent),
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseError(f"provenance invocation failed: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseError(
            f"provenance invocation exited {completed.returncode}: {stderr[:1000]}"
        )
    if len(completed.stdout) > MAX_RECEIPT_BYTES:
        raise ReleaseError("provenance output is unexpectedly large")
    result = _load_json_bytes(completed.stdout, "provenance")
    if not isinstance(result, dict):
        raise ReleaseError("provenance output must be a JSON object")
    release = result.get("release")
    package_sha256 = result.get("package_sha256")
    marker_sha256 = result.get("marker_sha256")
    if not isinstance(release, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", release
    ):
        raise ReleaseError("provenance release is invalid")
    if not isinstance(package_sha256, str) or not SHA256_RE.fullmatch(package_sha256):
        raise ReleaseError("provenance package SHA-256 is invalid")
    if not isinstance(marker_sha256, str) or not SHA256_RE.fullmatch(marker_sha256):
        raise ReleaseError("provenance marker SHA-256 is invalid")
    if result.get("package_integrity") is not True:
        raise ReleaseError("package provenance integrity is not current")
    if marker_sha256 != package_sha256:
        raise ReleaseError("provenance marker does not match the canonical package hash")
    return {
        "integrity": True,
        "marker_sha256": marker_sha256,
        "release": release,
        "sha256": package_sha256,
    }


def _stable_package_snapshot(
    package: Path,
) -> tuple[tuple[PackageMember, ...], dict[str, Any]]:
    before = _scan_package(package)
    provenance = _invoke_provenance(package)
    after = _scan_package(package)
    if before != after:
        raise ReleaseError("package changed while provenance was being computed")
    return after, provenance


def _write_zip(handle: Any, members: Sequence[PackageMember]) -> None:
    with zipfile.ZipFile(
        handle,
        mode="w",
        compression=zipfile.ZIP_STORED,
        allowZip64=False,
        strict_timestamps=True,
    ) as archive:
        archive.comment = b""
        for member in members:
            info = zipfile.ZipInfo(member.archive_path, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.create_version = 20
            info.extract_version = 10
            info.external_attr = REGULAR_MODE << 16
            info.internal_attr = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(info, member.data, compress_type=zipfile.ZIP_STORED)


def _finite_number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReleaseError(f"{label} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed) or (positive and parsed <= 0):
        qualifier = "positive finite" if positive else "finite"
        raise ReleaseError(f"{label} must be {qualifier}")
    return parsed


def _bounded_int(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReleaseError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise ReleaseError(f"{label} must be {minimum}..{maximum}")
    return value


def _rounded_timing(value: float) -> float:
    return round(value, 9)


def _timing_summary(samples: Sequence[float]) -> dict[str, Any]:
    if not samples:
        raise ReleaseError("runtime timing sample set is empty")
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "max_seconds": _rounded_timing(max(samples)),
        "min_seconds": _rounded_timing(min(samples)),
        "p50_seconds": _rounded_timing(statistics.median(samples)),
        "p95_seconds": _rounded_timing(ordered[p95_index]),
        "samples_seconds": [_rounded_timing(value) for value in samples],
    }


def _stable_source_sha256(relative_path: str) -> str:
    path = PROJECT_ROOT / relative_path
    try:
        before = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseError(f"cannot inspect runtime benchmark source {relative_path}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ReleaseError(
            f"runtime benchmark source must be a regular, non-symlink file: {relative_path}"
        )
    try:
        data = path.read_bytes()
        after = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseError(f"cannot read runtime benchmark source {relative_path}: {exc}") from exc
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(data) != after.st_size:
        raise ReleaseError(f"runtime benchmark source changed while hashing: {relative_path}")
    return _sha256_bytes(data)


def _validate_runtime_observation(
    value: Any, label: str, *, timeout_seconds: float
) -> float:
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} must be an object")
    _require_exact_keys(
        value,
        {
            "returncode",
            "seconds",
            "stderr_bytes",
            "stderr_sha256",
            "stdout_bytes",
            "stdout_sha256",
            "timed_out",
        },
        label,
    )
    seconds = _finite_number(value["seconds"], f"{label}.seconds", positive=True)
    if seconds > timeout_seconds:
        raise ReleaseError(f"{label}.seconds exceeds the configured timeout")
    if value["returncode"] != 0 or isinstance(value["returncode"], bool):
        raise ReleaseError(f"{label} is not a successful command")
    if value["timed_out"] is not False:
        raise ReleaseError(f"{label} timed out")
    for stream in ("stdout", "stderr"):
        size = value[f"{stream}_bytes"]
        digest = value[f"{stream}_sha256"]
        _bounded_int(size, f"{label}.{stream}_bytes", minimum=0, maximum=16 * 1024 * 1024)
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ReleaseError(f"{label}.{stream}_sha256 is invalid")
    empty_sha256 = _sha256_bytes(b"")
    if value["stderr_bytes"] != 0 or value["stderr_sha256"] != empty_sha256:
        raise ReleaseError(f"{label} contains unexpected stderr evidence")
    if value["stdout_bytes"] <= 0:
        raise ReleaseError(f"{label} contains no stdout evidence")
    return seconds


def _validate_runtime_pair_validation(surface: str, value: Any, label: str) -> bool:
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} must be an object")
    common = {
        "combined_normalized_sha256",
        "combined_parse_error",
        "semantic_equal",
        "v5_normalized_sha256",
        "v5_parse_error",
    }
    if surface == "template":
        expected = {
            "combined_json_sha256",
            "combined_parse_error",
            "semantic_equal",
            "stderr_exact",
            "stdout_exact",
            "v5_json_sha256",
            "v5_parse_error",
        }
        combined_hash_key = "combined_json_sha256"
        v5_hash_key = "v5_json_sha256"
    elif surface == "probe":
        expected = common | {"combined_intentional_fields", "v5_intentional_fields"}
        combined_hash_key = "combined_normalized_sha256"
        v5_hash_key = "v5_normalized_sha256"
    else:
        expected = common | {"combined_status", "status_equal", "v5_status"}
        combined_hash_key = "combined_normalized_sha256"
        v5_hash_key = "v5_normalized_sha256"
    _require_exact_keys(value, expected, label)
    if value["semantic_equal"] is not True:
        raise ReleaseError(f"{label} does not prove semantic parity")
    if value["combined_parse_error"] is not None or value["v5_parse_error"] is not None:
        raise ReleaseError(f"{label} contains a parse error")
    combined_hash = value[combined_hash_key]
    v5_hash = value[v5_hash_key]
    if (
        not isinstance(combined_hash, str)
        or not SHA256_RE.fullmatch(combined_hash)
        or v5_hash != combined_hash
    ):
        raise ReleaseError(f"{label} normalized result hashes do not match")
    if surface == "template":
        if value["stdout_exact"] is not True or value["stderr_exact"] is not True:
            raise ReleaseError(f"{label} is not byte-exact")
    elif surface == "probe":
        if not isinstance(value["combined_intentional_fields"], dict) or not isinstance(
            value["v5_intentional_fields"], dict
        ):
            raise ReleaseError(f"{label} intentional difference details are invalid")
    else:
        if (
            value["status_equal"] is not True
            or value["combined_status"] != "passed"
            or value["v5_status"] != "passed"
        ):
            raise ReleaseError(f"{label} runner status parity is invalid")
    return True


def _validate_runtime_summary(
    value: Any, expected: dict[str, Any], label: str
) -> None:
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} must be an object")
    _require_exact_keys(
        value,
        {"max_seconds", "min_seconds", "p50_seconds", "p95_seconds", "samples_seconds"},
        label,
    )
    if value != expected:
        raise ReleaseError(f"{label} does not recompute from raw timing pairs")


def _runtime_comparison(
    combined: dict[str, Any], v5: dict[str, Any]
) -> dict[str, Any]:
    combined_p50 = float(combined["p50_seconds"])
    combined_p95 = float(combined["p95_seconds"])
    if combined_p50 <= 0 or combined_p95 <= 0:
        raise ReleaseError("combined runtime summary must be positive")
    median_delta = float(v5["p50_seconds"]) - combined_p50
    p95_delta = float(v5["p95_seconds"]) - combined_p95
    return {
        "median_absolute_delta_seconds": _rounded_timing(abs(median_delta)),
        "median_change_fraction": _rounded_timing(median_delta / combined_p50),
        "median_delta_seconds": _rounded_timing(median_delta),
        "p95_absolute_delta_seconds": _rounded_timing(abs(p95_delta)),
        "p95_change_fraction": _rounded_timing(p95_delta / combined_p95),
        "p95_delta_seconds": _rounded_timing(p95_delta),
        "v5_median_regression_limit_seconds": RUNTIME_REGRESSION_LIMIT_SECONDS,
        "v5_median_regression_within_limit": (
            median_delta <= RUNTIME_REGRESSION_LIMIT_SECONDS
        ),
    }


def _validate_runtime_surface(
    surface: str,
    value: Any,
    *,
    pairs: int,
    warmups: int,
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, bool]]:
    label = f"runtime surface {surface}"
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} must be an object")
    _require_exact_keys(
        value,
        {
            "alternating_order",
            "combined",
            "comparison",
            "exit_parity",
            "pairs",
            "raw_pairs",
            "semantic_parity",
            "v5",
            "warmups_per_runtime",
        },
        label,
    )
    if (
        value["pairs"] != pairs
        or isinstance(value["pairs"], bool)
        or value["warmups_per_runtime"] != warmups
        or isinstance(value["warmups_per_runtime"], bool)
        or value["alternating_order"] is not True
    ):
        raise ReleaseError(f"{label} configuration differs from the benchmark receipt")
    rows = value["raw_pairs"]
    if not isinstance(rows, list) or len(rows) != pairs:
        raise ReleaseError(f"{label} raw pair count differs")
    samples: dict[str, list[float]] = {"combined": [], "v5": []}
    exit_parity = True
    semantic_parity = True
    runner_status_parity = True
    for index, row in enumerate(rows):
        row_label = f"{label} pair {index}"
        if not isinstance(row, dict):
            raise ReleaseError(f"{row_label} must be an object")
        _require_exact_keys(
            row,
            {
                "combined",
                "index",
                "order",
                "v5",
                "v5_minus_combined_seconds",
                "validation",
            },
            row_label,
        )
        expected_order = ["combined", "v5"] if index % 2 == 0 else ["v5", "combined"]
        if row["index"] != index or isinstance(row["index"], bool):
            raise ReleaseError(f"{row_label} index differs")
        if row["order"] != expected_order:
            raise ReleaseError(f"{row_label} order is not alternating")
        combined_seconds = _validate_runtime_observation(
            row["combined"], f"{row_label}.combined", timeout_seconds=timeout_seconds
        )
        v5_seconds = _validate_runtime_observation(
            row["v5"], f"{row_label}.v5", timeout_seconds=timeout_seconds
        )
        samples["combined"].append(combined_seconds)
        samples["v5"].append(v5_seconds)
        expected_delta = _rounded_timing(v5_seconds - combined_seconds)
        observed_delta = _finite_number(
            row["v5_minus_combined_seconds"], f"{row_label}.delta"
        )
        if observed_delta != expected_delta:
            raise ReleaseError(f"{row_label} delta does not recompute")
        semantic = _validate_runtime_pair_validation(
            surface, row["validation"], f"{row_label}.validation"
        )
        exit_parity = exit_parity and (
            row["combined"]["returncode"] == row["v5"]["returncode"]
        )
        semantic_parity = semantic_parity and semantic
        if surface == "runner":
            runner_status_parity = runner_status_parity and (
                row["validation"]["status_equal"] is True
            )
    combined_summary = _timing_summary(samples["combined"])
    v5_summary = _timing_summary(samples["v5"])
    _validate_runtime_summary(value["combined"], combined_summary, f"{label}.combined")
    _validate_runtime_summary(value["v5"], v5_summary, f"{label}.v5")
    comparison = _runtime_comparison(combined_summary, v5_summary)
    if value["comparison"] != comparison:
        raise ReleaseError(f"{label} comparison does not recompute from raw samples")
    if comparison["v5_median_regression_within_limit"] is not True:
        raise ReleaseError(f"{label} exceeds the 25ms median regression limit")
    if value["exit_parity"] is not exit_parity or exit_parity is not True:
        raise ReleaseError(f"{label} exit parity does not recompute")
    if value["semantic_parity"] is not semantic_parity or semantic_parity is not True:
        raise ReleaseError(f"{label} semantic parity does not recompute")
    summary = {
        "combined_p50_seconds": combined_summary["p50_seconds"],
        "median_delta_seconds": comparison["median_delta_seconds"],
        "v5_p50_seconds": v5_summary["p50_seconds"],
    }
    recomputed = {
        "exit_parity": exit_parity,
        "median_within_limit": comparison["v5_median_regression_within_limit"],
        "runner_status_parity": runner_status_parity,
        "semantic_parity": semantic_parity,
    }
    return summary, recomputed


def _load_runtime_benchmark_receipt(path: Path) -> dict[str, Any]:
    expanded = path.expanduser()
    try:
        before = expanded.lstat()
    except OSError as exc:
        raise ReleaseError(f"cannot inspect runtime benchmark receipt {expanded}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ReleaseError("runtime benchmark receipt must be a regular, non-symlink file")
    if before.st_size > MAX_RUNTIME_RECEIPT_BYTES:
        raise ReleaseError("runtime benchmark receipt is unexpectedly large")
    try:
        raw = expanded.read_bytes()
        after = expanded.lstat()
    except OSError as exc:
        raise ReleaseError(f"cannot read runtime benchmark receipt: {exc}") from exc
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(raw) != after.st_size:
        raise ReleaseError("runtime benchmark receipt changed while being read")
    value = _load_json_bytes(raw, "runtime benchmark receipt")
    if not isinstance(value, dict):
        raise ReleaseError("runtime benchmark receipt must be a JSON object")
    _require_exact_keys(
        value,
        {
            "benchmark",
            "command_failures",
            "configuration",
            "environment",
            "gates",
            "passed",
            "schema_version",
            "source",
            "surfaces",
        },
        "runtime benchmark receipt",
    )
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        raise ReleaseError("runtime benchmark receipt schema_version must be 1")
    if value["benchmark"] != "endurant-v5-runtime":
        raise ReleaseError("runtime benchmark receipt identity differs")
    if value["command_failures"] != []:
        raise ReleaseError("runtime benchmark receipt contains command failures")
    if value["passed"] is not True:
        raise ReleaseError("runtime benchmark receipt is not passing")
    if not isinstance(value["environment"], dict):
        raise ReleaseError("runtime benchmark environment must be an object")

    configuration = value["configuration"]
    if not isinstance(configuration, dict):
        raise ReleaseError("runtime benchmark configuration must be an object")
    _require_exact_keys(configuration, RUNTIME_CONFIGURATION_KEYS, "runtime configuration")
    pairs = _bounded_int(configuration["pairs"], "runtime pairs", minimum=31, maximum=1000)
    _bounded_int(
        configuration["default_pairs"], "runtime default pairs", minimum=31, maximum=1000
    )
    warmups = _bounded_int(
        configuration["warmups_per_runtime_and_surface"],
        "runtime warmups",
        minimum=3,
        maximum=100,
    )
    timeout_seconds = _finite_number(
        configuration["command_timeout_seconds"],
        "runtime command timeout",
        positive=True,
    )
    if timeout_seconds > 3600:
        raise ReleaseError("runtime command timeout is unreasonably large")
    limit = _finite_number(
        configuration["v5_median_regression_limit_seconds"],
        "runtime regression limit",
        positive=True,
    )
    if limit != RUNTIME_REGRESSION_LIMIT_SECONDS:
        raise ReleaseError("runtime median regression limit must be exactly 25ms")
    if configuration["alternating_order"] is not True:
        raise ReleaseError("runtime benchmark must use alternating order")
    if configuration["probe_task"] != RUNTIME_PROBE_TASK:
        raise ReleaseError("runtime benchmark probe task differs")
    if configuration["intentional_probe_differences"] != RUNTIME_INTENTIONAL_PROBE_DIFFERENCES:
        raise ReleaseError("runtime benchmark documented probe differences differ")

    source = value["source"]
    if not isinstance(source, dict):
        raise ReleaseError("runtime benchmark source must be an object")
    _require_exact_keys(
        source,
        {
            "git_head",
            "git_status_sha256",
            "input_sha256_after",
            "input_sha256_before",
            "probe_task_sha256",
            "runner_plan_sha256",
        },
        "runtime benchmark source",
    )
    before_hashes = source["input_sha256_before"]
    after_hashes = source["input_sha256_after"]
    if not isinstance(before_hashes, dict) or not isinstance(after_hashes, dict):
        raise ReleaseError("runtime benchmark input hashes must be objects")
    expected_source_keys = set(RUNTIME_SOURCE_PATHS)
    _require_exact_keys(before_hashes, expected_source_keys, "runtime source before")
    _require_exact_keys(after_hashes, expected_source_keys, "runtime source after")
    if before_hashes != after_hashes:
        raise ReleaseError("runtime benchmark source changed during measurement")
    current_hashes = {
        relative: _stable_source_sha256(relative) for relative in RUNTIME_SOURCE_PATHS
    }
    if before_hashes != current_hashes:
        raise ReleaseError("runtime benchmark receipt is stale for current source inputs")
    expected_probe_hash = _sha256_bytes(RUNTIME_PROBE_TASK.encode("utf-8"))
    if source["probe_task_sha256"] != expected_probe_hash:
        raise ReleaseError("runtime benchmark probe task hash differs")
    if not isinstance(source["runner_plan_sha256"], str) or not SHA256_RE.fullmatch(
        source["runner_plan_sha256"]
    ):
        raise ReleaseError("runtime benchmark runner plan hash is invalid")
    git_head = source["git_head"]
    if git_head is not None and (
        not isinstance(git_head, str) or not re.fullmatch(r"[0-9a-f]{40}", git_head)
    ):
        raise ReleaseError("runtime benchmark git head is invalid")
    git_status_hash = source["git_status_sha256"]
    if git_status_hash is not None and (
        not isinstance(git_status_hash, str) or not SHA256_RE.fullmatch(git_status_hash)
    ):
        raise ReleaseError("runtime benchmark git status hash is invalid")

    surfaces = value["surfaces"]
    if not isinstance(surfaces, dict):
        raise ReleaseError("runtime benchmark surfaces must be an object")
    _require_exact_keys(surfaces, set(RUNTIME_SURFACES), "runtime benchmark surfaces")
    summaries: dict[str, Any] = {}
    recomputed: dict[str, dict[str, bool]] = {}
    for surface in RUNTIME_SURFACES:
        summaries[surface], recomputed[surface] = _validate_runtime_surface(
            surface,
            surfaces[surface],
            pairs=pairs,
            warmups=warmups,
            timeout_seconds=timeout_seconds,
        )

    gates = value["gates"]
    if not isinstance(gates, dict):
        raise ReleaseError("runtime benchmark gates must be an object")
    _require_exact_keys(gates, RUNTIME_GATE_NAMES, "runtime benchmark gates")
    expected_gates = {
        "exit_parity": all(item["exit_parity"] for item in recomputed.values()),
        "no_command_failure": value["command_failures"] == [],
        "probe_semantic_parity_with_documented_exceptions": recomputed["probe"][
            "semantic_parity"
        ],
        "runner_semantic_parity": recomputed["runner"]["semantic_parity"],
        "runner_status_parity": recomputed["runner"]["runner_status_parity"],
        "source_inputs_unchanged_during_run": before_hashes == after_hashes,
        "template_exact": recomputed["template"]["semantic_parity"],
        "v5_probe_median_regression_within_25ms": recomputed["probe"][
            "median_within_limit"
        ],
        "v5_runner_median_regression_within_25ms": recomputed["runner"][
            "median_within_limit"
        ],
        "v5_template_median_regression_within_25ms": recomputed["template"][
            "median_within_limit"
        ],
    }
    if gates != expected_gates or not all(gates.values()):
        raise ReleaseError("runtime benchmark gates do not recompute to all true")
    if value["passed"] is not all(expected_gates.values()):
        raise ReleaseError("runtime benchmark passed value does not recompute")
    return {
        "pairs": pairs,
        "receipt_sha256": _sha256_bytes(raw),
        "schema_version": 1,
        "surfaces": summaries,
        "v5_median_regression_limit_seconds": RUNTIME_REGRESSION_LIMIT_SECONDS,
        "warmups_per_runtime_and_surface": warmups,
    }


def _receipt(
    archive_path: Path,
    members: Sequence[PackageMember],
    provenance: dict[str, Any],
    runtime_benchmark: dict[str, Any],
) -> dict[str, Any]:
    return {
        "archive": {
            "format": "zip-stored",
            "members": [member.receipt_value() for member in members],
            "sha256": _sha256_file(archive_path),
            "size_bytes": archive_path.stat().st_size,
        },
        "package": {
            "integrity": provenance["integrity"],
            "marker_sha256": provenance["marker_sha256"],
            "name": PACKAGE_NAME,
            "release": provenance["release"],
            "sha256": provenance["sha256"],
        },
        "runtime_benchmark": runtime_benchmark,
        "schema": SCHEMA,
    }


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ReleaseError(
            f"{label} keys differ: missing={sorted(expected - actual)} "
            f"extra={sorted(actual - expected)}"
        )


def _validate_runtime_binding_shape(value: Any) -> None:
    if not isinstance(value, dict):
        raise ReleaseError("runtime benchmark binding must be an object")
    _require_exact_keys(
        value,
        {
            "pairs",
            "receipt_sha256",
            "schema_version",
            "surfaces",
            "v5_median_regression_limit_seconds",
            "warmups_per_runtime_and_surface",
        },
        "runtime benchmark binding",
    )
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        raise ReleaseError("runtime benchmark binding schema must be 1")
    _bounded_int(value["pairs"], "runtime binding pairs", minimum=31, maximum=1000)
    _bounded_int(
        value["warmups_per_runtime_and_surface"],
        "runtime binding warmups",
        minimum=3,
        maximum=100,
    )
    if (
        _finite_number(
            value["v5_median_regression_limit_seconds"], "runtime binding limit"
        )
        != RUNTIME_REGRESSION_LIMIT_SECONDS
    ):
        raise ReleaseError("runtime benchmark binding limit must be exactly 25ms")
    if not isinstance(value["receipt_sha256"], str) or not SHA256_RE.fullmatch(
        value["receipt_sha256"]
    ):
        raise ReleaseError("runtime benchmark binding receipt hash is invalid")
    surfaces = value["surfaces"]
    if not isinstance(surfaces, dict):
        raise ReleaseError("runtime benchmark binding surfaces must be an object")
    _require_exact_keys(surfaces, set(RUNTIME_SURFACES), "runtime binding surfaces")
    for surface in RUNTIME_SURFACES:
        summary = surfaces[surface]
        if not isinstance(summary, dict):
            raise ReleaseError(f"runtime binding surface {surface} must be an object")
        _require_exact_keys(
            summary,
            {"combined_p50_seconds", "median_delta_seconds", "v5_p50_seconds"},
            f"runtime binding surface {surface}",
        )
        combined = _finite_number(
            summary["combined_p50_seconds"],
            f"runtime binding surface {surface} combined p50",
            positive=True,
        )
        v5 = _finite_number(
            summary["v5_p50_seconds"],
            f"runtime binding surface {surface} v5 p50",
            positive=True,
        )
        delta = _finite_number(
            summary["median_delta_seconds"],
            f"runtime binding surface {surface} median delta",
        )
        if delta != _rounded_timing(v5 - combined):
            raise ReleaseError(
                f"runtime binding surface {surface} median delta does not recompute"
            )
        if delta > RUNTIME_REGRESSION_LIMIT_SECONDS:
            raise ReleaseError(f"runtime binding surface {surface} exceeds 25ms")


def _validate_receipt_shape(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseError("release receipt must be a JSON object")
    _require_exact_keys(
        value, {"archive", "package", "runtime_benchmark", "schema"}, "receipt"
    )
    if value["schema"] != SCHEMA:
        raise ReleaseError(f"unsupported release receipt schema: {value['schema']!r}")
    archive = value["archive"]
    package = value["package"]
    if not isinstance(archive, dict) or not isinstance(package, dict):
        raise ReleaseError("receipt archive and package fields must be objects")
    _require_exact_keys(
        archive, {"format", "members", "sha256", "size_bytes"}, "archive receipt"
    )
    _require_exact_keys(
        package,
        {"integrity", "marker_sha256", "name", "release", "sha256"},
        "package receipt",
    )
    if archive["format"] != "zip-stored":
        raise ReleaseError("release receipt requires zip-stored format")
    if not isinstance(archive["sha256"], str) or not SHA256_RE.fullmatch(
        archive["sha256"]
    ):
        raise ReleaseError("archive receipt SHA-256 is invalid")
    if type(archive["size_bytes"]) is not int or archive["size_bytes"] <= 0:
        raise ReleaseError("archive receipt size is invalid")
    if package["name"] != PACKAGE_NAME or package["integrity"] is not True:
        raise ReleaseError("package receipt identity or integrity is invalid")
    if not isinstance(package["release"], str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", package["release"]
    ):
        raise ReleaseError("package receipt release is invalid")
    for key in ("marker_sha256", "sha256"):
        if not isinstance(package[key], str) or not SHA256_RE.fullmatch(package[key]):
            raise ReleaseError(f"package receipt {key} is invalid")
    if package["marker_sha256"] != package["sha256"]:
        raise ReleaseError("package receipt marker and canonical hash differ")
    _validate_runtime_binding_shape(value["runtime_benchmark"])
    rows = archive["members"]
    if not isinstance(rows, list) or not rows or len(rows) > MAX_FILES:
        raise ReleaseError("archive receipt members are invalid")
    previous = ""
    seen_casefolded: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ReleaseError(f"archive receipt member {index} must be an object")
        _require_exact_keys(row, {"path", "sha256", "size_bytes"}, "member receipt")
        path = row["path"]
        if not isinstance(path, str):
            raise ReleaseError(f"archive receipt member {index} path is invalid")
        _validate_archive_member_path(path)
        if path <= previous:
            raise ReleaseError("archive receipt members are not strictly sorted")
        previous = path
        folded = path.casefold()
        if folded in seen_casefolded:
            raise ReleaseError(f"case-insensitive archive member collision: {path}")
        seen_casefolded.add(folded)
        if not isinstance(row["sha256"], str) or not SHA256_RE.fullmatch(row["sha256"]):
            raise ReleaseError(f"archive receipt member {index} SHA-256 is invalid")
        if type(row["size_bytes"]) is not int or row["size_bytes"] < 0:
            raise ReleaseError(f"archive receipt member {index} size is invalid")
    return value


def _validate_archive_member_path(name: str) -> None:
    if not name or name.endswith("/") or "\\" in name or "\x00" in name:
        raise ReleaseError(f"unsafe or non-file archive member path: {name!r}")
    path = PurePosixPath(name)
    if path.as_posix() != name:
        raise ReleaseError(f"non-canonical archive member path: {name!r}")
    if path.is_absolute() or len(path.parts) < 2 or path.parts[0] != PACKAGE_NAME:
        raise ReleaseError(f"archive member is outside {PACKAGE_NAME}/: {name!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseError(f"unsafe archive member path: {name!r}")
    relative = PurePosixPath(*path.parts[1:])
    reason = _is_forbidden_relative(relative, is_directory=False)
    if reason:
        raise ReleaseError(f"{name}: {reason}")


def _load_receipt(path: Path) -> dict[str, Any]:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ReleaseError(f"cannot inspect release receipt {path}: {exc}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ReleaseError("release receipt must be a regular, non-symlink file")
    size = path.stat().st_size
    if size > MAX_RECEIPT_BYTES:
        raise ReleaseError("release receipt is unexpectedly large")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReleaseError(f"cannot read release receipt: {exc}") from exc
    value = _validate_receipt_shape(_load_json_bytes(raw, "release receipt"))
    if raw != _canonical_json_bytes(value):
        raise ReleaseError("release receipt is not canonical JSON")
    return value


def _inspect_archive(
    archive_path: Path,
    receipt: dict[str, Any],
    current_members: Sequence[PackageMember],
) -> None:
    try:
        mode = archive_path.lstat().st_mode
    except OSError as exc:
        raise ReleaseError(f"cannot inspect release archive {archive_path}: {exc}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ReleaseError("release archive must be a regular, non-symlink file")
    archive_receipt = receipt["archive"]
    if archive_path.stat().st_size != archive_receipt["size_bytes"]:
        raise ReleaseError("release archive size does not match the receipt")
    if _sha256_file(archive_path) != archive_receipt["sha256"]:
        raise ReleaseError("release archive SHA-256 does not match the receipt")
    expected_rows = [member.receipt_value() for member in current_members]
    if archive_receipt["members"] != expected_rows:
        raise ReleaseError("receipt members do not match the current package")
    canonical_buffer = io.BytesIO()
    _write_zip(canonical_buffer, current_members)
    canonical_archive = canonical_buffer.getvalue()
    if archive_receipt["size_bytes"] != len(canonical_archive):
        raise ReleaseError("release archive is not the canonical deterministic ZIP")
    if archive_receipt["sha256"] != _sha256_bytes(canonical_archive):
        raise ReleaseError("release archive hash is not the canonical deterministic ZIP")
    try:
        if archive_path.read_bytes() != canonical_archive:
            raise ReleaseError("release archive bytes are not canonical")
    except OSError as exc:
        raise ReleaseError(f"cannot read release archive: {exc}") from exc
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            if archive.comment != b"":
                raise ReleaseError("release archive comment must be empty")
            infos = archive.infolist()
            names = [info.filename for info in infos]
            expected_names = [member.archive_path for member in current_members]
            if names != expected_names:
                raise ReleaseError("archive members are not the exact sorted package layout")
            if len(set(names)) != len(names):
                raise ReleaseError("release archive contains duplicate members")
            for info, member in zip(infos, current_members):
                _validate_archive_member_path(info.filename)
                if info.compress_type != zipfile.ZIP_STORED:
                    raise ReleaseError(f"archive member is compressed: {info.filename}")
                if info.date_time != FIXED_ZIP_TIME:
                    raise ReleaseError(f"archive member timestamp is not fixed: {info.filename}")
                if info.create_system != 3:
                    raise ReleaseError(f"archive member platform metadata differs: {info.filename}")
                if (info.external_attr >> 16) != REGULAR_MODE:
                    raise ReleaseError(f"archive member mode differs: {info.filename}")
                if info.extra or info.comment or info.flag_bits & 0x1:
                    raise ReleaseError(f"archive member has forbidden metadata: {info.filename}")
                if info.file_size != len(member.data) or info.compress_size != len(member.data):
                    raise ReleaseError(f"archive member size differs: {info.filename}")
                if info.file_size > MAX_PACKAGE_BYTES:
                    raise ReleaseError(f"archive member is unexpectedly large: {info.filename}")
                data = archive.read(info)
                if data != member.data or _sha256_bytes(data) != member.sha256:
                    raise ReleaseError(f"archive member content differs: {info.filename}")
            bad = archive.testzip()
            if bad is not None:
                raise ReleaseError(f"archive CRC check failed: {bad}")
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, ReleaseError):
            raise
        raise ReleaseError(f"invalid release archive: {exc}") from exc


def _verify_source_inputs(
    package: Path,
    receipt_path: Path,
    runtime_receipt_path: Path = DEFAULT_RUNTIME_RECEIPT,
) -> tuple[dict[str, Any], tuple[PackageMember, ...]]:
    receipt = _load_receipt(receipt_path)
    runtime_benchmark = _load_runtime_benchmark_receipt(runtime_receipt_path)
    members, provenance = _stable_package_snapshot(package)
    expected_package = {
        "integrity": provenance["integrity"],
        "marker_sha256": provenance["marker_sha256"],
        "name": PACKAGE_NAME,
        "release": provenance["release"],
        "sha256": provenance["sha256"],
    }
    if receipt["package"] != expected_package:
        raise ReleaseError("receipt provenance does not match the current package")
    if receipt["runtime_benchmark"] != runtime_benchmark:
        raise ReleaseError(
            "release receipt runtime binding does not match the current benchmark receipt"
        )
    expected_members = [member.receipt_value() for member in members]
    if receipt["archive"]["members"] != expected_members:
        raise ReleaseError("release receipt members do not match the current package")
    canonical_buffer = io.BytesIO()
    _write_zip(canonical_buffer, members)
    canonical_archive = canonical_buffer.getvalue()
    if receipt["archive"]["size_bytes"] != len(canonical_archive):
        raise ReleaseError("release receipt archive size does not recompute from source")
    if receipt["archive"]["sha256"] != _sha256_bytes(canonical_archive):
        raise ReleaseError("release receipt archive hash does not recompute from source")
    return receipt, members


def verify_source(
    package: Path,
    receipt_path: Path,
    runtime_receipt_path: Path = DEFAULT_RUNTIME_RECEIPT,
) -> dict[str, Any]:
    receipt, _ = _verify_source_inputs(
        package, receipt_path, runtime_receipt_path
    )
    return receipt


def verify_release(
    package: Path,
    archive: Path,
    receipt_path: Path,
    runtime_receipt_path: Path = DEFAULT_RUNTIME_RECEIPT,
) -> dict[str, Any]:
    receipt, members = _verify_source_inputs(
        package, receipt_path, runtime_receipt_path
    )
    _inspect_archive(archive, receipt, members)
    return receipt


def _prepare_target(path: Path, label: str) -> Path:
    target = path.expanduser().absolute()
    if target.name in {"", ".", ".."}:
        raise ReleaseError(f"invalid {label} path: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReleaseError(f"cannot create {label} parent: {exc}") from exc
    if not target.parent.is_dir():
        raise ReleaseError(f"{label} parent is not a directory: {target.parent}")
    if os.path.lexists(target):
        raise ReleaseError(f"refusing to overwrite existing {label}: {target}")
    return target


def _temp_path(parent: Path, prefix: str) -> tuple[int, Path]:
    descriptor, raw = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=parent)
    return descriptor, Path(raw)


def _publish_link(source: Path, target: Path, label: str) -> None:
    try:
        os.link(source, target, follow_symlinks=False)
    except FileExistsError as exc:
        raise ReleaseError(f"refusing to overwrite existing {label}: {target}") from exc
    except OSError as exc:
        raise ReleaseError(f"cannot atomically publish {label}: {exc}") from exc


def build_release(
    package: Path,
    output: Path,
    receipt_path: Path,
    runtime_receipt_path: Path = DEFAULT_RUNTIME_RECEIPT,
) -> dict[str, Any]:
    root = _validate_package_root(package)
    runtime_benchmark = _load_runtime_benchmark_receipt(runtime_receipt_path)
    archive_target = _prepare_target(output, "release archive")
    receipt_target = _prepare_target(receipt_path, "release receipt")
    if archive_target == receipt_target:
        raise ReleaseError("release archive and receipt paths must differ")
    members, provenance = _stable_package_snapshot(root)
    archive_fd, archive_temp = _temp_path(archive_target.parent, ".endurant-archive-")
    receipt_fd = -1
    receipt_temp: Path | None = None
    archive_published = False
    receipt_published = False
    completed = False
    try:
        with os.fdopen(archive_fd, "w+b") as handle:
            _write_zip(handle, members)
            handle.flush()
            os.fsync(handle.fileno())
        receipt = _receipt(archive_temp, members, provenance, runtime_benchmark)
        receipt_bytes = _canonical_json_bytes(receipt)
        receipt_fd, receipt_temp = _temp_path(
            receipt_target.parent, ".endurant-receipt-"
        )
        with os.fdopen(receipt_fd, "wb") as handle:
            receipt_fd = -1
            handle.write(receipt_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        verify_release(root, archive_temp, receipt_temp, runtime_receipt_path)
        _publish_link(archive_temp, archive_target, "release archive")
        archive_published = True
        _publish_link(receipt_temp, receipt_target, "release receipt")
        receipt_published = True
        verify_release(root, archive_target, receipt_target, runtime_receipt_path)
        completed = True
        return receipt
    finally:
        if receipt_fd >= 0:
            os.close(receipt_fd)
        if not completed:
            published = (
                (receipt_published, receipt_target, receipt_temp),
                (archive_published, archive_target, archive_temp),
            )
            for was_published, target, temporary in published:
                if not was_published or temporary is None:
                    continue
                try:
                    if target.samefile(temporary):
                        target.unlink()
                except (FileNotFoundError, OSError):
                    pass
        for temporary in (archive_temp, receipt_temp):
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="create archive and deterministic receipt")
    build.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    build.add_argument("--output", type=Path, default=DEFAULT_ARCHIVE)
    build.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    build.add_argument(
        "--runtime-receipt", type=Path, default=DEFAULT_RUNTIME_RECEIPT
    )
    verify = subparsers.add_parser("verify", help="verify archive, receipt, and package")
    verify.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    verify.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    verify.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    verify.add_argument(
        "--runtime-receipt", type=Path, default=DEFAULT_RUNTIME_RECEIPT
    )
    verify_source_parser = subparsers.add_parser(
        "verify-source",
        help="verify receipt and current sources without reading a release archive",
    )
    verify_source_parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    verify_source_parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    verify_source_parser.add_argument(
        "--runtime-receipt", type=Path, default=DEFAULT_RUNTIME_RECEIPT
    )
    return parser


def _success_summary(command: str, receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "archive_sha256": receipt["archive"]["sha256"],
        "archive_size_bytes": receipt["archive"]["size_bytes"],
        "command": command,
        "members": len(receipt["archive"]["members"]),
        "package_sha256": receipt["package"]["sha256"],
        "release": receipt["package"]["release"],
        "runtime_receipt_sha256": receipt["runtime_benchmark"]["receipt_sha256"],
        "status": "passed",
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build":
            receipt = build_release(
                args.package, args.output, args.receipt, args.runtime_receipt
            )
        elif args.command == "verify":
            receipt = verify_release(
                args.package, args.archive, args.receipt, args.runtime_receipt
            )
        else:
            receipt = verify_source(
                args.package, args.receipt, args.runtime_receipt
            )
        print(json.dumps(_success_summary(args.command, receipt), sort_keys=True))
        return 0
    except (ReleaseError, OSError) as exc:
        print(f"endurant-release: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
