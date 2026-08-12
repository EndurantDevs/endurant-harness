#!/usr/bin/env python3
"""Run a hash-frozen, resumable Harness promotion campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import signal
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from eval_lib import (
    ARTIFACTS,
    FIXTURES,
    canonical_json_bytes,
    canonical_sha256,
    read_json,
    sha256_file,
    tree_manifest,
    write_json_atomic,
)


ROOT = Path(__file__).resolve().parents[1]
RUN_AGENT = Path(__file__).with_name("run_agent.py")
GRADE_RUN = Path(__file__).with_name("grade_run.py")
EVAL_LIB = Path(__file__).with_name("eval_lib.py")
PROMOTION_RUNNER = Path(__file__).resolve()
ALLOWED_METRICS = {"duration_seconds", "uncached_input_tokens"}
PHASES = ("aa", "development", "confirmation", "audit")
CAPTURE_FILES = (
    "agent-events-observed.jsonl",
    "codex-observed.jsonl",
    "codex.jsonl",
    "grade.json",
    "grader-ci.stderr",
    "grader-ci.stdout",
    "metadata.json",
    "summary.json",
)
LINEAGE_ATTESTATION_KEYS = {
    "attested_by",
    "candidate_sha256",
    "causal_minimum",
    "frozen_sha256",
    "lineage_kind",
    "rationale",
    "schema_version",
    "tested_removals",
}


class CampaignError(ValueError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CampaignError(f"expected JSON object: {path}")
    return value


def resolve_inside(root: Path, value: str, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise CampaignError(f"{label} is invalid")
    root = root.resolve()
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CampaignError(f"{label} escapes repository: {value}") from exc
    return path


def validate_plan(path: Path) -> dict[str, Any]:
    plan = load_object(path)
    required = {
        "ab_pairs",
        "aa_pairs",
        "audit_fixture_sha256",
        "candidate_path",
        "fixtures",
        "id",
        "materiality_fraction",
        "max_aa_bias_fraction",
        "mining_trace_path",
        "model",
        "parent_revision",
        "primary_cost_metric",
        "proposal",
        "reasoning_effort",
        "schema_version",
        "timeout_seconds",
    }
    if set(plan) != required:
        raise CampaignError(f"plan keys must be exactly {sorted(required)}")
    if plan["schema_version"] != 1:
        raise CampaignError("schema_version must be 1")
    if not isinstance(plan["id"], str) or not plan["id"].replace("-", "").isalnum():
        raise CampaignError("id must contain only letters, digits, and hyphens")
    for field in ("aa_pairs", "ab_pairs"):
        if not isinstance(plan[field], int) or isinstance(plan[field], bool) or plan[field] < 5:
            raise CampaignError(f"{field} must be at least 5")
    if (
        not isinstance(plan["timeout_seconds"], int)
        or isinstance(plan["timeout_seconds"], bool)
        or not 30 <= plan["timeout_seconds"] <= 3600
    ):
        raise CampaignError("timeout_seconds must be 30..3600")
    for field in ("materiality_fraction", "max_aa_bias_fraction"):
        value = plan[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise CampaignError(f"{field} must be a finite fraction")
    if plan["primary_cost_metric"] not in ALLOWED_METRICS:
        raise CampaignError(f"primary_cost_metric must be one of {sorted(ALLOWED_METRICS)}")
    if not isinstance(plan["fixtures"], list) or not plan["fixtures"] or not all(
        isinstance(item, str) and item for item in plan["fixtures"]
    ):
        raise CampaignError("fixtures must be non-empty strings")
    if len(plan["fixtures"]) != len(set(plan["fixtures"])):
        raise CampaignError("fixtures must be unique")
    audit_sha256 = plan["audit_fixture_sha256"]
    if (
        not isinstance(audit_sha256, str)
        or len(audit_sha256) != 64
        or any(character not in "0123456789abcdef" for character in audit_sha256)
    ):
        raise CampaignError("audit_fixture_sha256 must be a lowercase SHA-256")
    proposal = plan["proposal"]
    proposal_keys = {
        "editable_surface",
        "expected_effect",
        "mechanism",
        "origin",
        "proposer_model",
        "proposer_reasoning_effort",
        "protected_behaviors",
        "rollback",
    }
    if not isinstance(proposal, dict) or set(proposal) != proposal_keys:
        raise CampaignError(f"proposal keys must be exactly {sorted(proposal_keys)}")
    for field in ("mechanism", "expected_effect", "origin", "rollback"):
        if not isinstance(proposal[field], str) or not proposal[field].strip():
            raise CampaignError(f"proposal.{field} must be non-empty")
    proposer_fields = ("proposer_model", "proposer_reasoning_effort")
    if proposal["origin"] == "human-authored":
        if any(proposal[field] is not None for field in proposer_fields):
            raise CampaignError("human-authored proposals must not name a proposer model")
    elif not all(
        isinstance(proposal[field], str) and proposal[field].strip()
        for field in proposer_fields
    ):
        raise CampaignError("agent-authored proposals require proposer model and effort")
    for field in ("editable_surface", "protected_behaviors"):
        if not isinstance(proposal[field], list) or not proposal[field] or not all(
            isinstance(item, str) and item for item in proposal[field]
        ):
            raise CampaignError(f"proposal.{field} must be non-empty strings")
    return plan


def git_output(argv: list[str]) -> bytes:
    completed = subprocess.run(
        ["git", *argv],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if completed.returncode != 0:
        raise CampaignError(completed.stderr.decode("utf-8", errors="replace").strip())
    return completed.stdout


def full_revision(value: str) -> str:
    revision = git_output(["rev-parse", "--verify", f"{value}^{{commit}}"]).decode().strip()
    if len(revision) != 40:
        raise CampaignError("parent_revision did not resolve to a full commit")
    return revision


def materialize_git_package(revision: str, destination: Path) -> None:
    prefix = "endurant-harness/"
    raw = git_output(["ls-tree", "-r", "-z", revision, "--", prefix])
    entries = [item for item in raw.split(b"\0") if item]
    if not entries:
        raise CampaignError("parent revision has no endurant-harness package")
    destination.mkdir(parents=True, exist_ok=False)
    for entry in entries:
        metadata, raw_path = entry.split(b"\t", 1)
        mode = metadata.split(b" ", 1)[0]
        repository_path = raw_path.decode("utf-8")
        if not repository_path.startswith(prefix):
            raise CampaignError(f"unexpected parent path: {repository_path}")
        relative = repository_path[len(prefix) :]
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git_output(["show", f"{revision}:{repository_path}"]))
        target.chmod(0o755 if mode == b"100755" else 0o644)


def copy_regular_tree(source: Path, destination: Path) -> None:
    manifest = tree_manifest(source)
    unsafe = [path for path, entry in manifest.items() if entry["type"] not in {"directory", "file"}]
    if unsafe:
        raise CampaignError(f"tree contains unsupported paths: {unsafe}")
    shutil.copytree(source, destination)


def tree_sha256(path: Path) -> str:
    return canonical_sha256(tree_manifest(path))


def sealed_audit_sha256(path: Path) -> str:
    if path.is_symlink() or path.stat().st_mode & 0o777:
        raise CampaignError("audit fixture is not sealed")
    path.chmod(0o700)
    try:
        return tree_sha256(path)
    finally:
        path.chmod(0)


def file_differences(parent: Path, candidate: Path) -> list[str]:
    before = tree_manifest(parent)
    after = tree_manifest(candidate)
    return sorted(
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
        and (before.get(path, after.get(path, {})).get("type") != "directory")
    )


def allowed_path(path: str, allowed: list[str]) -> bool:
    return any(path == item.rstrip("/") or path.startswith(item.rstrip("/") + "/") for item in allowed)


def load_traces(path: Path) -> list[dict[str, Any]]:
    payload = load_object(path)
    if payload.get("schema_version") != 1 or not isinstance(payload.get("traces"), list):
        raise CampaignError("trace corpus requires schema_version 1 and traces")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = {"agent_role", "id", "mechanism", "outcome", "terminal_failure"}
    for index, trace in enumerate(payload["traces"]):
        if not isinstance(trace, dict) or set(trace) != required:
            raise CampaignError(f"trace {index} has invalid keys")
        if trace["outcome"] not in {"failed", "success"}:
            raise CampaignError(f"trace {index} has invalid outcome")
        if not all(isinstance(trace[field], str) and trace[field] for field in required):
            raise CampaignError(f"trace {index} has empty fields")
        if trace["id"] in seen:
            raise CampaignError(f"duplicate trace id: {trace['id']}")
        seen.add(trace["id"])
        result.append(trace)
    return result


def mine_traces(traces: Iterable[dict[str, Any]], min_support: int) -> dict[str, Any]:
    failures: dict[tuple[str, str, str], list[str]] = {}
    successes: list[str] = []
    for trace in traces:
        if trace["outcome"] == "success":
            successes.append(trace["id"])
            continue
        key = (trace["terminal_failure"], trace["agent_role"], trace["mechanism"])
        failures.setdefault(key, []).append(trace["id"])
    clusters = [
        {
            "agent_role": key[1],
            "mechanism": key[2],
            "support": len(ids),
            "terminal_failure": key[0],
            "trace_ids": sorted(ids),
        }
        for key, ids in sorted(failures.items())
        if len(ids) >= min_support
    ]
    return {
        "clusters": clusters,
        "protected_success_ids": sorted(successes),
        "trace_count": sum(len(ids) for ids in failures.values()) + len(successes),
    }


def fixture_path(value: str) -> Path:
    path = resolve_inside(FIXTURES, value, "fixture")
    required_files = ("fixture.json", "hidden_grade.py", "task.txt")
    missing = [name for name in required_files if not (path / name).is_file()]
    if missing or not (path / "template").is_dir():
        raise CampaignError(f"fixture {value!r} is incomplete: {missing}")
    return path


def freeze(
    plan_path: Path,
    campaign_dir: Path,
    *,
    audit_fixture: Path | None = None,
    codex_version: str | None = None,
) -> dict[str, Any]:
    plan = validate_plan(plan_path)
    if audit_fixture is None:
        raise CampaignError("a pre-registered --audit-fixture is required at initialization")
    campaign_dir.mkdir(parents=True, exist_ok=False)
    frozen_root = campaign_dir / "frozen"
    frozen_root.mkdir()
    parent_revision = full_revision(plan["parent_revision"])
    parent = frozen_root / "parent" / "endurant-harness"
    candidate = frozen_root / "candidate" / "endurant-harness"
    noop = frozen_root / "noop" / "endurant-harness"
    materialize_git_package(parent_revision, parent)
    candidate_source = resolve_inside(ROOT, plan["candidate_path"], "candidate_path")
    copy_regular_tree(candidate_source, candidate)
    copy_regular_tree(parent, noop)
    if tree_manifest(parent) != tree_manifest(noop):
        raise CampaignError("no-op arm is not byte-identical to parent")
    changed = file_differences(parent, candidate)
    outside = [path for path in changed if not allowed_path(path, plan["proposal"]["editable_surface"])]
    if outside:
        raise CampaignError(f"candidate changed outside editable_surface: {outside}")
    fixture_sources = {fixture: fixture_path(fixture) for fixture in plan["fixtures"]}
    fixtures = {
        fixture: {
            "path": str(source.relative_to(ROOT)),
            "sha256": tree_sha256(source),
        }
        for fixture, source in fixture_sources.items()
    }
    audit_source = audit_fixture.resolve()
    required_audit = ("fixture.json", "hidden_grade.py", "task.txt")
    if (
        any(not (audit_source / name).is_file() for name in required_audit)
        or not (audit_source / "template").is_dir()
        or tree_sha256(audit_source) != plan["audit_fixture_sha256"]
    ):
        raise CampaignError("audit fixture is incomplete or differs from its pre-registered hash")
    audit = frozen_root / "audit-fixture"
    copy_regular_tree(audit_source, audit)
    audit_sha256 = tree_sha256(audit)
    audit.chmod(0)
    traces_path = resolve_inside(ROOT, plan["mining_trace_path"], "mining_trace_path")
    mining = mine_traces(load_traces(traces_path), min_support=2)
    if not mining["clusters"]:
        raise CampaignError("mining partition has no recurring mechanism")
    if codex_version is None:
        codex_version = subprocess.run(
            ["codex", "--version"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        ).stdout.strip()
    frozen = {
        "audit_fixture": {
            "path": "frozen/audit-fixture",
            "sha256": audit_sha256,
        },
        "candidate": {"path": "frozen/candidate/endurant-harness", "sha256": tree_sha256(candidate)},
        "candidate_diff_paths": changed,
        "environment": {
            "history": "disabled",
            "memory": "disabled",
            "model": plan["model"],
            "network": "disabled",
            "codex_version": codex_version,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "reasoning_effort": plan["reasoning_effort"],
            "subagents": "disabled",
            "timeout_seconds": plan["timeout_seconds"],
        },
        "fixtures": fixtures,
        "mining": mining,
        "mining_trace_sha256": sha256_file(traces_path),
        "noop": {"path": "frozen/noop/endurant-harness", "sha256": tree_sha256(noop)},
        "parent": {
            "path": "frozen/parent/endurant-harness",
            "revision": parent_revision,
            "sha256": tree_sha256(parent),
        },
        "plan": plan,
        "plan_sha256": sha256_file(plan_path),
        "runner_inputs": {
            "lab/eval_lib.py": sha256_file(EVAL_LIB),
            "lab/grade_run.py": sha256_file(GRADE_RUN),
            "lab/run_agent.py": sha256_file(RUN_AGENT),
            "lab/run_promotion_campaign.py": sha256_file(PROMOTION_RUNNER),
        },
        "schema_version": 1,
    }
    frozen["frozen_sha256"] = canonical_sha256(frozen)
    write_json_atomic(campaign_dir / "frozen.json", frozen)
    return frozen


def validate_frozen(campaign_dir: Path) -> dict[str, Any]:
    campaign_dir = campaign_dir.resolve()
    frozen = read_json(campaign_dir / "frozen.json")
    body = {key: value for key, value in frozen.items() if key != "frozen_sha256"}
    if frozen.get("frozen_sha256") != canonical_sha256(body):
        raise CampaignError("frozen contract hash mismatch")
    for arm in ("parent", "candidate", "noop"):
        expected_path = f"frozen/{arm}/endurant-harness"
        if frozen[arm].get("path") != expected_path:
            raise CampaignError(f"{arm} path differs from frozen layout")
        path = resolve_inside(campaign_dir, frozen[arm]["path"], f"{arm} path")
        if tree_sha256(path) != frozen[arm]["sha256"]:
            raise CampaignError(f"{arm} package drift")
    if frozen["parent"]["sha256"] != frozen["noop"]["sha256"]:
        raise CampaignError("no-op package drift")
    for fixture, details in frozen["fixtures"].items():
        expected_path = f"fixtures/{fixture}"
        if details.get("path") != expected_path:
            raise CampaignError(f"fixture path differs from frozen layout: {fixture}")
        path = resolve_inside(ROOT, details["path"], f"fixture path: {fixture}")
        if tree_sha256(path) != details["sha256"]:
            raise CampaignError(f"fixture drift: {fixture}")
    runner_inputs = {
        "lab/eval_lib.py": EVAL_LIB,
        "lab/grade_run.py": GRADE_RUN,
        "lab/run_agent.py": RUN_AGENT,
        "lab/run_promotion_campaign.py": PROMOTION_RUNNER,
    }
    if set(frozen.get("runner_inputs", {})) != set(runner_inputs):
        raise CampaignError("runner input allowlist mismatch")
    for relative, path in runner_inputs.items():
        digest = frozen["runner_inputs"][relative]
        if sha256_file(path) != digest:
            raise CampaignError(f"runner input drift: {relative}")
    if frozen["audit_fixture"].get("path") != "frozen/audit-fixture":
        raise CampaignError("audit fixture path differs from frozen layout")
    audit = resolve_inside(campaign_dir, "frozen/audit-fixture", "audit fixture path")
    if sealed_audit_sha256(audit) != frozen["audit_fixture"]["sha256"]:
        raise CampaignError("sealed audit fixture drift")
    return frozen


def ledger_events(campaign_dir: Path) -> list[dict[str, Any]]:
    path = campaign_dir / "campaign-events.jsonl"
    if not path.is_file():
        return []
    result: list[dict[str, Any]] = []
    previous = "0" * 64
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        event = json.loads(line)
        if not isinstance(event, dict) or event.get("sequence") != index:
            raise CampaignError("campaign ledger sequence is invalid")
        event_hash = event.get("event_sha256")
        body = {key: value for key, value in event.items() if key != "event_sha256"}
        if body.get("previous_event_sha256") != previous or event_hash != canonical_sha256(body):
            raise CampaignError("campaign ledger hash chain is invalid")
        previous = event_hash
        result.append(event)
    return result


def append_event(campaign_dir: Path, body: dict[str, Any]) -> dict[str, Any]:
    events = ledger_events(campaign_dir)
    event = {
        **body,
        "previous_event_sha256": events[-1]["event_sha256"] if events else "0" * 64,
        "sequence": len(events),
    }
    event["event_sha256"] = canonical_sha256(event)
    path = campaign_dir / "campaign-events.jsonl"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        remaining = memoryview(canonical_json_bytes(event))
        while remaining:
            remaining = remaining[os.write(descriptor, remaining) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return event


def validate_lineage_attestation(
    value: dict[str, Any], frozen: dict[str, Any]
) -> None:
    if set(value) != LINEAGE_ATTESTATION_KEYS or value.get("schema_version") != 1:
        raise CampaignError("lineage attestation has invalid keys or schema")
    if value.get("frozen_sha256") != frozen["frozen_sha256"]:
        raise CampaignError("lineage attestation uses a different frozen contract")
    if value.get("candidate_sha256") != frozen["candidate"]["sha256"]:
        raise CampaignError("lineage attestation uses a different candidate")
    if value.get("lineage_kind") not in {"single-candidate", "merged-winner"}:
        raise CampaignError("lineage attestation has invalid lineage_kind")
    if value.get("causal_minimum") is not True:
        raise CampaignError("lineage attestation does not attest a causal minimum")
    if not isinstance(value.get("tested_removals"), list) or not all(
        isinstance(item, str) and item.strip() for item in value["tested_removals"]
    ):
        raise CampaignError("lineage attestation tested_removals are invalid")
    if value["lineage_kind"] == "merged-winner" and not value["tested_removals"]:
        raise CampaignError("merged-winner attestation requires tested_removals")
    for field in ("attested_by", "rationale"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise CampaignError(f"lineage attestation {field} is empty")


def lineage_seal(campaign_dir: Path) -> dict[str, Any] | None:
    events = ledger_events(campaign_dir)
    seals = [event for event in events if event.get("kind") == "lineage-sealed"]
    path = campaign_dir / "lineage-attestation.json"
    if not seals and not path.exists():
        return None
    if len(seals) != 1 or not path.is_file() or path.is_symlink():
        raise CampaignError("lineage seal is incomplete or duplicated")
    frozen = validate_frozen(campaign_dir)
    attestation = load_object(path)
    validate_lineage_attestation(attestation, frozen)
    confirmation_sha256 = canonical_sha256(phase_summary(campaign_dir, "confirmation"))
    seal = seals[0]
    if (
        seal.get("attestation_sha256") != sha256_file(path)
        or seal.get("candidate_sha256") != frozen["candidate"]["sha256"]
        or seal.get("confirmation_summary_sha256") != confirmation_sha256
        or seal.get("frozen_sha256") != frozen["frozen_sha256"]
    ):
        raise CampaignError("lineage seal differs from current evidence")
    seal_sequence = seal["sequence"]
    if any(
        event.get("sequence", -1) < seal_sequence
        and (
            event.get("kind") == "run"
            and event.get("record", {}).get("phase") == "audit"
            or event.get("kind") == "started"
            and event.get("slot", {}).get("phase") == "audit"
        )
        for event in events
    ):
        raise CampaignError("lineage was sealed after audit execution began")
    return {
        "attestation_sha256": seal["attestation_sha256"],
        "attested_by": attestation["attested_by"],
        "lineage_kind": attestation["lineage_kind"],
    }


def _seal_lineage(campaign_dir: Path, attestation_path: Path) -> dict[str, Any]:
    campaign_dir = campaign_dir.resolve()
    frozen = validate_frozen(campaign_dir)
    if lineage_seal(campaign_dir) is not None:
        raise CampaignError("lineage is already sealed")
    confirmation = phase_summary(campaign_dir, "confirmation")
    if confirmation["passed"] is not True or confirmation["decision"] != "candidate":
        raise CampaignError("confirmation did not reproduce the candidate effect")
    if phase_records(campaign_dir, "audit"):
        raise CampaignError("audit execution began before lineage was sealed")
    capture_errors = verify_capture_hashes(campaign_dir)
    if capture_errors:
        raise CampaignError("; ".join(capture_errors))
    attestation = load_object(attestation_path)
    validate_lineage_attestation(attestation, frozen)
    destination = campaign_dir / "lineage-attestation.json"
    write_json_atomic(destination, attestation)
    return append_event(
        campaign_dir,
        {
            "attestation_sha256": sha256_file(destination),
            "candidate_sha256": frozen["candidate"]["sha256"],
            "confirmation_summary_sha256": canonical_sha256(confirmation),
            "frozen_sha256": frozen["frozen_sha256"],
            "kind": "lineage-sealed",
        },
    )


def seal_lineage(campaign_dir: Path, attestation_path: Path) -> dict[str, Any]:
    lock = campaign_dir / ".campaign.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise CampaignError("campaign is locked; inspect before removing a stale lock") from exc
    try:
        return _seal_lineage(campaign_dir, attestation_path)
    finally:
        lock.rmdir()


def run_record(
    capture: Path,
    frozen: dict[str, Any],
    campaign_dir: Path,
    *,
    phase: str,
    pair: int,
    arm: str,
    fixture: str,
) -> dict[str, Any]:
    missing = [name for name in CAPTURE_FILES if not (capture / name).is_file()]
    if missing:
        raise CampaignError(f"run capture is incomplete: {missing}")
    summary = read_json(capture / "summary.json")
    metadata = read_json(capture / "metadata.json")
    grade = read_json(capture / "grade.json")
    agent = summary["agent"]
    expected_run_id = f"{frozen['plan']['id']}-{phase}-{pair}-{fixture}-{arm}"
    expected_capture = (ARTIFACTS / "runs" / expected_run_id).resolve()
    if capture.resolve() != expected_capture:
        raise CampaignError(f"capture does not match run slot: {expected_run_id}")
    expected_fixture = (
        frozen["audit_fixture"] if fixture == "sealed-audit" else frozen["fixtures"].get(fixture)
    )
    if not isinstance(expected_fixture, dict):
        raise CampaignError(f"run names an unfrozen fixture: {fixture}")
    expected_fixture_path = (
        campaign_dir / expected_fixture["path"]
        if fixture == "sealed-audit"
        else ROOT / expected_fixture["path"]
    ).resolve()
    expected_subject = (campaign_dir / frozen[arm]["path"]).resolve()
    metadata_valid = bool(
        summary.get("run_id") == expected_run_id
        and metadata.get("run_id") == expected_run_id
        and metadata.get("fixture") == fixture
        and metadata.get("repeat") == pair
        and metadata.get("subject") == f"promotion-{arm}"
        and metadata.get("subject_source") == str(expected_subject)
        and metadata.get("fixture_source") == str(expected_fixture_path)
        and canonical_sha256(metadata.get("fixture_tree_manifest"))
        == expected_fixture["sha256"]
        and canonical_sha256(metadata.get("subject_tree_manifest"))
        == frozen[arm]["sha256"]
        and metadata.get("model") == frozen["environment"]["model"]
        and metadata.get("reasoning_effort") == frozen["environment"]["reasoning_effort"]
        and metadata.get("codex_version") == frozen["environment"]["codex_version"]
    )
    usage = agent.get("usage", {})
    integrity_gates = {
        "agent_completed": agent.get("turn_status") == "completed",
        "agent_event_log": grade.get("agent_event_log_valid") is True
        and grade.get("agent_event_log_tampered") is not True,
        "fixture_tree_unchanged": grade.get("fixture_tree_unchanged") is True,
        "git_state": grade.get("git_state_unchanged") is True,
        "metadata_binding": metadata_valid,
        "scope": not grade.get("unexpected_changed_paths") and not grade.get("protected_changed_paths"),
        "subject_tree": grade.get("subject_tree_unchanged") is True,
    }
    return {
        "arm": arm,
        "capture": str(capture.relative_to(ROOT)),
        "capture_hashes": {
            name: sha256_file(capture / name)
            for name in CAPTURE_FILES
        },
        "command_items": agent.get("item_counts", {}).get("command_execution"),
        "codex_version": metadata.get("codex_version"),
        "duration_seconds": agent.get("duration_seconds"),
        "fixture": fixture,
        "frozen_sha256": frozen["frozen_sha256"],
        "integrity_gates": integrity_gates,
        "model": metadata.get("model"),
        "pair": pair,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "run_integrity_passed": all(integrity_gates.values()),
        "phase": phase,
        "reasoning_effort": metadata.get("reasoning_effort"),
        "run_id": summary.get("run_id"),
        "uncached_input_tokens": agent.get("uncached_input_tokens"),
        "usage": usage,
        "task_passed": summary.get("passed") is True,
    }


def run_arm(
    campaign_dir: Path,
    frozen: dict[str, Any],
    *,
    phase: str,
    pair: int,
    arm: str,
    fixture: str,
    fixture_path: Path | None = None,
) -> dict[str, Any]:
    label = f"{frozen['plan']['id']}-{phase}-{pair}-{fixture}-{arm}"
    subject = campaign_dir / frozen[arm]["path"]
    argv = [
        sys.executable,
        str(RUN_AGENT),
        "--fixture",
        fixture,
        "--subject-path",
        str(subject),
        "--subject-label",
        f"promotion-{arm}",
        "--run-id",
        label,
        "--repeat",
        str(pair),
        "--model",
        frozen["environment"]["model"],
        "--reasoning-effort",
        frozen["environment"]["reasoning_effort"],
        "--timeout",
        str(frozen["environment"]["timeout_seconds"]),
    ]
    if fixture_path is not None:
        if fixture_path.is_symlink() or fixture_path.stat().st_mode & 0o777:
            raise CampaignError("audit fixture is not sealed")
        argv.extend(
            ["--fixture-path", str(fixture_path), "--sealed-fixture-source"]
        )
    slot = {"arm": arm, "fixture": fixture, "pair": pair, "phase": phase}
    append_event(
        campaign_dir,
        {
            "frozen_sha256": frozen["frozen_sha256"],
            "kind": "started",
            "run_id": label,
            "slot": slot,
        },
    )
    proc = subprocess.Popen(
        argv,
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        try:
            stdout, stderr = proc.communicate(
                timeout=frozen["environment"]["timeout_seconds"] + 300
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            stdout, stderr = proc.communicate()
    finally:
        if fixture_path is not None and not fixture_path.is_symlink():
            fixture_path.chmod(0)
    if timed_out:
        append_event(
            campaign_dir,
            {"kind": "inconclusive", "reason": "timeout", "run_id": label, "slot": slot},
        )
        raise CampaignError(f"run timed out and is inconclusive: {label}")
    capture = ARTIFACTS / "runs" / label
    if not (capture / "summary.json").is_file():
        append_event(
            campaign_dir,
            {"kind": "inconclusive", "reason": "missing-summary", "run_id": label, "slot": slot},
        )
        raise CampaignError(f"run produced no summary: {label}: {stderr[-1000:]}")
    try:
        record = run_record(
            capture,
            frozen,
            campaign_dir,
            phase=phase,
            pair=pair,
            arm=arm,
            fixture=fixture,
        )
    except CampaignError:
        append_event(
            campaign_dir,
            {"kind": "inconclusive", "reason": "incomplete-capture", "run_id": label, "slot": slot},
        )
        raise
    record["runner_returncode"] = proc.returncode
    append_event(
        campaign_dir,
        {
            "frozen_sha256": frozen["frozen_sha256"],
            "kind": "run",
            "record": record,
        },
    )
    return record


def expected_slots(frozen: dict[str, Any], phase: str) -> list[tuple[int, str, str]]:
    fixtures = frozen["plan"]["fixtures"]
    pairs = frozen["plan"]["aa_pairs"] if phase == "aa" else frozen["plan"]["ab_pairs"]
    arms = ("parent", "noop") if phase == "aa" else ("parent", "candidate")
    slots: list[tuple[int, str, str]] = []
    for pair in range(1, pairs + 1):
        for fixture in fixtures:
            order = arms if pair % 2 else tuple(reversed(arms))
            slots.extend((pair, fixture, arm) for arm in order)
    return slots


def completed_records(campaign_dir: Path) -> list[dict[str, Any]]:
    return [
        event["record"]
        for event in ledger_events(campaign_dir)
        if event.get("kind") == "run"
    ]


def phase_records(campaign_dir: Path, phase: str) -> list[dict[str, Any]]:
    return [record for record in completed_records(campaign_dir) if record["phase"] == phase]


def slot_key(value: dict[str, Any]) -> tuple[str, int, str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(value.get(field), str) and value[field]
        for field in ("phase", "fixture", "arm")
    ) or not isinstance(value.get("pair"), int) or isinstance(value["pair"], bool):
        raise CampaignError("campaign run slot is invalid")
    return (value["phase"], value["pair"], value["fixture"], value["arm"])


def validate_resume_state(
    events: list[dict[str, Any]], frozen_sha256: str | None = None
) -> None:
    started: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    completed: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    for event in events:
        if event.get("kind") == "started":
            if set(event.get("slot", {})) != {"arm", "fixture", "pair", "phase"}:
                raise CampaignError("campaign start slot is invalid")
            key = slot_key(event["slot"])
            if key in started:
                raise CampaignError("campaign contains duplicate run slots")
            started[key] = event
        elif event.get("kind") == "run":
            key = slot_key(event["record"])
            if key in completed:
                raise CampaignError("campaign contains duplicate run slots")
            completed[key] = event
        elif event.get("kind") == "inconclusive":
            raise CampaignError("campaign contains an inconclusive started run")
    if set(completed) - set(started):
        raise CampaignError("campaign contains a completed run without a start event")
    for key, event in completed.items():
        start = started[key]
        record = event["record"]
        if start.get("run_id") != record.get("run_id"):
            raise CampaignError("campaign start/run identity mismatch")
        if frozen_sha256 is not None and (
            start.get("frozen_sha256") != frozen_sha256
            or record.get("frozen_sha256") != frozen_sha256
            or event.get("frozen_sha256") != frozen_sha256
        ):
            raise CampaignError("campaign run used a different frozen contract")
    unresolved = set(started) - set(completed)
    if unresolved:
        raise CampaignError(f"campaign has incomplete started slots: {sorted(unresolved)}")


def _run_phase(campaign_dir: Path, phase: str) -> dict[str, Any]:
    frozen = validate_frozen(campaign_dir)
    if phase not in PHASES:
        raise CampaignError(f"unknown phase: {phase}")
    if phase == "development" and not phase_summary(campaign_dir, "aa")["passed"]:
        raise CampaignError("A/A gate has not passed")
    if phase == "confirmation" and phase_summary(campaign_dir, "development")["decision"] != "candidate":
        raise CampaignError("development did not select the candidate")
    if phase == "audit":
        confirmation = phase_summary(campaign_dir, "confirmation")
        if confirmation["passed"] is not True or confirmation["decision"] != "candidate":
            raise CampaignError("confirmation did not reproduce the candidate effect")
        if lineage_seal(campaign_dir) is None:
            raise CampaignError("lineage must be sealed before audit")
        private = campaign_dir / frozen["audit_fixture"]["path"]
        slots = [(1, "sealed-audit", "parent"), (1, "sealed-audit", "candidate")]
    else:
        slots = expected_slots(frozen, phase)
        private = None
    events = ledger_events(campaign_dir)
    validate_resume_state(events, frozen["frozen_sha256"])
    if any(event.get("kind") == "phase" and event.get("phase") == phase for event in events):
        raise CampaignError(f"phase already completed: {phase}")
    existing = {
        (record["phase"], record["pair"], record["fixture"], record["arm"]): record
        for record in completed_records(campaign_dir)
    }
    capture_errors = verify_capture_hashes(campaign_dir)
    if capture_errors:
        raise CampaignError("; ".join(capture_errors))
    for pair, fixture, arm in slots:
        key = (phase, pair, fixture, arm)
        if key in existing:
            continue
        run_arm(
            campaign_dir,
            frozen,
            phase=phase,
            pair=pair,
            arm=arm,
            fixture=fixture,
            fixture_path=private,
        )
    summary = phase_summary(campaign_dir, phase)
    append_event(campaign_dir, {"kind": "phase", "phase": phase, "summary": summary})
    return summary


def run_phase(campaign_dir: Path, phase: str) -> dict[str, Any]:
    lock = campaign_dir / ".campaign.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise CampaignError("campaign is locked; inspect before removing a stale lock") from exc
    try:
        return _run_phase(campaign_dir, phase)
    finally:
        lock.rmdir()


def finite_metric(record: dict[str, Any], field: str) -> float | None:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def paired_changes(
    records: list[dict[str, Any]], baseline: str, changed: str, field: str
) -> tuple[list[float], bool]:
    grouped: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault((record["pair"], record["fixture"]), {})[record["arm"]] = record
    changes: list[float] = []
    for arms in grouped.values():
        before = finite_metric(arms.get(baseline, {}), field)
        after = finite_metric(arms.get(changed, {}), field)
        if before is None or after is None or before == 0:
            return changes, False
        changes.append((before - after) / before)
    return changes, bool(grouped)


def one_sided_sign_probability(candidate_only: int, parent_only: int) -> float:
    discordant = candidate_only + parent_only
    if discordant == 0 or candidate_only <= parent_only:
        return 1.0
    return sum(
        math.comb(discordant, successes)
        for successes in range(candidate_only, discordant + 1)
    ) / (2**discordant)


def percentile90(values: list[float]) -> float:
    if not values:
        return float("inf")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.9) - 1)]


def phase_summary(campaign_dir: Path, phase: str) -> dict[str, Any]:
    frozen = validate_frozen(campaign_dir)
    records = phase_records(campaign_dir, phase)
    if phase == "audit":
        expected = 2
        baseline, changed = "parent", "candidate"
        expected_keys = {
            ("audit", 1, "sealed-audit", "parent"),
            ("audit", 1, "sealed-audit", "candidate"),
        }
    else:
        slots = expected_slots(frozen, phase)
        expected = len(slots)
        baseline, changed = ("parent", "noop") if phase == "aa" else ("parent", "candidate")
        expected_keys = {(phase, pair, fixture, arm) for pair, fixture, arm in slots}
    actual_keys = [slot_key(record) for record in records]
    complete = len(records) == expected and set(actual_keys) == expected_keys
    environment_fixed = all(
        record.get("model") == frozen["environment"]["model"]
        and record.get("reasoning_effort") == frozen["environment"]["reasoning_effort"]
        and record.get("codex_version") == frozen["environment"]["codex_version"]
        and record.get("platform") == frozen["environment"]["platform"]
        and record.get("python") == frozen["environment"]["python"]
        and record.get("frozen_sha256") == frozen["frozen_sha256"]
        for record in records
    )
    integrity_passed = complete and environment_fixed and all(
        record.get("run_integrity_passed") is True
        and record.get("runner_returncode") == 0
        for record in records
    )
    field = frozen["plan"]["primary_cost_metric"]
    changes, metrics_complete = paired_changes(records, baseline, changed, field)
    metrics_complete = metrics_complete and len(changes) == expected // 2
    median_change = statistics.median(changes) if changes else None
    summary: dict[str, Any] = {
        "complete": complete,
        "environment_fixed": environment_fixed,
        "run_integrity_passed": integrity_passed,
        "paired_change_fractions": [round(value, 6) for value in changes],
        "paired_count": len(changes),
        "paired_metrics_complete": metrics_complete,
        "passed": False,
        "phase": phase,
        "primary_cost_metric": field,
        "record_count": len(records),
    }
    if phase == "aa":
        grouped: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault((record["pair"], record["fixture"]), {})[
                record["arm"]
            ] = record
        outcomes_match = complete and all(
            arms.get("parent", {}).get("task_passed")
            is arms.get("noop", {}).get("task_passed")
            for arms in grouped.values()
        )
        all_tasks_passed = complete and all(
            record.get("task_passed") is True for record in records
        )
        absolute_noise = [abs(value) for value in changes]
        p90 = percentile90(absolute_noise)
        bias = abs(float(median_change)) if median_change is not None else float("inf")
        summary.update(
            {
                "all_tasks_passed": all_tasks_passed,
                "decision": (
                    "continue"
                    if integrity_passed
                    and outcomes_match
                    and all_tasks_passed
                    and metrics_complete
                    and bias <= frozen["plan"]["max_aa_bias_fraction"]
                    else "stop"
                ),
                "median_bias_fraction": round(bias, 6),
                "noise_p90_fraction": round(p90, 6),
                "outcomes_match": outcomes_match,
                "passed": (
                    integrity_passed
                    and outcomes_match
                    and all_tasks_passed
                    and metrics_complete
                    and bias <= frozen["plan"]["max_aa_bias_fraction"]
                ),
            }
        )
        return summary
    aa = phase_summary(campaign_dir, "aa") if phase != "aa" else {}
    threshold = max(
        float(frozen["plan"]["materiality_fraction"]),
        float(aa.get("noise_p90_fraction", float("inf"))),
    )
    no_pair_regressed = True
    parent_successes = 0
    candidate_successes = 0
    grouped: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault((record["pair"], record["fixture"]), {})[record["arm"]] = record
    for arms in grouped.values():
        parent_passed = arms.get("parent", {}).get("task_passed") is True
        candidate_passed = arms.get("candidate", {}).get("task_passed") is True
        parent_successes += parent_passed
        candidate_successes += candidate_passed
        if parent_passed and not candidate_passed:
            no_pair_regressed = False
    candidate_all_passed = complete and candidate_successes == len(grouped)
    candidate_only = sum(
        arms.get("candidate", {}).get("task_passed") is True
        and arms.get("parent", {}).get("task_passed") is not True
        for arms in grouped.values()
    )
    parent_only = sum(
        arms.get("parent", {}).get("task_passed") is True
        and arms.get("candidate", {}).get("task_passed") is not True
        for arms in grouped.values()
    )
    task_gain_probability = one_sided_sign_probability(candidate_only, parent_only)
    task_gain = task_gain_probability <= 0.05
    material = metrics_complete and median_change is not None and median_change >= threshold
    if phase in {"development", "confirmation"}:
        if not integrity_passed or not metrics_complete:
            decision = "insufficient-evidence"
        elif not no_pair_regressed or not candidate_all_passed:
            decision = "reject"
        elif task_gain or material:
            decision = "candidate"
        else:
            decision = "no-op"
        summary.update(
            {
                "candidate_successes": candidate_successes,
                "candidate_all_passed": candidate_all_passed,
                "candidate_only_successes": candidate_only,
                "decision": decision,
                "materiality_threshold_fraction": round(threshold, 6),
                "median_improvement_fraction": round(float(median_change or 0), 6),
                "no_pair_regressed": no_pair_regressed,
                "parent_successes": parent_successes,
                "parent_only_successes": parent_only,
                "passed": decision == "candidate",
                "task_gain": task_gain,
                "task_gain_probability": round(task_gain_probability, 6),
            }
        )
    else:
        summary.update(
            {
                "candidate_successes": candidate_successes,
                "decision": (
                    "insufficient-evidence"
                    if not integrity_passed
                    else "candidate"
                    if no_pair_regressed and candidate_all_passed
                    else "reject"
                ),
                "candidate_all_passed": candidate_all_passed,
                "no_pair_regressed": no_pair_regressed,
                "parent_successes": parent_successes,
                "passed": (
                    integrity_passed
                    and no_pair_regressed
                    and candidate_all_passed
                ),
            }
        )
    return summary


def campaign_decision(campaign_dir: Path) -> dict[str, Any]:
    frozen = validate_frozen(campaign_dir)
    lineage = lineage_seal(campaign_dir)
    aa = phase_summary(campaign_dir, "aa")
    if not aa["passed"]:
        decision = "insufficient-evidence"
    else:
        development = phase_summary(campaign_dir, "development")
        if development["decision"] == "insufficient-evidence":
            decision = "insufficient-evidence"
        elif development["decision"] == "no-op":
            decision = "no-op"
        elif development["decision"] == "reject":
            decision = "reject"
        else:
            confirmation = phase_summary(campaign_dir, "confirmation")
            audit = phase_summary(campaign_dir, "audit")
            if "insufficient-evidence" in {
                confirmation["decision"],
                audit["decision"],
            }:
                decision = "insufficient-evidence"
            elif (
                confirmation["decision"] == "candidate"
                and audit["decision"] == "candidate"
                and lineage is not None
            ):
                decision = "candidate"
            else:
                decision = "reject"
    receipt = {
        "campaign_id": frozen["plan"]["id"],
        "decision": decision,
        "evidence_tier": (
            "promotion-audited"
            if decision == "candidate"
            else "development-result"
        ),
        "human_authorization_required": True,
        "lineage_seal": lineage,
        "ledger_sha256": sha256_bytes(
            (campaign_dir / "campaign-events.jsonl").read_bytes()
            if (campaign_dir / "campaign-events.jsonl").is_file()
            else b""
        ),
        "schema_version": 1,
        "summaries": {
            phase: phase_summary(campaign_dir, phase)
            for phase in PHASES
        },
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def capture_path(value: object, run_id: object = None) -> Path:
    if not isinstance(value, str):
        raise CampaignError("capture path is missing")
    path = (ROOT / value).resolve()
    try:
        relative = path.relative_to((ARTIFACTS / "runs").resolve())
    except ValueError as exc:
        raise CampaignError(f"capture escapes run artifacts: {value}") from exc
    if len(relative.parts) != 1 or not isinstance(run_id, str) or relative.name != run_id:
        raise CampaignError(f"capture does not match run id: {value}")
    return path


def verify_capture_hashes(campaign_dir: Path) -> list[str]:
    errors: list[str] = []
    frozen = validate_frozen(campaign_dir)
    for record in completed_records(campaign_dir):
        try:
            capture = capture_path(record.get("capture"), record.get("run_id"))
        except CampaignError as exc:
            errors.append(str(exc))
            continue
        capture_hashes = record.get("capture_hashes")
        if not isinstance(capture_hashes, dict) or set(capture_hashes) != set(CAPTURE_FILES):
            errors.append(f"capture evidence incomplete: {record.get('run_id')}")
            continue
        for name in CAPTURE_FILES:
            expected = capture_hashes[name]
            path = capture / name
            if not path.is_file() or sha256_file(path) != expected:
                errors.append(f"capture drift: {record['run_id']}/{name}")
        try:
            recomputed = run_record(
                capture,
                frozen,
                campaign_dir,
                phase=record["phase"],
                pair=record["pair"],
                arm=record["arm"],
                fixture=record["fixture"],
            )
        except (CampaignError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"capture invalid: {record.get('run_id')}: {exc}")
            continue
        claimed = {key: value for key, value in record.items() if key != "runner_returncode"}
        if claimed != recomputed:
            errors.append(f"capture claims differ from raw evidence: {record.get('run_id')}")
        if record.get("runner_returncode") != 0:
            errors.append(f"runner failed: {record.get('run_id')}")
        metadata = read_json(capture / "metadata.json")
        if canonical_sha256(metadata.get("subject_tree_manifest")) != frozen[record["arm"]][
            "sha256"
        ]:
            errors.append(f"subject package differs from frozen arm: {record.get('run_id')}")
    return errors


def verify_campaign(campaign_dir: Path) -> dict[str, Any]:
    campaign_dir = campaign_dir.resolve()
    lock = campaign_dir / ".campaign.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise CampaignError("campaign is locked; inspect before removing a stale lock") from exc
    try:
        pending = {
            "decision": "insufficient-evidence",
            "errors": ["verification-in-progress"],
            "promotion_eligible": False,
            "schema_version": 1,
            "verification_integrity_passed": False,
        }
        pending["receipt_sha256"] = canonical_sha256(pending)
        write_json_atomic(campaign_dir / "decision.json", pending)
        result = campaign_decision(campaign_dir)
        frozen = validate_frozen(campaign_dir)
        errors: list[str] = []
        try:
            validate_resume_state(ledger_events(campaign_dir), frozen["frozen_sha256"])
        except CampaignError as exc:
            errors.append(str(exc))
        try:
            if phase_records(campaign_dir, "audit") and lineage_seal(campaign_dir) is None:
                errors.append("audit has no pre-existing lineage seal")
        except CampaignError as exc:
            errors.append(str(exc))
        errors.extend(verify_capture_hashes(campaign_dir))
        result.pop("receipt_sha256", None)
        if errors:
            result["decision"] = "insufficient-evidence"
            result["evidence_tier"] = "verification-failed"
        result.update(
            {
                "errors": errors,
                "promotion_eligible": result["decision"] == "candidate" and not errors,
                "verification_integrity_passed": not errors,
            }
        )
        result["receipt_sha256"] = canonical_sha256(result)
        write_json_atomic(campaign_dir / "decision.json", result)
        return result
    finally:
        lock.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    mine = subparsers.add_parser("mine")
    mine.add_argument("trace_path", type=Path)
    mine.add_argument("--min-support", type=int, default=2)

    initialize = subparsers.add_parser("init")
    initialize.add_argument("plan", type=Path)
    initialize.add_argument("campaign_dir", type=Path)
    initialize.add_argument("--audit-fixture", required=True, type=Path)

    run = subparsers.add_parser("run")
    run.add_argument("campaign_dir", type=Path)
    run.add_argument("phase", choices=PHASES)

    verify = subparsers.add_parser("verify")
    verify.add_argument("campaign_dir", type=Path)

    lineage = subparsers.add_parser("seal-lineage")
    lineage.add_argument("campaign_dir", type=Path)
    lineage.add_argument("attestation", type=Path)

    args = parser.parse_args()
    try:
        if args.command == "mine":
            result = mine_traces(load_traces(args.trace_path.resolve()), args.min_support)
        elif args.command == "init":
            result = freeze(
                args.plan.resolve(),
                args.campaign_dir.resolve(),
                audit_fixture=args.audit_fixture.resolve(),
            )
        elif args.command == "run":
            result = run_phase(args.campaign_dir.resolve(), args.phase)
        elif args.command == "seal-lineage":
            result = seal_lineage(
                args.campaign_dir.resolve(), args.attestation.resolve()
            )
        else:
            result = verify_campaign(args.campaign_dir.resolve())
    except (CampaignError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc), "passed": False}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.command == "verify" and not result["verification_integrity_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
