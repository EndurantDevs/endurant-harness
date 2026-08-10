#!/usr/bin/env python3
"""Run one isolated Codex fixture arm and capture provider-authoritative evidence."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from eval_lib import (
    ARTIFACTS,
    FIXTURES,
    canonical_prompt,
    file_manifest,
    git_state,
    manifest_delta,
    materialize_workspace,
    read_json,
    run_process,
    sha256_file,
    tree_manifest,
    write_json,
)


CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
INSTALLED_SKILL = str(CODEX_HOME / "skills" / "endurant-harness" / "SKILL.md")


def scoped_manifest(manifest: dict[str, str], prefixes: list[str]) -> dict[str, str]:
    return {
        path: digest
        for path, digest in manifest.items()
        if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)
    }


def observe_agent_events(
    source: Path, observed: Path, prior_lines: list[bytes]
) -> tuple[list[bytes], bool]:
    """Copy new complete agent event lines with runner-owned observation times."""
    if source.is_symlink():
        return prior_lines, True
    if not source.exists():
        return prior_lines, bool(prior_lines)
    try:
        raw = source.read_bytes()
    except OSError:
        return prior_lines, True
    complete_end = raw.rfind(b"\n") + 1
    complete_lines = raw[:complete_end].splitlines() if complete_end else []
    if (
        len(complete_lines) < len(prior_lines)
        or complete_lines[: len(prior_lines)] != prior_lines
    ):
        return prior_lines, True
    new_lines = complete_lines[len(prior_lines) :]
    if new_lines:
        with observed.open("a", encoding="utf-8") as handle:
            for sequence, line in enumerate(new_lines, start=len(prior_lines)):
                try:
                    event: Any = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    event = {"raw": line.decode("utf-8", errors="replace")}
                handle.write(
                    json.dumps(
                        {
                            "sequence": sequence,
                            "observed_monotonic_ns": time.monotonic_ns(),
                            "event": event,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            handle.flush()
    return complete_lines, False


def codex_argv(
    workspace: Path,
    capture: Path,
    event_sink: Path,
    model: str,
    reasoning_effort: str,
) -> list[str]:
    disabled_skill = f'[{{path="{INSTALLED_SKILL}",enabled=false}}]'
    return [
        "codex",
        "exec",
        "--strict-config",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--json",
        "-C",
        str(workspace),
        "-m",
        model,
        "-s",
        "workspace-write",
        "-o",
        str(capture / "final.txt"),
        "-c",
        'approval_policy="never"',
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "-c",
        'model_verbosity="low"',
        "-c",
        "agents.enabled=false",
        "-c",
        'web_search="disabled"',
        "-c",
        "sandbox_workspace_write.network_access=false",
        "-c",
        f'sandbox_workspace_write.writable_roots=["{event_sink}"]',
        "-c",
        'history.persistence="none"',
        "-c",
        "memories.use_memories=false",
        "-c",
        "memories.generate_memories=false",
        "-c",
        "features.skill_mcp_dependency_install=false",
        "-c",
        f"skills.config={disabled_skill}",
        "-",
    ]


def collect_metrics(raw_jsonl: Path, timed_jsonl: Path, started_ns: int) -> dict[str, Any]:
    usage: dict[str, int] = {}
    item_ids: set[str] = set()
    item_counts: dict[str, int] = {}
    commands: list[str] = []
    first_tool_ns: int | None = None
    turn_status = "unknown"

    timed_by_line: list[dict[str, Any]] = []
    if timed_jsonl.is_file():
        for line in timed_jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                timed_by_line.append(value)

    for wrapped in timed_by_line:
        event = wrapped.get("event")
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", ""))
        if event_type == "turn.completed":
            turn_status = "completed"
            payload = event.get("usage")
            if not isinstance(payload, dict) and isinstance(event.get("turn"), dict):
                payload = event["turn"].get("usage")
            if isinstance(payload, dict):
                for key, value in payload.items():
                    if isinstance(value, int) and not isinstance(value, bool):
                        usage[key] = value
        elif event_type == "turn.failed":
            turn_status = "failed"
        if not event_type.startswith("item."):
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", ""))
        item_type = str(item.get("type", "unknown"))
        if item_id and item_id in item_ids:
            continue
        if item_id:
            item_ids.add(item_id)
        item_counts[item_type] = item_counts.get(item_type, 0) + 1
        if item_type in {"command_execution", "file_change"} and first_tool_ns is None:
            observed = wrapped.get("observed_monotonic_ns")
            if isinstance(observed, int):
                first_tool_ns = observed
        if item_type == "command_execution":
            command = item.get("command")
            if isinstance(command, str):
                commands.append(command)

    input_tokens = usage.get("input_tokens")
    cached_tokens = usage.get("cached_input_tokens")
    uncached = None
    if isinstance(input_tokens, int) and isinstance(cached_tokens, int):
        uncached = max(input_tokens - cached_tokens, 0)
    return {
        "turn_status": turn_status,
        "usage": usage,
        "uncached_input_tokens": uncached,
        "unique_item_count": len(item_ids),
        "item_counts": item_counts,
        "commands": commands,
        "time_to_first_tool_seconds": (
            round((first_tool_ns - started_ns) / 1_000_000_000, 6)
            if first_tool_ns is not None
            else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    run_id = f"{stamp}-{args.subject}-{args.fixture}-r{args.repeat}"
    workspace_id = secrets.token_hex(10)
    event_run_id = secrets.token_hex(16)
    workspace, capture = materialize_workspace(
        args.fixture, args.subject, run_id, workspace_id=workspace_id
    )
    prompt = canonical_prompt(args.fixture)
    event_sink = ARTIFACTS / "event-sinks" / event_run_id
    event_sink.mkdir(parents=True, exist_ok=False)
    agent_event_log = event_sink / "verification-events.jsonl"
    orchestrator_event_log = capture / "orchestrator-events.jsonl"
    fixture_config = read_json(FIXTURES / args.fixture / "fixture.json")
    subject_skill = workspace / ".agents" / "skills" / "endurant-harness" / "SKILL.md"
    subject_root = subject_skill.parent

    environment = {
        **os.environ,
        "EVAL_EVENT_LOG": str(agent_event_log),
        "EVAL_RUN_ID": event_run_id,
        "EVAL_ACTOR": "agent",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if fixture_config.get("kind") == "performance":
        baseline = run_process(
            [sys.executable, "scripts/verify.py", "synthetic"],
            workspace,
            env={
                **environment,
                "EVAL_ACTOR": "orchestrator",
                "EVAL_EVENT_LOG": str(orchestrator_event_log),
            },
            timeout=120,
        )
        (capture / "baseline-benchmark.stdout").write_text(baseline.stdout, encoding="utf-8")
        (capture / "baseline-benchmark.stderr").write_text(baseline.stderr, encoding="utf-8")
        if baseline.returncode != 0:
            raise RuntimeError(f"fixture baseline benchmark failed: {baseline.stderr}")

    baseline_manifest = file_manifest(workspace)
    production_prefixes = list(fixture_config.get("production_prefixes", ["src/"]))
    baseline_production_manifest = scoped_manifest(baseline_manifest, production_prefixes)
    write_json(capture / "baseline-manifest.json", baseline_manifest)
    argv = codex_argv(workspace, capture, event_sink, args.model, args.reasoning_effort)
    metadata = {
        "run_id": run_id,
        "event_run_id": event_run_id,
        "workspace_id": workspace_id,
        "fixture": args.fixture,
        "subject": args.subject,
        "repeat": args.repeat,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "codex_version": run_process(["codex", "--version"], workspace).stdout.strip(),
        "subject_skill_sha256": sha256_file(subject_skill),
        "subject_tree_manifest": tree_manifest(subject_root),
        "baseline_git_state": git_state(workspace),
        "production_prefixes": production_prefixes,
        "prompt": prompt,
        "argv": argv,
        "workspace": str(workspace),
    }
    write_json(capture / "metadata.json", metadata)
    (capture / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    if args.prepare_only:
        try:
            event_sink.rmdir()
        except OSError:
            pass
        print(json.dumps(metadata, sort_keys=True))
        return 0

    raw_stdout = capture / "codex.jsonl"
    timed_stdout = capture / "codex-observed.jsonl"
    raw_stderr = capture / "codex.stderr"
    observed_agent_events = capture / "agent-events-observed.jsonl"
    agent_started_timestamp_ns = time.time_ns()
    started = time.monotonic()
    started_ns = time.monotonic_ns()
    metadata["agent_started_timestamp_ns"] = agent_started_timestamp_ns
    metadata["agent_started_monotonic_ns"] = started_ns
    write_json(capture / "metadata.json", metadata)
    proc = subprocess.Popen(
        argv,
        cwd=str(workspace),
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
        bufsize=1,
    )
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    proc.stdin.write(prompt)
    proc.stdin.close()

    def read_stdout() -> None:
        with raw_stdout.open("w", encoding="utf-8") as raw, timed_stdout.open(
            "w", encoding="utf-8"
        ) as timed:
            for line in proc.stdout:
                raw.write(line)
                raw.flush()
                try:
                    event: Any = json.loads(line)
                except json.JSONDecodeError:
                    event = {"raw": line.rstrip("\n")}
                timed.write(
                    json.dumps(
                        {"observed_monotonic_ns": time.monotonic_ns(), "event": event},
                        sort_keys=True,
                    )
                    + "\n"
                )
                timed.flush()

    def read_stderr() -> None:
        with raw_stderr.open("w", encoding="utf-8") as handle:
            for line in proc.stderr:
                handle.write(line)
                handle.flush()

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    first_edit_seconds: float | None = None
    first_edit_timestamp_ns: int | None = None
    first_edit_monotonic_ns: int | None = None
    observed_event_lines: list[bytes] = []
    agent_event_log_tampered = False
    timed_out = False
    while proc.poll() is None:
        if first_edit_seconds is None:
            current_manifest = scoped_manifest(file_manifest(workspace), production_prefixes)
            if manifest_delta(baseline_production_manifest, current_manifest) != {
                "added": [],
                "removed": [],
                "modified": [],
            }:
                first_edit_seconds = round(time.monotonic() - started, 6)
                first_edit_timestamp_ns = time.time_ns()
                first_edit_monotonic_ns = time.monotonic_ns()
        observed_event_lines, newly_tampered = observe_agent_events(
            agent_event_log, observed_agent_events, observed_event_lines
        )
        agent_event_log_tampered = agent_event_log_tampered or newly_tampered
        if time.monotonic() - started > args.timeout:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            break
        time.sleep(0.05)

    exit_code = proc.wait()
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    duration = round(time.monotonic() - started, 6)
    if first_edit_seconds is None:
        current_manifest = scoped_manifest(file_manifest(workspace), production_prefixes)
        if manifest_delta(baseline_production_manifest, current_manifest) != {
            "added": [],
            "removed": [],
            "modified": [],
        }:
            first_edit_seconds = duration
            first_edit_timestamp_ns = time.time_ns()
            first_edit_monotonic_ns = time.monotonic_ns()
    observed_event_lines, newly_tampered = observe_agent_events(
        agent_event_log, observed_agent_events, observed_event_lines
    )
    agent_event_log_tampered = agent_event_log_tampered or newly_tampered
    archived_agent_events = capture / "agent-events.jsonl"
    if agent_event_log.is_file() and not agent_event_log.is_symlink():
        shutil.move(str(agent_event_log), str(archived_agent_events))
    elif agent_event_log.exists() or agent_event_log.is_symlink():
        agent_event_log_tampered = True
    try:
        event_sink.rmdir()
    except OSError:
        pass
    metrics = collect_metrics(raw_stdout, timed_stdout, started_ns)
    metrics.update(
        {
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": duration,
            "time_to_first_edit_seconds": first_edit_seconds,
            "agent_started_timestamp_ns": agent_started_timestamp_ns,
            "agent_started_monotonic_ns": started_ns,
            "first_edit_timestamp_ns": first_edit_timestamp_ns,
            "first_edit_monotonic_ns": first_edit_monotonic_ns,
            "agent_event_log_tampered": agent_event_log_tampered,
            "subject_skill_unchanged": sha256_file(subject_skill)
            == metadata["subject_skill_sha256"],
            "subject_tree_unchanged": tree_manifest(subject_root)
            == metadata["subject_tree_manifest"],
        }
    )
    write_json(capture / "agent-metrics.json", metrics)

    grade = run_process(
        [
            sys.executable,
            str(Path(__file__).with_name("grade_run.py")),
            "--fixture",
            args.fixture,
            "--workspace",
            str(workspace),
            "--capture",
            str(capture),
        ],
        Path(__file__).parent,
        timeout=180,
    )
    (capture / "grade.stdout").write_text(grade.stdout, encoding="utf-8")
    (capture / "grade.stderr").write_text(grade.stderr, encoding="utf-8")
    summary = {
        "run_id": run_id,
        "agent": metrics,
        "grade_exit_code": grade.returncode,
        "grade": read_json(capture / "grade.json") if (capture / "grade.json").is_file() else None,
    }
    summary["passed"] = bool(
        exit_code == 0
        and not timed_out
        and metrics.get("turn_status") == "completed"
        and metrics.get("subject_tree_unchanged") is True
        and grade.returncode == 0
    )
    write_json(capture / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
