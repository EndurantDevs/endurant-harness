#!/usr/bin/env python3
"""Run the bounded Max/Ultra/selective-delegation Harness screening."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from eval_lib import canonical_sha256, read_json, sha256_file, tree_manifest, write_json


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "lab" / "run_agent.py"
GRADER = ROOT / "lab" / "grade_run.py"
PROMPT = ROOT / "lab" / "prompts" / "max-selective-delegation.txt"
SUBJECT = ROOT / "endurant-harness"
MODEL = "gpt-5.6-sol"
FIXTURES = (
    "settings-override-correctness",
    "record-selection-performance",
    "authorized-recovery",
)
ARMS = (
    {"name": "max-harness-control", "reasoning_effort": "max", "prompt": None},
    {"name": "ultra-harness-control", "reasoning_effort": "ultra", "prompt": None},
    {
        "name": "max-selective-overlay",
        "reasoning_effort": "max",
        "prompt": str(PROMPT.relative_to(ROOT)),
    },
)


class ExperimentError(RuntimeError):
    pass


def _run(argv: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def resolve_binary(raw: str) -> Path:
    candidate = Path(raw).expanduser()
    resolved = candidate.resolve() if candidate.exists() else None
    if resolved is None:
        found = shutil.which(raw)
        resolved = Path(found).resolve() if found else None
    if resolved is None or not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ExperimentError(f"Codex binary is not executable: {raw}")
    return resolved


def schedule(repeats: int) -> list[dict[str, Any]]:
    if repeats < 1:
        raise ExperimentError("repeats must be at least 1")
    rows: list[dict[str, Any]] = []
    for repeat in range(1, repeats + 1):
        for fixture_index, fixture in enumerate(FIXTURES):
            shift = (repeat - 1 + fixture_index) % len(ARMS)
            ordered = ARMS[shift:] + ARMS[:shift]
            for position, arm in enumerate(ordered, start=1):
                rows.append(
                    {
                        "arm": arm["name"],
                        "fixture": fixture,
                        "position": position,
                        "repeat": repeat,
                    }
                )
    return rows


def _git_text(*args: str) -> str:
    completed = _run(["git", *args])
    if completed.returncode:
        raise ExperimentError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def build_contract(binary: Path, repeats: int, timeout: int) -> dict[str, Any]:
    if timeout < 1:
        raise ExperimentError("timeout must be at least 1 second")
    version = _run([str(binary), "--version"])
    if version.returncode:
        raise ExperimentError(version.stderr.strip() or "Codex version probe failed")
    contract: dict[str, Any] = {
        "schema_version": 1,
        "evidence_tier": "screening",
        "question": "Does an explicit selective-delegation overlay improve the current Harness?",
        "interpretation_limits": [
            "all arms already contain the current Harness delegation policy",
            "one repeat is a Latin-square screen without a noise estimate",
            "root JSONL cannot observe nested agents or child token usage",
        ],
        "source_commit": _git_text("rev-parse", "HEAD"),
        "source_status": _git_text("status", "--short"),
        "codex": {
            "path": str(binary),
            "sha256": sha256_file(binary),
            "version": version.stdout.strip(),
        },
        "python": {"path": sys.executable, "version": sys.version.split()[0]},
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "model": MODEL,
        "provider": "openai direct via isolated --ignore-user-config run",
        "timeout_seconds": timeout,
        "repeats": repeats,
        "usage_scope": "root thread reported by codex exec; child-thread usage is not included",
        "arms": [
            {
                **arm,
                "subagents": "enabled",
                "prompt_sha256": sha256_file(PROMPT) if arm["prompt"] else None,
            }
            for arm in ARMS
        ],
        "schedule": schedule(repeats),
        "inputs": {
            "runner_sha256": sha256_file(RUNNER),
            "grader_sha256": sha256_file(GRADER),
            "subject_tree_sha256": canonical_sha256(tree_manifest(SUBJECT)),
            "fixtures": {
                fixture: canonical_sha256(tree_manifest(ROOT / "fixtures" / fixture))
                for fixture in FIXTURES
            },
        },
    }
    contract["contract_sha256"] = canonical_sha256(contract)
    return contract


def validate_contract(contract: dict[str, Any], binary: Path) -> None:
    current = build_contract(
        binary,
        int(contract["repeats"]),
        int(contract["timeout_seconds"]),
    )
    if current != contract:
        raise ExperimentError("frozen experiment inputs drifted")
    if contract["source_status"]:
        raise ExperimentError("execute from a clean worktree")


def delegation_metrics(path: Path) -> dict[str, Any]:
    calls: dict[str, set[str]] = {}
    children: set[str] = set()
    failed: set[str] = set()
    final_states: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or not str(event.get("type", "")).startswith(
                "item."
            ):
                continue
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") != "collab_tool_call":
                continue
            item_id = str(item.get("id", ""))
            tool = str(item.get("tool", "unknown"))
            if item_id:
                calls.setdefault(tool, set()).add(item_id)
                if item.get("status") == "failed":
                    failed.add(item_id)
            receiver_thread_ids = item.get("receiver_thread_ids")
            if tool == "spawn_agent" and isinstance(receiver_thread_ids, list):
                children.update(
                    thread_id
                    for thread_id in receiver_thread_ids
                    if isinstance(thread_id, str) and thread_id
                )
            states = item.get("agents_states")
            if isinstance(states, dict):
                for thread_id, state in states.items():
                    if isinstance(thread_id, str) and isinstance(state, dict):
                        if tool == "spawn_agent":
                            children.add(thread_id)
                        status = state.get("status")
                        if isinstance(status, str):
                            final_states[thread_id] = status
    terminal_states = {"completed", "shutdown"}
    return {
        "calls_by_tool": {tool: len(ids) for tool, ids in sorted(calls.items())},
        "failed_call_count": len(failed),
        "root_observed_spawned_agent_count": len(children),
        "root_observed_child_thread_ids": sorted(children),
        "last_observed_agent_states": dict(sorted(final_states.items())),
        "all_root_observed_children_terminal": all(
            final_states.get(thread_id) in terminal_states for thread_id in children
        ),
        "nested_delegation_observable": False,
        "child_model_effort_available": False,
        "child_token_usage_available": False,
        "total_usage_exact": False,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for arm in (item["name"] for item in ARMS):
        selected = [row for row in rows if row["arm"] == arm]
        durations = [
            float(row["duration_seconds"])
            for row in selected
            if isinstance(row.get("duration_seconds"), (int, float))
        ]
        usage_totals = [
            int(row["root_reported_usage"]["input_tokens"])
            + int(row["root_reported_usage"]["output_tokens"])
            for row in selected
            if isinstance(row.get("root_reported_usage"), dict)
            and isinstance(row["root_reported_usage"].get("input_tokens"), int)
            and not isinstance(row["root_reported_usage"].get("input_tokens"), bool)
            and isinstance(row["root_reported_usage"].get("output_tokens"), int)
            and not isinstance(row["root_reported_usage"].get("output_tokens"), bool)
        ]
        result[arm] = {
            "passed": sum(bool(row["passed"]) for row in selected),
            "functional_passed": sum(bool(row["functional_passed"]) for row in selected),
            "runs": len(selected),
            "median_duration_seconds": statistics.median(durations) if durations else None,
            "median_root_reported_tokens": statistics.median(usage_totals)
            if usage_totals
            else None,
            "root_usage_available": len(usage_totals),
            "root_usage_missing": len(selected) - len(usage_totals),
            "root_observed_spawned_agents": sum(
                row["delegation"]["root_observed_spawned_agent_count"] for row in selected
            ),
        }
    return result


def capture_hashes(capture: Path) -> dict[str, str | None]:
    return {
        name: sha256_file(capture / name) if (capture / name).is_file() else None
        for name in (
            "metadata.json",
            "agent-metrics.json",
            "summary.json",
            "grade.json",
            "codex.jsonl",
            "codex.stderr",
        )
    }


def observed_policy_pass(arm: str, fixture: str, delegation: dict[str, Any]) -> bool:
    if arm != "max-selective-overlay":
        return True
    maximum = 2 if fixture == "record-selection-performance" else 0
    return bool(
        delegation["root_observed_spawned_agent_count"] <= maximum
        and delegation["failed_call_count"] == 0
        and delegation["all_root_observed_children_terminal"] is True
    )


def execute(contract: dict[str, Any], binary: Path, output_dir: Path) -> dict[str, Any]:
    validate_contract(contract, binary)
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "contract.json", contract)
    env = {**os.environ, "PATH": f"{binary.parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    selected = shutil.which("codex", path=env["PATH"])
    if selected is None or not Path(selected).samefile(binary):
        raise ExperimentError("PATH does not select the frozen Codex binary")
    arm_by_name = {arm["name"]: arm for arm in ARMS}
    campaign_id = f"delegation-{contract['contract_sha256'][:12]}-{time.time_ns()}"
    rows: list[dict[str, Any]] = []
    for slot, planned in enumerate(contract["schedule"], start=1):
        validate_contract(contract, binary)
        arm = arm_by_name[planned["arm"]]
        load_before = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
        run_id = (
            f"{campaign_id}-{slot:02d}-r{planned['repeat']}-"
            f"{planned['fixture']}-{planned['arm']}"
        )
        argv = [
            sys.executable,
            "-S",
            str(RUNNER),
            "--fixture",
            planned["fixture"],
            "--subject-path",
            str(SUBJECT),
            "--subject-label",
            "endurant-harness-delegation",
            "--repeat",
            str(planned["repeat"]),
            "--model",
            MODEL,
            "--reasoning-effort",
            arm["reasoning_effort"],
            "--subagents",
            "enabled",
            "--timeout",
            str(contract["timeout_seconds"]),
            "--run-id",
            run_id,
        ]
        if arm["prompt"]:
            argv.extend(["--prompt-context-file", str(PROMPT)])
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        (output_dir / f"{slot:02d}.stdout").write_text(completed.stdout, encoding="utf-8")
        (output_dir / f"{slot:02d}.stderr").write_text(completed.stderr, encoding="utf-8")
        load_after = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
        capture = ROOT / "artifacts" / "runs" / run_id
        summary_path = capture / "summary.json"
        if not summary_path.is_file():
            raise ExperimentError(f"run produced no summary: {run_id}")
        summary = read_json(summary_path)
        metadata = read_json(capture / "metadata.json")
        expected_prompt_hash = sha256_file(PROMPT) if arm["prompt"] else None
        if (
            metadata.get("codex_version") != contract["codex"]["version"]
            or metadata.get("model") != MODEL
            or metadata.get("reasoning_effort") != arm["reasoning_effort"]
            or metadata.get("subagents") != "enabled"
            or metadata.get("prompt_context_sha256") != expected_prompt_hash
        ):
            raise ExperimentError(f"run configuration drifted: {run_id}")
        agent = summary.get("agent") if isinstance(summary.get("agent"), dict) else {}
        delegation = delegation_metrics(capture / "codex.jsonl")
        functional_passed = summary.get("passed") is True
        policy_passed = observed_policy_pass(
            planned["arm"], planned["fixture"], delegation
        )
        row = {
            **planned,
            "run_id": run_id,
            "process_exit_code": completed.returncode,
            "functional_passed": functional_passed,
            "policy_observed_pass": policy_passed,
            "passed": functional_passed and policy_passed,
            "duration_seconds": agent.get("duration_seconds"),
            "root_reported_usage": agent.get("usage", {}),
            "delegation": delegation,
            "host_load_average_before": load_before,
            "host_load_average_after": load_after,
            "capture": str(capture),
            "capture_hashes": capture_hashes(capture),
            "runner_stdout_sha256": sha256_file(output_dir / f"{slot:02d}.stdout"),
            "runner_stderr_sha256": sha256_file(output_dir / f"{slot:02d}.stderr"),
        }
        rows.append(row)
        partial = {
            "contract_sha256": contract["contract_sha256"],
            "runs": rows,
            "summary": summarize(rows),
            "status": "running",
        }
        write_json(output_dir / "result.json", partial)
        print(json.dumps(row, sort_keys=True), flush=True)
    result = {
        "contract_sha256": contract["contract_sha256"],
        "runs": rows,
        "summary": summarize(rows),
        "status": "passed" if all(row["passed"] for row in rows) else "failed",
    }
    result["result_sha256"] = canonical_sha256(result)
    write_json(output_dir / "result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex-binary",
        default="/Applications/ChatGPT.app/Contents/Resources/codex",
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    try:
        binary = resolve_binary(args.codex_binary)
        contract = build_contract(binary, args.repeats, args.timeout)
        if not args.execute:
            print(json.dumps(contract, indent=2, sort_keys=True))
            return 0
        output_dir = args.output_dir or (
            ROOT
            / "artifacts"
            / "runtime"
            / time.strftime("delegation-screen-%Y%m%dT%H%M%SZ", time.gmtime())
        )
        result = execute(contract, binary, output_dir.resolve())
    except (ExperimentError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "status": "failed"}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
