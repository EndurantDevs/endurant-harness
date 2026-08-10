#!/usr/bin/env python3
"""Externally grade one materialized fixture run."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from eval_lib import (
    FIXTURES,
    changed_paths,
    file_manifest,
    git_state,
    ignored_relative,
    latest_passing_event,
    load_events,
    manifest_delta,
    read_json,
    run_process,
    tree_manifest,
)


def allowed_path(path: str, allowed: list[str]) -> bool:
    return any(path == item or path.startswith(item.rstrip("/") + "/") for item in allowed)


def protected_path(path: str, prefixes: list[str]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def relevant_symlinks(root: Path) -> list[str]:
    result: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink() and not ignored_relative(relative):
            result.append(relative.as_posix())
    return sorted(result)


def event_timestamp_ns(event: dict[str, Any] | None) -> int | None:
    if not event:
        return None
    value = event.get("timestamp_ns")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def events_for_run(
    events: list[dict[str, Any]], actor: str, run_id: str
) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if event.get("actor") == actor and event.get("run_id") == run_id
    ]


def load_observed_agent_events(path: Path) -> tuple[list[dict[str, Any]], bool]:
    events: list[dict[str, Any]] = []
    previous_observed = -1
    if not path.is_file():
        return events, False
    for expected_sequence, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines()
    ):
        try:
            wrapped = json.loads(line)
        except json.JSONDecodeError:
            return [], False
        if not isinstance(wrapped, dict) or wrapped.get("sequence") != expected_sequence:
            return [], False
        observed = wrapped.get("observed_monotonic_ns")
        event = wrapped.get("event")
        if (
            not isinstance(observed, int)
            or isinstance(observed, bool)
            or observed < previous_observed
            or not isinstance(event, dict)
        ):
            return [], False
        copied = dict(event)
        copied["_runner_observed_monotonic_ns"] = observed
        events.append(copied)
        previous_observed = observed
    return events, True


def observed_before(event: dict[str, Any] | None, boundary_ns: object) -> bool:
    observed = event.get("_runner_observed_monotonic_ns") if event else None
    return bool(
        isinstance(observed, int)
        and not isinstance(observed, bool)
        and isinstance(boundary_ns, int)
        and not isinstance(boundary_ns, bool)
        and observed < boundary_ns
    )


def observed_at_or_after(event: dict[str, Any] | None, boundary_ns: object) -> bool:
    observed = event.get("_runner_observed_monotonic_ns") if event else None
    return bool(
        isinstance(observed, int)
        and not isinstance(observed, bool)
        and isinstance(boundary_ns, int)
        and not isinstance(boundary_ns, bool)
        and observed >= boundary_ns
    )


def event_before(event: dict[str, Any] | None, boundary_ns: object) -> bool:
    timestamp = event_timestamp_ns(event)
    return bool(
        timestamp is not None
        and isinstance(boundary_ns, int)
        and not isinstance(boundary_ns, bool)
        and timestamp < boundary_ns
    )


def event_at_or_after(event: dict[str, Any] | None, boundary_ns: object) -> bool:
    timestamp = event_timestamp_ns(event)
    return bool(
        timestamp is not None
        and isinstance(boundary_ns, int)
        and not isinstance(boundary_ns, bool)
        and timestamp >= boundary_ns
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--capture", required=True)
    args = parser.parse_args()

    fixture_root = FIXTURES / args.fixture
    config = read_json(fixture_root / "fixture.json")
    workspace = Path(args.workspace).resolve()
    capture = Path(args.capture).resolve()
    before = read_json(capture / "baseline-manifest.json")
    metadata = read_json(capture / "metadata.json")
    metrics_path = capture / "agent-metrics.json"
    agent_metrics = read_json(metrics_path) if metrics_path.is_file() else {}
    event_run_id = str(metadata.get("event_run_id", ""))
    agent_started_timestamp_ns = metadata.get("agent_started_timestamp_ns")
    first_edit_timestamp_ns = agent_metrics.get("first_edit_timestamp_ns")
    first_edit_monotonic_ns = agent_metrics.get("first_edit_monotonic_ns")

    after = file_manifest(workspace)
    delta = manifest_delta(before, after)
    changed = changed_paths(delta)
    allowed = list(config.get("allowed_changes", []))
    protected = list(config.get("protected_prefixes", []))
    unexpected = [path for path in changed if not allowed_path(path, allowed)]
    protected_changed = [path for path in changed if protected_path(path, protected)]
    symlink_paths = relevant_symlinks(workspace)

    subject_root = workspace / ".agents" / "skills" / "endurant-harness"
    expected_subject_tree = metadata.get("subject_tree_manifest")
    subject_tree_unchanged = bool(
        isinstance(expected_subject_tree, dict)
        and tree_manifest(subject_root) == expected_subject_tree
    )
    current_git_state = git_state(workspace)
    git_state_unchanged = current_git_state == metadata.get("baseline_git_state")

    required_changed_prefixes = list(config.get("required_changed_prefixes", []))
    missing_changed_prefixes = [
        prefix
        for prefix in required_changed_prefixes
        if not any(path.startswith(prefix) for path in changed)
    ]
    required_changed_paths = list(config.get("required_changed_paths", []))
    missing_changed_paths = [path for path in required_changed_paths if path not in changed]

    hidden = run_process(
        [sys.executable, str(fixture_root / "hidden_grade.py"), str(workspace)],
        fixture_root,
    )
    hidden_payload: dict[str, Any]
    try:
        hidden_payload = json.loads(hidden.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        hidden_payload = {"passed": False, "parse_error": hidden.stdout[-1000:]}

    observed_events, agent_event_log_valid = load_observed_agent_events(
        capture / "agent-events-observed.jsonl"
    )
    agent_events = events_for_run(observed_events, "agent", event_run_id)
    agent_event_log_tampered = agent_metrics.get("agent_event_log_tampered") is True
    forbidden_gates = list(config.get("forbidden_agent_gates", []))
    forbidden_observed = sorted(
        {
            str(event.get("gate"))
            for event in agent_events
            if event.get("gate") in forbidden_gates
        }
    )

    grader_event_log = capture / "grader-events.jsonl"
    grader_environment = {
        **os.environ,
        "EVAL_EVENT_LOG": str(grader_event_log),
        "EVAL_RUN_ID": event_run_id,
        "EVAL_ACTOR": "grader",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    acceptance = run_process(
        [sys.executable, "scripts/verify.py", "ci-preflight"],
        workspace,
        env=grader_environment,
    )
    (capture / "grader-ci.stdout").write_text(acceptance.stdout, encoding="utf-8")
    (capture / "grader-ci.stderr").write_text(acceptance.stderr, encoding="utf-8")

    final_benchmark = None
    if config.get("kind") == "performance":
        final_benchmark = run_process(
            [sys.executable, "scripts/verify.py", "synthetic"],
            workspace,
            env=grader_environment,
            timeout=120,
        )
        (capture / "grader-benchmark.stdout").write_text(
            final_benchmark.stdout, encoding="utf-8"
        )
        (capture / "grader-benchmark.stderr").write_text(
            final_benchmark.stderr, encoding="utf-8"
        )

    grader_events = events_for_run(load_events(grader_event_log), "grader", event_run_id)
    final_ci_event = latest_passing_event(grader_events, "ci-preflight", "grader")
    final_verification_sha = (
        str(final_ci_event.get("verification_sha256")) if final_ci_event else ""
    )
    required_gates = list(config.get("required_agent_gates", []))
    missing_gates = [
        gate
        for gate in required_gates
        if not any(
            event.get("gate") == gate
            and event.get("passed") is True
            and event.get("verification_sha256") == final_verification_sha
            and observed_at_or_after(event, first_edit_monotonic_ns)
            for event in agent_events
        )
    ]

    performance: dict[str, Any] = {"required": config.get("kind") == "performance"}
    if performance["required"]:
        orchestrator_events = events_for_run(
            load_events(capture / "orchestrator-events.jsonl"),
            "orchestrator",
            event_run_id,
        )
        baseline_event = latest_passing_event(orchestrator_events, "synthetic", "orchestrator")
        final_event = latest_passing_event(grader_events, "synthetic", "grader")
        baseline_before_agent = event_before(baseline_event, agent_started_timestamp_ns)
        grader_final_after_edit = event_at_or_after(final_event, first_edit_timestamp_ns)
        baseline_p95 = float(baseline_event.get("p95_seconds", 0)) if baseline_event else 0.0
        final_p95 = float(final_event.get("p95_seconds", 0)) if final_event else 0.0
        improvement = (baseline_p95 - final_p95) / baseline_p95 if baseline_p95 else 0.0
        threshold = float(config.get("minimum_p95_improvement_fraction", 0.0))
        agent_synthetic = [event for event in agent_events if event.get("gate") == "synthetic"]
        baseline_source = str(baseline_event.get("source_sha256")) if baseline_event else ""
        baseline_verification = (
            str(baseline_event.get("verification_sha256")) if baseline_event else ""
        )
        final_source = str(final_event.get("source_sha256")) if final_event else ""
        agent_baseline_seen = any(
            event.get("passed") is True
            and event.get("source_sha256") == baseline_source
            and event.get("verification_sha256") == baseline_verification
            and observed_before(event, first_edit_monotonic_ns)
            for event in agent_synthetic
        )
        agent_final_seen = any(
            event.get("passed") is True
            and event.get("source_sha256") == final_source
            and event.get("verification_sha256") == final_verification_sha
            and observed_at_or_after(event, first_edit_monotonic_ns)
            for event in agent_synthetic
        )
        performance = {
            "required": True,
            "baseline_p95_seconds": baseline_p95,
            "final_p95_seconds": final_p95,
            "improvement_fraction": round(improvement, 6),
            "threshold_fraction": threshold,
            "threshold_passed": bool(
                final_benchmark is not None
                and final_benchmark.returncode == 0
                and improvement >= threshold
            ),
            "baseline_before_agent": baseline_before_agent,
            "grader_final_after_edit": grader_final_after_edit,
            "agent_baseline_seen": agent_baseline_seen,
            "agent_final_seen": agent_final_seen,
        }

    result = {
        "fixture": args.fixture,
        "passed": False,
        "functional": hidden_payload,
        "local_ci_preflight_passed": acceptance.returncode == 0,
        "delta": delta,
        "changed_paths": changed,
        "unexpected_changed_paths": unexpected,
        "protected_changed_paths": protected_changed,
        "symlink_paths": symlink_paths,
        "subject_tree_unchanged": subject_tree_unchanged,
        "git_state_unchanged": git_state_unchanged,
        "agent_event_log_valid": agent_event_log_valid,
        "agent_event_log_tampered": agent_event_log_tampered,
        "current_git_state": current_git_state,
        "missing_required_changed_prefixes": missing_changed_prefixes,
        "missing_required_changed_paths": missing_changed_paths,
        "agent_gate_counts": {
            gate: sum(1 for event in agent_events if event.get("gate") == gate)
            for gate in sorted({str(event.get("gate")) for event in agent_events})
        },
        "missing_required_agent_gates": missing_gates,
        "forbidden_agent_gates_observed": forbidden_observed,
        "final_verification_sha256": final_verification_sha,
        "performance": performance,
    }
    result["passed"] = bool(
        hidden_payload.get("passed") is True
        and acceptance.returncode == 0
        and not unexpected
        and not protected_changed
        and not symlink_paths
        and subject_tree_unchanged
        and git_state_unchanged
        and agent_event_log_valid
        and not agent_event_log_tampered
        and not missing_changed_prefixes
        and not missing_changed_paths
        and not missing_gates
        and not forbidden_observed
        and (not performance["required"] or performance.get("threshold_passed") is True)
        and (not performance["required"] or performance.get("baseline_before_agent") is True)
        and (not performance["required"] or performance.get("grader_final_after_edit") is True)
        and (not performance["required"] or performance.get("agent_baseline_seen") is True)
        and (not performance["required"] or performance.get("agent_final_seen") is True)
    )
    output = capture / "grade.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
