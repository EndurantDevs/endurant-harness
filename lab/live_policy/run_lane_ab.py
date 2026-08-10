#!/usr/bin/env python3
"""Run and externally score a bounded live lane-classification A/B."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = ROOT / "lab" / "evals" / "lane-cases.json"
DEFAULT_SKILL = (
    ROOT / "subjects" / "combined-candidate" / "endurant-harness" / "SKILL.md"
)
OUTPUT_SCHEMA = Path(__file__).with_name("lane-output.schema.json")
INSTALLED_SKILL = (
    Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    / "skills"
    / "endurant-harness"
    / "SKILL.md"
)

CURRENT_DIRECT = """### Direct lane

For clear reversible work, inspect instructions, dirty edits, direct path, predicted files, and checks. Edit once; run focused behavior, available local CI, and diff. Skip probe, hypothesis, analogy, checkpoint, JSON plan, subagent, and broad suites. Escalate for uncertainty, contradictions, coupling, performance/efficiency, migrations, security, deployment, or material risk.
"""

CANDIDATE_DIRECT = """### Direct lane

Classify silently. Use the direct lane only when every condition is established: the requested behavior is clear; the target is internal or private; the change is confined to one module or package with a predictable source-and-test surface; the edit is reversible; and a focused behavior check is known. Otherwise use the escalated lane.

Always escalate uncertainty or contradictory evidence; cross-package coupling; performance or resource claims; migrations or persisted-data compatibility; security, authentication, or authorization; concurrency or flaky failures; deployment, shared configuration, dependencies, generated clients, packaging, or platform behavior; public APIs or external protocols; destructive cleanup; and any material risk.

For qualifying direct work, inspect instructions, dirty edits, direct path, predicted files, and checks. Edit once; run focused behavior, available local CI, and diff. Skip probe, hypothesis, analogy, checkpoint, JSON plan, subagent, and broad suites. Do not emit a classification rationale or add a separate classification turn.
"""


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def candidate_policy(current: str) -> str:
    occurrences = current.count(CURRENT_DIRECT)
    if occurrences != 1:
        raise ValueError(
            "combined candidate direct-lane text changed; "
            f"expected one exact section, found {occurrences}"
        )
    return current.replace(CURRENT_DIRECT, CANDIDATE_DIRECT)


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = read_object(path)
    if payload.get("schema_version") != 1:
        raise ValueError("lane case schema_version must be 1")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 40:
        raise ValueError("lane case corpus must contain exactly 40 cases")
    seen: set[str] = set()
    direct = 0
    escalated = 0
    result: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case {index} must be an object")
        case_id = case.get("id")
        lane = case.get("expected_lane")
        task = case.get("task")
        tags = case.get("tags")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError(f"case {index} has an invalid or duplicate id")
        if lane not in {"direct", "escalated"}:
            raise ValueError(f"case {case_id} has an invalid expected_lane")
        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"case {case_id} has no task")
        if not isinstance(tags, list) or not tags or not all(
            isinstance(tag, str) and tag for tag in tags
        ):
            raise ValueError(f"case {case_id} has invalid tags")
        seen.add(case_id)
        direct += lane == "direct"
        escalated += lane == "escalated"
        result.append(dict(case))
    if direct != 20 or escalated != 20:
        raise ValueError("lane corpus must contain 20 direct and 20 escalated cases")
    return result


def make_prompt(policy: str, cases: list[dict[str, Any]], seed: int) -> str:
    shuffled = list(cases)
    random.Random(seed).shuffle(shuffled)
    tasks = "\n".join(f"{case['id']}\t{case['task']}" for case in shuffled)
    return f"""You are running a classification-only evaluation of an engineering workflow policy.

Apply the policy independently to every task. Choose `direct` only when the policy permits the direct lane; otherwise choose `escalated`. Do not solve any task, inspect files, call tools, or provide reasoning. Return exactly one classification for every supplied ID using the required JSON schema.

<policy>
{policy.rstrip()}
</policy>

