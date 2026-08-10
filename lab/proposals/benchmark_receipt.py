"""Synthetic benchmark receipt and comparator prototype."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


BODY_KEYS = {
    "schema_version",
    "benchmark_id",
    "phase",
    "source",
    "workload",
    "correctness",
    "metric_schema",
    "metrics",
    "primary_metric",
    "minimum_improvement_fraction",
}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_manifest(root: Path, paths: list[str]) -> dict[str, str]:
    return {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in paths
    }


def build_receipt(
    profile: dict[str, Any],
    event: dict[str, Any],
    root: Path,
    phase: str,
) -> dict[str, Any]:
    if phase not in {"baseline", "final"}:
        raise ValueError("benchmark phase must be baseline or final")
    if not isinstance(event, dict):
        raise ValueError("benchmark event must be an object")
    try:
        metric_schema = profile["metric_schema"]
        correctness_keys = profile["correctness_keys"]
    except (KeyError, TypeError) as error:
        raise ValueError("benchmark profile is incomplete") from error
    if not isinstance(metric_schema, dict) or not metric_schema:
        raise ValueError("benchmark metric schema must be a non-empty object")
    metrics = event.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("benchmark event metrics must be an object")
    metric_keys = set(metric_schema)
    if set(metrics) != metric_keys:
        raise ValueError("benchmark metrics do not match profile schema")
    if (
        not isinstance(correctness_keys, list)
        or not correctness_keys
        or not all(isinstance(key, str) and key for key in correctness_keys)
    ):
        raise ValueError("benchmark correctness keys must be a non-empty string array")
    if any(key not in event for key in correctness_keys):
        raise ValueError("benchmark event is missing correctness data")
    correctness = {key: event[key] for key in correctness_keys}
    body = {
        "schema_version": 1,
        "benchmark_id": profile["benchmark_id"],
        "phase": phase,
        "source": file_manifest(root, profile["source_files"]),
        "workload": {
            "argv": profile["argv"],
            "cwd": profile.get("cwd", "."),
            "env": profile.get("env", {}),
            "files": file_manifest(root, profile["workload_files"]),
        },
        "correctness": correctness,
        "metric_schema": metric_schema,
        "metrics": metrics,
        "primary_metric": profile["primary_metric"],
        "minimum_improvement_fraction": profile["minimum_improvement_fraction"],
    }
    return {"body": body, "receipt_sha256": canonical_sha256(body)}


def _validated_body(receipt: Any) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise ValueError("benchmark receipt must be an object")
    body = receipt.get("body")
    if not isinstance(body, dict) or set(body) != BODY_KEYS:
        raise ValueError("benchmark receipt body schema is invalid")
    if body.get("schema_version") != 1:
        raise ValueError("unsupported benchmark receipt schema")
    if receipt.get("receipt_sha256") != canonical_sha256(body):
        raise ValueError("benchmark receipt hash mismatch")
    if body.get("phase") not in {"baseline", "final"}:
        raise ValueError("benchmark receipt phase is invalid")
    for key in ("source", "workload", "correctness", "metric_schema", "metrics"):
        if not isinstance(body.get(key), dict):
            raise ValueError(f"benchmark receipt {key} must be an object")
    primary = body.get("primary_metric")
    schema = body["metric_schema"]
    metrics = body["metrics"]
    if not isinstance(primary, str) or primary not in schema or set(metrics) != set(schema):
        raise ValueError("benchmark receipt metrics do not match schema")
    metric_contract = schema.get(primary)
    if (
        not isinstance(metric_contract, dict)
        or metric_contract.get("direction") not in {"lower", "higher"}
    ):
        raise ValueError("benchmark primary metric direction is invalid")
    try:
        metric_value = float(metrics[primary])
        threshold = float(body["minimum_improvement_fraction"])
    except (TypeError, ValueError) as error:
        raise ValueError("benchmark metric or threshold is invalid") from error
    if metric_value <= 0 or not 0 <= threshold <= 1:
        raise ValueError("benchmark metric or threshold is out of range")
    return body


def compare(
    baseline: dict[str, Any],
    final: dict[str, Any],
    *,
    observed_baseline_source: dict[str, str],
    observed_final_source: dict[str, str],
) -> dict[str, Any]:
    before = _validated_body(baseline)
    after = _validated_body(final)
    if before["phase"] != "baseline" or after["phase"] != "final":
        raise ValueError("benchmark phases are invalid")
    if before["source"] != observed_baseline_source or after["source"] != observed_final_source:
        raise ValueError("benchmark source observation mismatch")
    for key in (
        "benchmark_id",
        "workload",
        "correctness",
        "metric_schema",
        "primary_metric",
        "minimum_improvement_fraction",
    ):
        if before[key] != after[key]:
            raise ValueError(f"benchmark {key} changed")
    expected_metrics = set(before["metric_schema"])
    if set(before["metrics"]) != expected_metrics or set(after["metrics"]) != expected_metrics:
        raise ValueError("benchmark metric keys changed")
    metric = before["primary_metric"]
    baseline_value = float(before["metrics"][metric])
    final_value = float(after["metrics"][metric])
    direction = before["metric_schema"][metric]["direction"]
    improvement = (
        (baseline_value - final_value) / baseline_value
        if direction == "lower"
        else (final_value - baseline_value) / baseline_value
    )
    threshold = float(after["minimum_improvement_fraction"])
    return {
        "passed": improvement >= threshold,
        "primary_metric": metric,
        "baseline": baseline_value,
        "final": final_value,
        "improvement_fraction": round(improvement, 9),
        "threshold_fraction": threshold,
    }


def optional_compare(
    enabled: bool,
    baseline: dict[str, Any] | None = None,
    final: dict[str, Any] | None = None,
    *,
    observed_baseline_source: dict[str, str] | None = None,
    observed_final_source: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Keep ordinary tasks on a measured constant-time disabled path."""
    if not enabled:
        return None
    if (
        baseline is None
        or final is None
        or observed_baseline_source is None
        or observed_final_source is None
    ):
        raise ValueError("enabled benchmark comparison requires both receipts and sources")
    return compare(
        baseline,
        final,
        observed_baseline_source=observed_baseline_source,
        observed_final_source=observed_final_source,
    )
