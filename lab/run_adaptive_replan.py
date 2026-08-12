#!/usr/bin/env python3
"""Exercise task-local adaptive replans with isolated parallel agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from eval_lib import (
    ARTIFACTS,
    FIXTURES,
    canonical_prompt,
    canonical_sha256,
    file_manifest,
    git_state,
    materialize_workspace,
    read_json,
    run_process,
    sha256_file,
    tree_manifest,
    write_json_atomic,
)


ROOT = Path(__file__).resolve().parents[1]
ADAPTIVE_RUNNER = Path(__file__)
RUN_AGENT = Path(__file__).with_name("run_agent.py")
GRADE_RUN = Path(__file__).with_name("grade_run.py")
EVAL_LIB = Path(__file__).with_name("eval_lib.py")
FAILED_SETTINGS_PATCH = ROOT / "lab" / "baselines" / "settings-failed-attempt.patch"
CAPTURE_FILES = (
    "agent-events.jsonl",
    "agent-events-observed.jsonl",
    "agent-metrics.json",
    "codex-observed.jsonl",
    "codex.jsonl",
    "grade.json",
    "grader-ci.stderr",
    "grader-ci.stdout",
    "metadata.json",
    "prompt.txt",
    "summary.json",
)
CASES: dict[str, dict[str, Any]] = {
    "software-settings": {
        "fixture": "settings-override-correctness",
        "integration": "patch",
        "oracle": [sys.executable, "scripts/verify.py", "focused"],
        "seed_patch": FAILED_SETTINGS_PATCH,
        "candidates": [
            {
                "id": "explicit-condition",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "low",
                "context": (
                    "A prior isolated attempt replaced `overrides.get(key) or default` with "
                    "`overrides.get(key, default)`. The unchanged focused oracle still failed "
                    "because explicit None must use the default. You are the explicit-condition "
                    "candidate: fix the shared merge function using explicit membership/None "
                    "semantics, add the requested public-path regressions, and run the original gates."
                ),
            },
            {
                "id": "sentinel-lookup",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "context": (
                    "A prior isolated attempt replaced `overrides.get(key) or default` with "
                    "`overrides.get(key, default)`. The unchanged focused oracle still failed "
                    "because explicit None must use the default. You are the sentinel candidate: "
                    "use a private sentinel or equivalent single-lookup mechanism in the shared "
                    "merge function, add the requested public-path regressions, and run the original gates."
                ),
            },
        ],
    },
    "authorized-recovery": {
        "fixture": "authorized-recovery",
        "integration": "action",
        "oracle": [sys.executable, "scripts/verify.py", "terminal"],
        "candidates": [
            {
                "id": "checkpoint-resume",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "medium",
                "context": (
                    "You are the checkpoint-resume recovery candidate. Inspect exact state, use "
                    "the compare-and-swap resume command, and prove the unchanged terminal oracle. "
                    "Run these as four separate standalone commands in order: "
                    "`python3 scripts/mock_control.py inspect`; `python3 scripts/mock_control.py "
                    "resume --expected-state-sha256 <the state_sha256 returned by inspect>`; "
                    "`python3 scripts/verify.py terminal`; and "
                    "`python3 scripts/verify.py ci-preflight`. Do not combine them with other shell "
                    "commands. Do not edit mocked state directly or use another recovery mechanism."
                ),
            },
            {
                "id": "restart-source",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "context": (
                    "You are the restart-source recovery candidate. Evaluate only whether the "
                    "compare-and-swap restart command can satisfy every stated invariant and the "
                    "unchanged terminal oracle. If it cannot, stop with exact evidence; do not "
                    "switch to resume, edit state directly, or weaken verification."
                ),
            },
        ],
    },
}


def tree_sha256(path: Path) -> str:
    return canonical_sha256(tree_manifest(path))


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_identifier(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value)
    return cleaned.strip("-")[:80]


def process_result(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "returncode": completed.returncode,
        "stdout_sha256": bytes_sha256(completed.stdout.encode()),
        "stderr_sha256": bytes_sha256(completed.stderr.encode()),
    }


def apply_patch(workspace: Path, patch: bytes) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=workspace,
        input=patch.decode("utf-8"),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )


def git_patch(workspace: Path) -> bytes:
    completed = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--"],
        cwd=workspace,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace"))
    return completed.stdout


def untracked_paths(workspace: Path) -> list[str]:
    completed = run_process(
        ["git", "ls-files", "--others", "--exclude-standard"], workspace, timeout=30
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)
    return sorted(line for line in completed.stdout.splitlines() if line)


def runtime_bytecode(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}


def complete_patch(
    workspace: Path, approved_changed_paths: list[str]
) -> tuple[bytes, list[str], list[str], list[str]]:
    patch = bytearray(git_patch(workspace))
    approved: list[str] = []
    removed: list[str] = []
    unexpected: list[str] = []
    allowed = set(approved_changed_paths)
    for relative in untracked_paths(workspace):
        candidate = workspace / relative
        try:
            candidate.resolve().relative_to(workspace.resolve())
        except ValueError:
            unexpected.append(relative)
            continue
        if runtime_bytecode(Path(relative)):
            if candidate.is_file() and not candidate.is_symlink():
                candidate.unlink()
                removed.append(relative)
                for parent in candidate.parents:
                    if parent == workspace:
                        break
                    if parent.name == "__pycache__":
                        try:
                            parent.rmdir()
                        except OSError:
                            pass
            else:
                unexpected.append(relative)
            continue
        if relative not in allowed or not candidate.is_file() or candidate.is_symlink():
            unexpected.append(relative)
            continue
        completed = subprocess.run(
            ["git", "diff", "--binary", "--no-index", "--", "/dev/null", relative],
            cwd=workspace,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if completed.returncode != 1 or not completed.stdout:
            raise RuntimeError(
                completed.stderr.decode("utf-8", errors="replace")
                or f"could not capture untracked path: {relative}"
            )
        patch.extend(completed.stdout)
        approved.append(relative)
    return bytes(patch), sorted(approved), sorted(removed), sorted(unexpected)


def patch_changed_lines(patch: bytes) -> int | None:
    completed = subprocess.run(
        ["git", "apply", "--numstat", "-"],
        cwd=ROOT,
        input=patch,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if completed.returncode != 0:
        return None
    total = 0
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        added, removed, _ = line.split("\t", 2)
        if not added.isdigit() or not removed.isdigit():
            return None
        total += int(added) + int(removed)
    return total


def candidate_run_contracts(
    case: dict[str, Any], campaign_id: str, fixture_path: Path
) -> list[dict[str, Any]]:
    base_prompt = canonical_prompt(str(case["fixture"]), fixture_path=fixture_path)
    contracts = []
    for candidate in case["candidates"]:
        context = candidate["context"] + "\n"
        prompt = (
            f"{base_prompt}\n\n<task-local-context>\n"
            f"{context.strip()}\n</task-local-context>"
        )
        contracts.append(
            {
                "context_sha256": bytes_sha256(context.encode()),
                "fixture_sha256": tree_sha256(fixture_path),
                "id": candidate["id"],
                "model": candidate["model"],
                "prompt_sha256": bytes_sha256(prompt.encode()),
                "reasoning_effort": candidate["reasoning_effort"],
                "run_id": f"{campaign_id}-{safe_identifier(candidate['id'])}",
                "subject": f"adaptive-{candidate['id']}",
            }
        )
    return contracts


def actual_overlap(candidates: list[dict[str, Any]]) -> bool:
    intervals = [
        (item.get("started_monotonic_ns"), item.get("ended_monotonic_ns"))
        for item in candidates
    ]
    return bool(
        intervals
        and all(
            isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(end, int)
            and not isinstance(end, bool)
            and start < end
            for start, end in intervals
        )
        and max(start for start, _ in intervals) < min(end for _, end in intervals)
    )


def seeded_fixture(
    case: dict[str, Any], workspace: Path, artifact_root: Path
) -> Path:
    source = FIXTURES / str(case["fixture"])
    destination = artifact_root / "seeded-fixture"
    destination.mkdir()
    for name in ("fixture.json", "hidden_grade.py", "task.txt"):
        shutil.copy2(source / name, destination / name)
    ignored = shutil.ignore_patterns(".git", "skills", "__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(workspace, destination / "template", ignore=ignored)
    return destination


def run_seeded_failure(
    case_id: str,
    case: dict[str, Any],
    subject: Path,
    campaign_id: str,
    artifact_root: Path,
) -> tuple[dict[str, Any], Path]:
    fixture = str(case["fixture"])
    run_id = f"{campaign_id}-seed"
    workspace, capture = materialize_workspace(
        fixture,
        "seed",
        run_id,
        subject_path=subject,
    )
    if case_id == "software-settings":
        patch = Path(case["seed_patch"]).read_bytes()
        applied = apply_patch(workspace, patch)
        if applied.returncode != 0:
            raise RuntimeError(f"seed patch failed: {applied.stderr}")
        source_sha256 = bytes_sha256(patch)
    else:
        source_sha256 = tree_sha256(workspace / "external")
    started_ns = time.monotonic_ns()
    completed = run_process(list(case["oracle"]), workspace, timeout=60)
    ended_ns = time.monotonic_ns()
    stdout_path = capture / "seeded-oracle.stdout"
    stderr_path = capture / "seeded-oracle.stderr"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    result = {
        **process_result(completed),
        "capture": str(capture.relative_to(ROOT)),
        "ended_monotonic_ns": ended_ns,
        "oracle": list(case["oracle"]),
        "seed_sha256": source_sha256,
        "stderr_path": str(stderr_path.relative_to(ROOT)),
        "stdout_path": str(stdout_path.relative_to(ROOT)),
        "started_monotonic_ns": started_ns,
        "workspace": str(workspace.relative_to(ROOT)),
    }
    if completed.returncode != 1:
        raise RuntimeError(
            f"seeded oracle must fail normally with exit 1, got {completed.returncode}"
        )
    write_json_atomic(capture / "seeded-failure.json", result)
    return result, seeded_fixture(case, workspace, artifact_root)


def candidate_argv(
    case: dict[str, Any],
    candidate: dict[str, str],
    subject: Path,
    campaign_id: str,
    context: Path,
    timeout: int,
    fixture_path: Path,
) -> tuple[str, list[str]]:
    run_id = f"{campaign_id}-{safe_identifier(candidate['id'])}"
    return run_id, [
        sys.executable,
        str(RUN_AGENT),
        "--fixture",
        str(case["fixture"]),
        "--fixture-path",
        str(fixture_path),
        "--subject-path",
        str(subject),
        "--subject-label",
        f"adaptive-{candidate['id']}",
        "--run-id",
        run_id,
        "--repeat",
        "1",
        "--model",
        candidate["model"],
        "--reasoning-effort",
        candidate["reasoning_effort"],
        "--timeout",
        str(timeout),
        "--prompt-context-file",
        str(context),
    ]


def process_group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def terminate_process(proc: subprocess.Popen[str], run_id: str) -> None:
    metadata_path = ARTIFACTS / "runs" / run_id / "metadata.json"
    metadata = read_json(metadata_path) if metadata_path.is_file() else {}
    child_pid = metadata.get("agent_pid")
    child_pid = child_pid if isinstance(child_pid, int) and child_pid > 1 else None
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if child_pid is not None:
        try:
            os.killpg(child_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=3)
    if child_pid is not None and process_group_exists(child_pid):
        try:
            os.killpg(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def launch_candidates(
    case: dict[str, Any],
    subject: Path,
    campaign_id: str,
    artifact_root: Path,
    timeout: int,
    fixture_path: Path,
) -> list[dict[str, Any]]:
    contracts = {
        item["id"]: item
        for item in candidate_run_contracts(case, campaign_id, fixture_path)
    }
    running: list[dict[str, Any]] = []
    for candidate in case["candidates"]:
        context = artifact_root / f"{candidate['id']}-context.txt"
        context.write_text(candidate["context"] + "\n", encoding="utf-8")
        run_id, argv = candidate_argv(
            case, candidate, subject, campaign_id, context, timeout, fixture_path
        )
        stdout_path = artifact_root / f"{candidate['id']}.stdout"
        stderr_path = artifact_root / f"{candidate['id']}.stderr"
        stdout = stdout_path.open("w", encoding="utf-8")
        stderr = stderr_path.open("w", encoding="utf-8")
        started_ns = time.monotonic_ns()
        proc = subprocess.Popen(
            argv,
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=stdout,
            stderr=stderr,
            text=True,
            start_new_session=True,
        )
        running.append(
            {
                "config": candidate,
                "contract": contracts[candidate["id"]],
                "context": context,
                "proc": proc,
                "run_id": run_id,
                "started_monotonic_ns": started_ns,
                "stderr": stderr,
                "stdout": stdout,
                "stderr_path": stderr_path,
                "stdout_path": stdout_path,
            }
        )

    deadline = time.monotonic() + timeout + 240
    pending = list(running)
    while pending and time.monotonic() < deadline:
        for item in list(pending):
            returncode = item["proc"].poll()
            if returncode is not None:
                item["returncode"] = returncode
                item["ended_monotonic_ns"] = time.monotonic_ns()
                pending.remove(item)
        if pending:
            time.sleep(0.05)
    for item in pending:
        terminate_process(item["proc"], item["run_id"])
        item["returncode"] = item["proc"].returncode
        item["ended_monotonic_ns"] = time.monotonic_ns()
        item["timed_out"] = True

    results: list[dict[str, Any]] = []
    for item in running:
        returncode = item["returncode"]
        timed_out = item.get("timed_out", False)
        ended_ns = item["ended_monotonic_ns"]
        item["stdout"].close()
        item["stderr"].close()
        capture = ARTIFACTS / "runs" / item["run_id"]
        summary_path = capture / "summary.json"
        metadata_path = capture / "metadata.json"
        summary = read_json(summary_path) if summary_path.is_file() else {}
        metadata = read_json(metadata_path) if metadata_path.is_file() else {}
        agent_metrics_path = capture / "agent-metrics.json"
        agent_metrics = read_json(agent_metrics_path) if agent_metrics_path.is_file() else {}
        grade = read_json(capture / "grade.json") if (capture / "grade.json").is_file() else {}
        workspace_value = metadata.get("workspace")
        workspace = Path(workspace_value) if isinstance(workspace_value, str) else None
        approved_paths = (
            grade.get("changed_paths", [])
            if grade.get("passed") is True and isinstance(grade.get("changed_paths"), list)
            else []
        )
        if workspace and workspace.is_dir():
            patch, approved_untracked, removed_runtime, untracked = complete_patch(
                workspace, approved_paths
            )
        else:
            patch, approved_untracked, removed_runtime, untracked = b"", [], [], []
        patch_path = artifact_root / f"{item['config']['id']}.patch"
        patch_path.write_bytes(patch)
        capture_hashes = {
            name: sha256_file(capture / name)
            for name in CAPTURE_FILES
            if (capture / name).is_file()
        }
        contract = item["contract"]
        workspace_id = metadata.get("workspace_id")
        expected_workspace = (
            ARTIFACTS / "workspaces" / workspace_id
            if isinstance(workspace_id, str)
            else None
        )
        prompt = metadata.get("prompt")
        fixture_source = metadata.get("fixture_source")
        operation = grade.get("runner_observed_operation")
        raw_bindings_valid = bool(
            metadata.get("run_id") == contract["run_id"]
            and metadata.get("fixture") == case["fixture"]
            and metadata.get("subject") == contract["subject"]
            and metadata.get("model") == contract["model"]
            and metadata.get("reasoning_effort") == contract["reasoning_effort"]
            and metadata.get("prompt_context_sha256") == contract["context_sha256"]
            and isinstance(prompt, str)
            and bytes_sha256(prompt.encode()) == contract["prompt_sha256"]
            and isinstance(fixture_source, str)
            and Path(fixture_source).resolve() == fixture_path.resolve()
            and canonical_sha256(metadata.get("fixture_tree_manifest"))
            == canonical_sha256(tree_manifest(fixture_path))
            and workspace is not None
            and expected_workspace is not None
            and workspace.resolve() == expected_workspace.resolve()
            and agent_metrics.get("agent_started_monotonic_ns")
            == metadata.get("agent_started_monotonic_ns")
        )
        result = {
            "capture": str(capture.relative_to(ROOT)),
            "capture_hashes": capture_hashes,
            "changed_lines": patch_changed_lines(patch),
            "changed_paths": grade.get("changed_paths", []),
            "duration_seconds": agent_metrics.get("duration_seconds"),
            "ended_monotonic_ns": agent_metrics.get("agent_ended_monotonic_ns"),
            "grade_sha256": sha256_file(capture / "grade.json") if (capture / "grade.json").is_file() else None,
            "id": item["config"]["id"],
            "model": metadata.get("model"),
            "passed": bool(
                returncode == 0
                and not timed_out
                and not untracked
                and set(capture_hashes) == set(CAPTURE_FILES)
                and summary.get("passed") is True
                and raw_bindings_valid
            ),
            "patch_path": str(patch_path.relative_to(artifact_root)),
            "patch_sha256": bytes_sha256(patch),
            "reasoning_effort": metadata.get("reasoning_effort"),
            "returncode": returncode,
            "run_id": item["run_id"],
            "started_monotonic_ns": agent_metrics.get("agent_started_monotonic_ns"),
            "summary_sha256": sha256_file(summary_path) if summary_path.is_file() else None,
            "timed_out": timed_out,
            "untracked_paths": untracked,
            "approved_untracked_paths": approved_untracked,
            "removed_runtime_paths": removed_runtime,
            "workspace": str(workspace.relative_to(ROOT)) if workspace and workspace.is_relative_to(ROOT) else workspace_value,
            "wrapper_ended_monotonic_ns": ended_ns,
            "wrapper_started_monotonic_ns": item["started_monotonic_ns"],
        }
        if case["integration"] == "action":
            result["operation"] = operation
            result["actions"] = (
                operation.get("actions", []) if isinstance(operation, dict) else []
            )
        results.append(result)
    return results


def select_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    passing = [
        candidate
        for candidate in candidates
        if candidate.get("passed") is True
        and isinstance(candidate.get("changed_paths"), list)
        and isinstance(candidate.get("changed_lines"), int)
        and candidate["changed_lines"] >= 0
        and isinstance(candidate.get("duration_seconds"), (int, float))
        and not isinstance(candidate.get("duration_seconds"), bool)
        and float(candidate["duration_seconds"]) > 0
        and isinstance(candidate.get("workspace"), str)
    ]
    if not passing:
        return None
    return min(
        passing,
        key=lambda item: (
            len(item.get("changed_paths", [])),
            int(item.get("changed_lines") or 0),
            float(item.get("duration_seconds") or float("inf")),
            item["id"],
        ),
    )


def final_checks(
    case: dict[str, Any], workspace: Path, fixture_root: Path, capture: Path
) -> dict[str, Any]:
    oracle = run_process(list(case["oracle"]), workspace, timeout=120)
    hidden = run_process(
        [sys.executable, str(fixture_root / "hidden_grade.py"), str(workspace)],
        fixture_root,
        timeout=120,
    )
    local_ci = run_process(
        [sys.executable, "scripts/verify.py", "ci-preflight"],
        workspace,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "EVAL_ACTOR": "orchestrator"},
        timeout=120,
    )
    processes = {"hidden_grade": hidden, "local_ci": local_ci, "oracle": oracle}
    for name, completed in processes.items():
        (capture / f"owner-{name}.stdout").write_text(completed.stdout, encoding="utf-8")
        (capture / f"owner-{name}.stderr").write_text(completed.stderr, encoding="utf-8")
    return {
        "hidden_grade": process_result(hidden),
        "local_ci": process_result(local_ci),
        "oracle": {**process_result(oracle), "argv": list(case["oracle"])},
        "passed": oracle.returncode == hidden.returncode == local_ci.returncode == 0,
    }


def integrate(
    case_id: str,
    case: dict[str, Any],
    selected: dict[str, Any] | None,
    subject: Path,
    campaign_id: str,
    candidates_ended_ns: int,
    artifact_root: Path,
    fixture_path: Path,
) -> dict[str, Any]:
    workspace, capture = materialize_workspace(
        str(case["fixture"]),
        "owner",
        f"{campaign_id}-owner",
        subject_path=subject,
        fixture_path=fixture_path,
    )
    baseline_git = git_state(workspace)
    started_ns = time.monotonic_ns()
    if selected is None:
        result = {
            "capture": str(capture.relative_to(ROOT)),
            "integration": "no-op",
            "passed": False,
            "started_after_candidates": started_ns >= candidates_ended_ns,
            "started_monotonic_ns": started_ns,
            "workspace": str(workspace.relative_to(ROOT)),
        }
        write_json_atomic(capture / "owner.json", result)
        return result

    if case["integration"] == "patch":
        patch_path = artifact_root / selected["patch_path"]
        patch = patch_path.read_bytes()
        applied = apply_patch(workspace, patch)
        if applied.returncode != 0:
            raise RuntimeError(f"owner could not apply selected patch: {applied.stderr}")
        integration = {
            "kind": "patch",
            "patch_sha256": bytes_sha256(patch),
            "selected_patch_sha256": selected["patch_sha256"],
        }
        integrated_patch = patch
    else:
        actions = selected.get("actions", [])
        if len(actions) != 1 or actions[0].get("action") != "resume":
            raise RuntimeError("selected recovery has no single safe resume action")
        inspect = run_process([sys.executable, "scripts/mock_control.py", "inspect"], workspace)
        inspected = json.loads(inspect.stdout)
        expected = inspected["state_sha256"]
        action = run_process(
            [
                sys.executable,
                "scripts/mock_control.py",
                "resume",
                "--expected-state-sha256",
                expected,
            ],
            workspace,
            env={**os.environ, "EVAL_ACTOR": "orchestrator", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if action.returncode != 0:
            raise RuntimeError(f"owner recovery failed: {action.stderr or action.stdout}")
        integration = {
            "action": "resume",
            "expected_state_sha256": expected,
            "kind": "action",
            "mutation_owner": "orchestrator",
            "selected_action_sha256": canonical_sha256(actions[0]),
            "selected_operation_sha256": canonical_sha256(selected["operation"]),
        }
        integrated_patch = None
    checks = final_checks(case, workspace, fixture_path, capture)
    workspace_patch, approved_untracked, removed_runtime, unexpected = complete_patch(
        workspace, selected.get("changed_paths", [])
    )
    if unexpected:
        raise RuntimeError(f"owner has unexpected untracked paths: {unexpected}")
    owner_patch = integrated_patch if integrated_patch is not None else workspace_patch
    owner_patch_path = capture / "owner.patch"
    owner_patch_path.write_bytes(owner_patch)
    ended_ns = time.monotonic_ns()
    result = {
        "baseline_git_state": baseline_git,
        "capture": str(capture.relative_to(ROOT)),
        "checks": checks,
        "ended_monotonic_ns": ended_ns,
        "final_manifest_sha256": canonical_sha256(file_manifest(workspace)),
        "git_state_unchanged": git_state(workspace) == baseline_git,
        "integration": integration,
        "owner_patch_sha256": bytes_sha256(owner_patch),
        "approved_untracked_paths": approved_untracked,
        "removed_runtime_paths": removed_runtime,
        "passed": bool(checks["passed"] and git_state(workspace) == baseline_git),
        "selected": selected["id"],
        "started_after_candidates": started_ns >= candidates_ended_ns,
        "started_monotonic_ns": started_ns,
        "workspace": str(workspace.relative_to(ROOT)),
    }
    if case_id == "authorized-recovery":
        actions = [
            json.loads(line)
            for line in (workspace / "external" / "actions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        state = read_json(workspace / "external" / "state.json")
        result["operation"] = {"actions": actions, "state": state}
        result["passed"] = bool(
            result["passed"]
            and len(actions) == 1
            and actions[0].get("actor") == "orchestrator"
            and state.get("lineage") == "lineage-a"
            and state.get("source_fetches") == 1
        )
    write_json_atomic(capture / "owner.json", result)
    return result


def resolve_artifact_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str):
        raise ValueError(f"{label} path is missing")
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} path escapes artifact root") from exc
    return path


def validate_receipt(value: dict[str, Any], artifact_root: Path | None = None) -> list[str]:
    errors: list[str] = []
    if artifact_root is None:
        return ["artifact root is required"]
    artifact_root = artifact_root.resolve()
    body = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if value.get("receipt_sha256") != canonical_sha256(body):
        errors.append("receipt hash mismatch")
    case_id = value.get("case")
    case = CASES.get(case_id) if isinstance(case_id, str) else None
    if case is None:
        return errors + ["unknown case"]
    fixture_path = artifact_root / "seeded-fixture"
    frozen_path = artifact_root / "frozen.json"
    if not frozen_path.is_file():
        errors.append("frozen contract is missing")
        frozen = {}
    else:
        frozen = read_json(frozen_path)
        if value.get("frozen") != frozen:
            errors.append("receipt frozen contract differs from frozen.json")
        if value.get("frozen_file_sha256") != sha256_file(frozen_path):
            errors.append("frozen contract file hash mismatch")
        frozen_body = {key: item for key, item in frozen.items() if key != "contract_sha256"}
        if frozen.get("contract_sha256") != canonical_sha256(frozen_body):
            errors.append("frozen contract self-hash mismatch")
        for field, path in (
            ("adaptive_runner_sha256", ADAPTIVE_RUNNER),
            ("eval_lib_sha256", EVAL_LIB),
            ("grader_sha256", GRADE_RUN),
            ("runner_sha256", RUN_AGENT),
        ):
            if frozen.get(field) != sha256_file(path):
                errors.append(f"frozen {field} drift")
        if frozen.get("candidate_contract_sha256") != canonical_sha256(
            case["candidates"]
        ):
            errors.append("frozen candidate contract drift")
        if not fixture_path.is_dir() or tree_sha256(fixture_path) != frozen.get(
            "seeded_fixture_sha256"
        ):
            errors.append("seeded fixture drift")
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != len(case["candidates"]):
        errors.append("candidate count differs from frozen case")
        candidates = []
    ids = [candidate.get("id") for candidate in candidates if isinstance(candidate, dict)]
    if len(ids) != len(set(ids)):
        errors.append("candidate ids must be unique")
    frozen_runs = frozen.get("candidate_runs", [])
    if not isinstance(frozen_runs, list) or {
        item.get("id") for item in frozen_runs if isinstance(item, dict)
    } != {item["id"] for item in case["candidates"]}:
        errors.append("frozen candidate runs are incomplete")
        frozen_runs = []
    for candidate in candidates:
        configured = next(
            (
                item
                for item in case["candidates"]
                if item["id"] == candidate.get("id")
            ),
            None,
        )
        if configured is None or candidate.get("model") != configured["model"]:
            errors.append(f"{candidate.get('id')}: model drift")
        if configured is None or candidate.get("reasoning_effort") != configured["reasoning_effort"]:
            errors.append(f"{candidate.get('id')}: reasoning effort drift")
        run_contract = next(
            (
                item
                for item in frozen_runs
                if isinstance(item, dict) and item.get("id") == candidate.get("id")
            ),
            None,
        )
        if run_contract is None:
            errors.append(f"{candidate.get('id')}: frozen run contract missing")
        try:
            capture = (ROOT / str(candidate.get("capture"))).resolve()
            capture.relative_to((ARTIFACTS / "runs").resolve())
        except (TypeError, ValueError):
            errors.append(f"{candidate.get('id')}: invalid capture path")
            continue
        if run_contract is not None and capture != (
            ARTIFACTS / "runs" / str(run_contract.get("run_id"))
        ).resolve():
            errors.append(f"{candidate.get('id')}: capture differs from frozen run")
        hashes = candidate.get("capture_hashes")
        if not isinstance(hashes, dict) or set(hashes) != set(CAPTURE_FILES):
            errors.append(f"{candidate.get('id')}: incomplete capture evidence")
            continue
        for name, expected_hash in hashes.items():
            path = capture / name
            if not path.is_file() or sha256_file(path) != expected_hash:
                errors.append(f"{candidate.get('id')}: capture drift: {name}")
        if all(
            (capture / name).is_file()
            for name in ("agent-metrics.json", "grade.json", "metadata.json", "summary.json")
        ):
            agent_metrics = read_json(capture / "agent-metrics.json")
            grade = read_json(capture / "grade.json")
            metadata = read_json(capture / "metadata.json")
            summary = read_json(capture / "summary.json")
            if metadata.get("model") != candidate.get("model"):
                errors.append(f"{candidate.get('id')}: metadata model drift")
            if metadata.get("reasoning_effort") != candidate.get("reasoning_effort"):
                errors.append(f"{candidate.get('id')}: metadata effort drift")
            if canonical_sha256(metadata.get("subject_tree_manifest")) != frozen.get(
                "subject_sha256"
            ):
                errors.append(f"{candidate.get('id')}: subject package drift")
            if run_contract is not None:
                prompt = metadata.get("prompt")
                fixture_source = metadata.get("fixture_source")
                fixture_manifest = metadata.get("fixture_tree_manifest")
                workspace_id = metadata.get("workspace_id")
                metadata_workspace = metadata.get("workspace")
                candidate_workspace = candidate.get("workspace")
                exact_fields = {
                    "fixture": metadata.get("fixture") == case["fixture"],
                    "model": metadata.get("model") == run_contract.get("model"),
                    "prompt": isinstance(prompt, str)
                    and bytes_sha256(prompt.encode()) == run_contract.get("prompt_sha256"),
                    "prompt context": metadata.get("prompt_context_sha256")
                    == run_contract.get("context_sha256"),
                    "run": metadata.get("run_id") == run_contract.get("run_id")
                    == candidate.get("run_id"),
                    "subject": metadata.get("subject") == run_contract.get("subject"),
                }
                for label, valid in exact_fields.items():
                    if not valid:
                        errors.append(f"{candidate.get('id')}: {label} drift")
                prompt_path = capture / "prompt.txt"
                if (
                    not isinstance(prompt, str)
                    or not prompt_path.is_file()
                    or prompt_path.read_text(encoding="utf-8") != prompt + "\n"
                ):
                    errors.append(f"{candidate.get('id')}: prompt capture drift")
                if (
                    not isinstance(fixture_source, str)
                    or Path(fixture_source).resolve() != fixture_path.resolve()
                    or not isinstance(fixture_manifest, dict)
                    or canonical_sha256(fixture_manifest)
                    != canonical_sha256(tree_manifest(fixture_path))
                ):
                    errors.append(f"{candidate.get('id')}: seeded fixture drift")
                expected_workspace = (
                    ARTIFACTS / "workspaces" / workspace_id
                    if isinstance(workspace_id, str)
                    else None
                )
                if (
                    expected_workspace is None
                    or not isinstance(metadata_workspace, str)
                    or Path(metadata_workspace).resolve() != expected_workspace.resolve()
                    or not isinstance(candidate_workspace, str)
                    or (ROOT / candidate_workspace).resolve() != expected_workspace.resolve()
                ):
                    errors.append(f"{candidate.get('id')}: workspace drift")
            if (
                candidate.get("started_monotonic_ns")
                != agent_metrics.get("agent_started_monotonic_ns")
                or candidate.get("ended_monotonic_ns")
                != agent_metrics.get("agent_ended_monotonic_ns")
            ):
                errors.append(f"{candidate.get('id')}: agent interval drift")
            if candidate.get("duration_seconds") != agent_metrics.get("duration_seconds"):
                errors.append(f"{candidate.get('id')}: duration drift")
            if candidate.get("changed_paths") != grade.get("changed_paths"):
                errors.append(f"{candidate.get('id')}: changed paths drift")
            if case["integration"] == "action" and candidate.get(
                "operation"
            ) != grade.get("runner_observed_operation"):
                errors.append(f"{candidate.get('id')}: recovery operation drift")
            expected_pass = bool(
                candidate.get("returncode") == 0
                and candidate.get("timed_out") is False
                and candidate.get("untracked_paths") == []
                and summary.get("passed") is True
            )
            if candidate.get("passed") is not expected_pass:
                errors.append(f"{candidate.get('id')}: pass claim differs from raw summary")
        try:
            patch_path = resolve_artifact_path(
                artifact_root, candidate.get("patch_path"), "candidate patch"
            )
            patch = patch_path.read_bytes() if patch_path.is_file() else b""
            if not patch_path.is_file() or bytes_sha256(patch) != candidate.get("patch_sha256"):
                errors.append(f"{candidate.get('id')}: patch evidence drift")
            if patch_changed_lines(patch) != candidate.get("changed_lines"):
                errors.append(f"{candidate.get('id')}: changed lines drift")
        except ValueError as exc:
            errors.append(f"{candidate.get('id')}: {exc}")
    if candidates:
        overlap = actual_overlap(candidates)
        if value.get("parallel_overlap") is not overlap or not overlap:
            errors.append("candidate execution did not overlap")
        workspaces = [item.get("workspace") for item in candidates]
        if len(set(workspaces)) != len(workspaces) or not all(
            isinstance(item, str) for item in workspaces
        ):
            errors.append("candidate workspaces are not distinct")
    seeded = value.get("seeded_failure", {})
    if seeded.get("returncode") != 1 or seeded.get("oracle") != frozen.get("oracle"):
        errors.append("seeded failure did not use the frozen oracle or exit normally")
    seed_capture = ROOT / str(seeded.get("capture", "")) / "seeded-failure.json"
    if not seed_capture.is_file() or read_json(seed_capture) != seeded:
        errors.append("seeded failure evidence is missing or differs")
    for stream in ("stdout", "stderr"):
        stream_path = ROOT / str(seeded.get(f"{stream}_path", ""))
        expected = seeded.get(f"{stream}_sha256")
        if not stream_path.is_file() or sha256_file(stream_path) != expected:
            errors.append(f"seeded {stream} evidence drift")
    selected_id = value.get("selected_candidate")
    selected = next((item for item in candidates if item.get("id") == selected_id), None)
    expected = select_candidate(candidates)
    if (expected or {}).get("id") != selected_id:
        errors.append("selected candidate is not the leanest passing candidate")
    owner = value.get("owner", {})
    owner_path = ROOT / str(owner.get("capture", "")) / "owner.json"
    if not owner_path.is_file() or read_json(owner_path) != owner:
        errors.append("owner evidence is missing or differs")
    if selected is None:
        if owner.get("integration") != "no-op" or owner.get("passed") is not False:
            errors.append("unselected owner must be a failed no-op")
        if owner.get("workspace") in [item.get("workspace") for item in candidates]:
            errors.append("owner workspace is not isolated")
        if value.get("passed") is not False:
            errors.append("top-level pass does not match recomputed gates")
        return errors
    owner_capture = owner_path.parent
    checks = owner.get("checks", {})
    for name in ("hidden_grade", "local_ci", "oracle"):
        process = checks.get(name, {}) if isinstance(checks, dict) else {}
        for stream in ("stdout", "stderr"):
            path = owner_capture / f"owner-{name}.{stream}"
            if not path.is_file() or sha256_file(path) != process.get(f"{stream}_sha256"):
                errors.append(f"owner {name} {stream} evidence drift")
        if process.get("returncode") != 0:
            errors.append(f"owner {name} did not pass")
    owner_patch_path = owner_capture / "owner.patch"
    if not owner_patch_path.is_file() or bytes_sha256(owner_patch_path.read_bytes()) != owner.get(
        "owner_patch_sha256"
    ):
        errors.append("owner patch evidence drift")
    if owner.get("workspace") in [item.get("workspace") for item in candidates]:
        errors.append("owner workspace is not isolated")
    if selected is not None:
        if owner.get("selected") != selected_id or owner.get("passed") is not True:
            errors.append("owner did not prove the selected candidate")
        if owner.get("started_after_candidates") is not True:
            errors.append("owner mutated before candidate evaluation completed")
        wrapper_ends = [item.get("wrapper_ended_monotonic_ns") for item in candidates]
        if (
            not all(isinstance(item, int) and not isinstance(item, bool) for item in wrapper_ends)
            or not isinstance(owner.get("started_monotonic_ns"), int)
            or owner["started_monotonic_ns"] < max(wrapper_ends)
        ):
            errors.append("owner start is not bound to completed candidate wrappers")
        if case["integration"] == "patch" and owner.get("integration", {}).get(
            "patch_sha256"
        ) != selected.get("patch_sha256"):
            errors.append("owner patch differs from selected candidate")
        if case["integration"] == "action":
            actions = owner.get("operation", {}).get("actions", [])
            selected_operation = selected.get("operation", {})
            selected_actions = selected_operation.get("actions", [])
            if len(actions) != 1 or actions[0].get("actor") != "orchestrator":
                errors.append("shared operation lacks exactly one orchestrator mutation")
            state = owner.get("operation", {}).get("state", {})
            integration = owner.get("integration", {})
            if (
                actions
                and integration.get("expected_state_sha256")
                != actions[0].get("expected_state_sha256")
            ):
                errors.append("shared operation is not bound to the selected state")
            if (
                len(selected_actions) != 1
                or selected_actions[0].get("action") != "resume"
                or integration.get("expected_state_sha256")
                != selected_actions[0].get("expected_state_sha256")
                or integration.get("selected_action_sha256")
                != canonical_sha256(selected_actions[0])
                or integration.get("selected_operation_sha256")
                != canonical_sha256(selected_operation)
            ):
                errors.append("shared operation differs from selected recovery evidence")
            if state.get("lineage") != "lineage-a" or state.get("source_fetches") != 1:
                errors.append("shared operation violated retry or lineage invariants")
    recomputed_passed = bool(not errors and selected is not None and owner.get("passed") is True)
    if value.get("passed") is not recomputed_passed:
        errors.append("top-level pass does not match recomputed gates")
    return errors


def frozen_contract(case_id: str, case: dict[str, Any], subject: Path) -> dict[str, Any]:
    fixture = FIXTURES / str(case["fixture"])
    value = {
        "adaptive_runner_sha256": sha256_file(ADAPTIVE_RUNNER),
        "case": case_id,
        "candidate_contract_sha256": canonical_sha256(case["candidates"]),
        "eval_lib_sha256": sha256_file(EVAL_LIB),
        "fixture_sha256": tree_sha256(fixture),
        "grader_sha256": sha256_file(GRADE_RUN),
        "oracle": list(case["oracle"]),
        "runner_sha256": sha256_file(RUN_AGENT),
        "subject_sha256": tree_sha256(subject),
    }
    value["contract_sha256"] = canonical_sha256(value)
    return value


def run_case(case_id: str, subject: Path, artifact_root: Path, timeout: int) -> dict[str, Any]:
    case = CASES[case_id]
    artifact_root.mkdir(parents=True, exist_ok=False)
    campaign_id = safe_identifier(artifact_root.name)
    frozen = frozen_contract(case_id, case, subject)
    seeded, fixture_path = run_seeded_failure(
        case_id, case, subject, campaign_id, artifact_root
    )
    frozen["seeded_fixture_sha256"] = tree_sha256(fixture_path)
    frozen["candidate_runs"] = candidate_run_contracts(
        case, campaign_id, fixture_path
    )
    frozen["contract_sha256"] = canonical_sha256(
        {key: item for key, item in frozen.items() if key != "contract_sha256"}
    )
    write_json_atomic(artifact_root / "frozen.json", frozen)
    candidates = launch_candidates(
        case, subject, campaign_id, artifact_root, timeout, fixture_path
    )
    overlap = actual_overlap(candidates)
    selected = select_candidate(candidates)
    owner = integrate(
        case_id,
        case,
        selected,
        subject,
        campaign_id,
        max(item["wrapper_ended_monotonic_ns"] for item in candidates),
        artifact_root,
        fixture_path,
    )
    receipt: dict[str, Any] = {
        "case": case_id,
        "candidates": candidates,
        "evidence_tier": "task-evaluated",
        "frozen": frozen,
        "frozen_file_sha256": sha256_file(artifact_root / "frozen.json"),
        "owner": owner,
        "parallel_overlap": overlap,
        "passed": False,
        "schema_version": 1,
        "seeded_failure": seeded,
        "selected_candidate": selected["id"] if selected else None,
    }
    receipt["passed"] = bool(
        seeded["returncode"] != 0 and overlap and selected is not None and owner.get("passed") is True
    )
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    errors = validate_receipt(receipt, artifact_root)
    if errors:
        receipt["passed"] = False
        receipt["validation_errors"] = errors
        receipt["receipt_sha256"] = canonical_sha256(
            {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        )
    write_json_atomic(artifact_root / "adaptive-replan.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(CASES))
    parser.add_argument("--subject-path", type=Path, default=ROOT / "endurant-harness")
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("adaptive process-group evaluation currently requires POSIX")
    if args.verify:
        receipt_path = args.verify.resolve()
        receipt = read_json(receipt_path)
        errors = validate_receipt(receipt, receipt_path.parent)
        print(json.dumps({"errors": errors, "passed": not errors}, indent=2, sort_keys=True))
        return 0 if not errors else 1
    if not args.case:
        parser.error("--case is required unless --verify is used")
    subject = args.subject_path.resolve()
    case = CASES[args.case]
    frozen = frozen_contract(args.case, case, subject)
    if args.dry_run:
        print(json.dumps({"candidates": case["candidates"], "frozen": frozen}, indent=2, sort_keys=True))
        return 0
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    artifact_root = (
        args.artifact_root.resolve()
        if args.artifact_root
        else ARTIFACTS / "adaptive" / f"{stamp}-{args.case}"
    )
    receipt = run_case(args.case, subject, artifact_root, args.timeout)
    print(json.dumps({**receipt, "candidates": [], "owner": {}}, indent=2, sort_keys=True))
    print(f"receipt={artifact_root / 'adaptive-replan.json'}")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