<tasks>
{tasks}
</tasks>
"""


def codex_argv(
    workspace: Path,
    final_path: Path,
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
        "--output-schema",
        str(OUTPUT_SCHEMA),
        "-C",
        str(workspace),
        "-m",
        model,
        "-s",
        "read-only",
        "-o",
        str(final_path),
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


def event_metrics(path: Path) -> dict[str, Any]:
    usage: dict[str, int] = {}
    item_ids: set[str] = set()
    tool_items = 0
    thread_id: str | None = None
    turn_status = "unknown"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "thread.started" and isinstance(event.get("thread_id"), str):
            thread_id = event["thread_id"]
        elif event_type == "turn.completed":
            turn_status = "completed"
            payload = event.get("usage")
            if isinstance(payload, dict):
                usage = {
                    key: value
                    for key, value in payload.items()
                    if isinstance(value, int) and not isinstance(value, bool)
                }
        elif event_type == "turn.failed":
            turn_status = "failed"
        if not isinstance(event_type, str) or not event_type.startswith("item."):
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            if item_id in item_ids:
                continue
            item_ids.add(item_id)
        if item.get("type") in {
            "command_execution",
            "file_change",
            "mcp_tool_call",
            "web_search",
        }:
            tool_items += 1
    input_tokens = usage.get("input_tokens")
    cached_tokens = usage.get("cached_input_tokens")
    uncached = None
    if isinstance(input_tokens, int) and isinstance(cached_tokens, int):
        uncached = max(input_tokens - cached_tokens, 0)
    return {
        "thread_id": thread_id,
        "turn_status": turn_status,
        "usage": usage,
        "uncached_input_tokens": uncached,
        "tool_items": tool_items,
    }


def parse_predictions(path: Path) -> tuple[list[dict[str, str]], str | None]:
    try:
        payload = read_object(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [], str(exc)
    values = payload.get("classifications")
    if not isinstance(values, list):
        return [], "classifications is not an array"
    predictions: list[dict[str, str]] = []
    for value in values:
        if not isinstance(value, dict):
            return [], "classification is not an object"
        case_id = value.get("id")
        lane = value.get("lane")
        if (
            not isinstance(case_id, str)
            or not isinstance(lane, str)
            or lane not in {"direct", "escalated"}
        ):
            return [], "classification has an invalid id or lane"
        predictions.append({"id": case_id, "lane": lane})
    return predictions, None


def score_predictions(
    cases: list[dict[str, Any]], predictions: list[dict[str, str]]
) -> dict[str, Any]:
    expected = {case["id"]: case["expected_lane"] for case in cases}
    counts: dict[str, int] = {}
    predicted: dict[str, str] = {}
    unknown_ids: list[str] = []
    invalid_predictions = 0
    for item in predictions:
        if not isinstance(item, dict):
            invalid_predictions += 1
            continue
        case_id = item.get("id")
        lane = item.get("lane")
        if (
            not isinstance(case_id, str)
            or not isinstance(lane, str)
            or lane not in {"direct", "escalated"}
        ):
            invalid_predictions += 1
            continue
        counts[case_id] = counts.get(case_id, 0) + 1
        if case_id not in expected:
            unknown_ids.append(case_id)
        elif case_id not in predicted:
            predicted[case_id] = lane
    duplicate_ids = sorted(case_id for case_id, count in counts.items() if count != 1)
    missing_ids = sorted(set(expected) - set(predicted))
    invalid = bool(
        invalid_predictions
        or unknown_ids
        or duplicate_ids
        or missing_ids
        or len(predictions) != len(cases)
    )
    correct_ids = sorted(
        case_id
        for case_id, expected_lane in expected.items()
        if not invalid and predicted.get(case_id) == expected_lane
    )
    direct_ids = {case_id for case_id, lane in expected.items() if lane == "direct"}
    hazardous_ids = {case_id for case_id, lane in expected.items() if lane == "escalated"}
    direct_correct = len(direct_ids & set(correct_ids))
    hazardous_correct = len(hazardous_ids & set(correct_ids))
    incorrect = sorted(set(expected) - set(correct_ids))
    return {
        "valid": not invalid,
        "prediction_count": len(predictions),
        "correct": len(correct_ids),
        "total": len(cases),
        "accuracy": round(len(correct_ids) / len(cases), 6),
        "direct_correct": direct_correct,
        "direct_total": len(direct_ids),
        "direct_recall": round(direct_correct / len(direct_ids), 6),
        "hazardous_correct": hazardous_correct,
        "hazardous_total": len(hazardous_ids),
        "hazardous_recall": round(hazardous_correct / len(hazardous_ids), 6),
        "incorrect_ids": incorrect,
        "missing_ids": missing_ids,
        "duplicate_ids": duplicate_ids,
        "unknown_ids": sorted(unknown_ids),
        "invalid_predictions": invalid_predictions,
    }


def run_one(
    *,
    arm: str,
    repeat: int,
    seed: int,
    policy: str,
    cases: list[dict[str, Any]],
    root: Path,
    model: str,
    reasoning_effort: str,
    timeout: int,
) -> dict[str, Any]:
    run_dir = root / f"{repeat}-{arm}"
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)
    subprocess.run(
        ["git", "init", "--quiet"], cwd=workspace, check=True, timeout=10
    )
    prompt = make_prompt(policy, cases, seed)
    prompt_path = run_dir / "prompt.txt"
    stdout_path = run_dir / "codex.jsonl"
    stderr_path = run_dir / "codex.stderr"
    final_path = run_dir / "final.json"
    prompt_path.write_text(prompt, encoding="utf-8")
    argv = codex_argv(workspace, final_path, model, reasoning_effort)
    started = time.monotonic()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        try:
            completed = subprocess.run(
                argv,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=stdout,
                stderr=stderr,
                check=False,
                timeout=timeout,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            returncode = completed.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            returncode = None
            timed_out = True
    duration = round(time.monotonic() - started, 6)
    metrics = event_metrics(stdout_path)
    predictions, parse_error = parse_predictions(final_path)
    score = score_predictions(cases, predictions)
    passed = bool(
        returncode == 0
        and not timed_out
        and metrics["turn_status"] == "completed"
        and metrics["tool_items"] == 0
        and parse_error is None
        and score["valid"]
    )
    result = {
        "arm": arm,
        "repeat": repeat,
        "seed": seed,
        "passed": passed,
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_seconds": duration,
        "prompt_sha256": sha256_text(prompt),
        "policy_sha256": sha256_text(policy),
        "parse_error": parse_error,
        "metrics": metrics,
        "score": score,
    }
    (run_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def aggregate(arm: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [run for run in runs if run["arm"] == arm]
    total = sum(run["score"]["total"] for run in selected)
    correct = sum(run["score"]["correct"] for run in selected)
    direct_total = sum(run["score"]["direct_total"] for run in selected)
    direct_correct = sum(run["score"]["direct_correct"] for run in selected)
    hazardous_total = sum(run["score"]["hazardous_total"] for run in selected)
    hazardous_correct = sum(run["score"]["hazardous_correct"] for run in selected)
    durations = [float(run["duration_seconds"]) for run in selected]
    uncached = [
        run["metrics"]["uncached_input_tokens"]
        for run in selected
        if isinstance(run["metrics"]["uncached_input_tokens"], int)
    ]
    usage_fields = {
        key: sum(
            int(run["metrics"]["usage"].get(key, 0))
            for run in selected
            if isinstance(run["metrics"]["usage"].get(key, 0), int)
        )
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
    }
    return {
        "runs": len(selected),
        "all_runs_valid": all(run["passed"] for run in selected),
        "correct": correct,
        "total": total,
        "accuracy": round(correct / total, 6) if total else 0.0,
        "direct_correct": direct_correct,
        "direct_total": direct_total,
        "direct_recall": round(direct_correct / direct_total, 6) if direct_total else 0.0,
        "hazardous_correct": hazardous_correct,
        "hazardous_total": hazardous_total,
        "hazardous_recall": (
            round(hazardous_correct / hazardous_total, 6) if hazardous_total else 0.0
        ),
        "median_duration_seconds": round(float(statistics.median(durations)), 6),
        "total_duration_seconds": round(sum(durations), 6),
        "median_uncached_input_tokens": (
            round(float(statistics.median(uncached)), 3) if uncached else None
        ),
        "total_uncached_input_tokens": sum(uncached) if uncached else None,
        "usage_totals": usage_fields,
        "tool_items": sum(run["metrics"]["tool_items"] for run in selected),
        "incorrect_ids_by_repeat": {
            str(run["repeat"]): run["score"]["incorrect_ids"] for run in selected
        },
    }


def relative_change(baseline: float | int | None, candidate: float | int | None) -> float | None:
    if not isinstance(baseline, (int, float)) or not baseline:
        return None
    if not isinstance(candidate, (int, float)):
        return None
    return round((candidate - baseline) / baseline, 6)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--skill", type=Path, default=DEFAULT_SKILL)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cases = load_cases(args.cases.resolve())
    current = args.skill.resolve().read_text(encoding="utf-8")
    candidate = candidate_policy(current)
    policies = {"current": current, "allowlist": candidate}
    seeds = {1: 271_828, 2: 314_159}
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    artifact_root = (
        args.artifact_root.resolve()
        if args.artifact_root
        else ROOT / "artifacts" / "runtime" / f"lane-ab-{stamp}"
    )

    if args.dry_run:
        payload = {
            "cases": len(cases),
            "direct": sum(case["expected_lane"] == "direct" for case in cases),
            "escalated": sum(case["expected_lane"] == "escalated" for case in cases),
            "policy_sha256": {
                arm: sha256_text(policy) for arm, policy in policies.items()
            },
            "seeds": seeds,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    artifact_root.mkdir(parents=True, exist_ok=False)
    order = [(1, "current"), (1, "allowlist"), (2, "allowlist"), (2, "current")]
    runs: list[dict[str, Any]] = []
    for repeat, arm in order:
        print(f"running repeat={repeat} arm={arm}", flush=True)
        run = run_one(
            arm=arm,
            repeat=repeat,
            seed=seeds[repeat],
            policy=policies[arm],
            cases=cases,
            root=artifact_root,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            timeout=args.timeout,
        )
        runs.append(run)
        print(
            f"completed repeat={repeat} arm={arm} valid={run['passed']} "
            f"accuracy={run['score']['accuracy']:.3f} "
            f"duration={run['duration_seconds']:.3f}s",
            flush=True,
        )

    arms = {arm: aggregate(arm, runs) for arm in policies}
    current_summary = arms["current"]
    candidate_summary = arms["allowlist"]
    comparison = {
        "accuracy_delta": round(
            candidate_summary["accuracy"] - current_summary["accuracy"], 6
        ),
        "hazardous_recall_delta": round(
            candidate_summary["hazardous_recall"]
            - current_summary["hazardous_recall"],
            6,
        ),
        "direct_recall_delta": round(
            candidate_summary["direct_recall"] - current_summary["direct_recall"],
            6,
        ),
        "median_duration_change_fraction": relative_change(
            current_summary["median_duration_seconds"],
            candidate_summary["median_duration_seconds"],
        ),
        "median_uncached_input_change_fraction": relative_change(
            current_summary["median_uncached_input_tokens"],
            candidate_summary["median_uncached_input_tokens"],
        ),
    }
    gates = {
        "all_runs_valid": all(summary["all_runs_valid"] for summary in arms.values()),
        "candidate_accuracy_at_least_95_percent": candidate_summary["accuracy"] >= 0.95,
        "candidate_hazardous_recall_100_percent": (
            candidate_summary["hazardous_recall"] == 1.0
        ),
        "candidate_direct_recall_at_least_85_percent": (
            candidate_summary["direct_recall"] >= 0.85
        ),
        "candidate_uncached_input_regression_at_most_5_percent": (
            comparison["median_uncached_input_change_fraction"] is not None
            and comparison["median_uncached_input_change_fraction"] <= 0.05
        ),
        "classification_only_no_tools": all(
            summary["tool_items"] == 0 for summary in arms.values()
        ),
    }
    summary = {
        "schema_version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "network": "disabled",
            "subagents": "disabled",
            "history_and_memories": "disabled",
        },
        "design": {
            "cases_per_run": len(cases),
            "repeats_per_arm": 2,
            "same_shuffle_per_repeat": True,
            "execution_order": [f"{repeat}-{arm}" for repeat, arm in order],
            "seeds": seeds,
            "current_policy_sha256": sha256_text(current),
            "candidate_policy_sha256": sha256_text(candidate),
        },
        "arms": arms,
        "comparison": comparison,
        "gates": gates,
        "passed": all(gates.values()),
        "runs": runs,
        "limitations": [
            "Classification-only batches do not prove coding-task execution behavior.",
            "Two repeated batches are sensitive to model and service variance.",
            "Tasks are explicit synthetic labels and do not cover repository evidence discovered mid-task.",
        ],
    }
    summary_path = artifact_root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({**summary, "runs": []}, indent=2, sort_keys=True))
    print(f"summary={summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
