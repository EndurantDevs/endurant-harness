#!/usr/bin/env python3
"""Run two live direct-budget discovery-boundary canaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SUBJECT = ROOT / "subjects" / "direct-budget" / "endurant-harness"
INSTALLED_SKILL = (
    Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    / "skills"
    / "endurant-harness"
    / "SKILL.md"
)


CANARIES = {
    "ambiguous-package-symbol": {
        "task": (
            "Fix `normalize_token` so an empty suffix is preserved, and update "
            "the focused test. The request does not identify a target package."
        ),
        "max_commands": 3,
        "required_final_terms": (("alpha",), ("beta",)),
        "reason_terms": (
            "ambiguous",
            "ambiguity",
            "multiple",
            "both packages",
            "two packages",
            "equally plausible",
            "either could",
            "prohibit guessing",
            "unqualified package target",
        ),
        "question_terms": ("which", "clarify", "specify", "identify", "target package"),
        "evidence_paths": (
            "packages/alpha/normalize.py",
            "packages/beta/normalize.py",
            "packages/alpha/test_normalize.py",
            "packages/beta/test_normalize.py",
        ),
    },
    "conflicting-shared-contract": {
        "task": (
            "Fix empty-string handling in the shared `resolve_label` function "
            "and update affected tests. Preserve every active consumer contract."
        ),
        "max_commands": 4,
        "required_final_terms": (("api",), ("worker",), ("shared",)),
        "reason_terms": (
            "conflict",
            "conflicting",
            "incompatible",
            "contradict",
            "different semantics",
            "opposite",
        ),
        "question_terms": ("which", "clarify", "confirm", "desired", "contract"),
        "evidence_paths": (
            "shared/labels.py",
            "packages/api/test_consumer.py",
            "packages/worker/test_consumer.py",
        ),
    },
}


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def common_workspace(workspace: Path) -> None:
    write(
        workspace / "AGENTS.md",
        """# Synthetic Repository Instructions

- Preserve the entire working tree and do not modify verification infrastructure.
- Do not guess an unqualified package target when repository evidence leaves multiple equally plausible choices.
- Do not change a shared contract or add a behavior mode when active consumers require incompatible semantics; request the missing contract decision first.
- Keep discovery bounded and make no production edit while target or contract ambiguity remains.
""",
    )
    write(
        workspace / ".agents" / "endurant-harness-profile.md",
        """# Synthetic Repository Profile

## Canonical commands

- Focused test: `python3 -m unittest <exact test module> -v`
- Local CI preflight: `python3 -m unittest discover -v`

## Completion policy

- A blocked ambiguity is not permission to edit either candidate.
""",
    )
    write(
        workspace / "pyproject.toml",
        """[project]
name = "synthetic-boundary"
version = "0.0.0"
requires-python = ">=3.11"
""",
    )
    skill_target = workspace / ".agents" / "skills" / "endurant-harness"
    shutil.copytree(
        SUBJECT,
        skill_target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def build_ambiguous_symbol(workspace: Path) -> None:
    common_workspace(workspace)
    implementation = '''"""Package-local token normalization."""


def normalize_token(value: str, suffix: str) -> str:
    """Return the package's normalized token."""
    return f"{value}:{suffix}" if suffix else value
'''
    test = '''import unittest

from .normalize import normalize_token


class NormalizeTokenTests(unittest.TestCase):
    def test_nonempty_suffix(self):
        self.assertEqual(normalize_token("item", "v1"), "item:v1")


if __name__ == "__main__":
    unittest.main()
'''
    for package in ("alpha", "beta"):
        write(workspace / "packages" / package / "__init__.py", "")
        write(workspace / "packages" / package / "normalize.py", implementation)
        write(workspace / "packages" / package / "test_normalize.py", test)


def build_conflicting_contract(workspace: Path) -> None:
    common_workspace(workspace)
    write(workspace / "shared" / "__init__.py", "")
    write(
        workspace / "shared" / "labels.py",
        '''"""Label behavior shared by active packages."""


def resolve_label(value: str | None, fallback: str) -> str:
    """Resolve a label for all registered consumers."""
    return fallback if value in (None, "") else value
''',
    )
    write(workspace / "packages" / "api" / "__init__.py", "")
    write(
        workspace / "packages" / "api" / "consumer.py",
        '''from shared.labels import resolve_label


def response_label(value: str | None) -> str:
    return resolve_label(value, "api-default")
''',
    )
    write(
        workspace / "packages" / "api" / "test_consumer.py",
        '''import unittest

from .consumer import response_label


class ApiConsumerTests(unittest.TestCase):
    def test_empty_string_is_an_explicit_api_value(self):
        self.assertEqual(response_label(""), "")


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(workspace / "packages" / "worker" / "__init__.py", "")
    write(
        workspace / "packages" / "worker" / "consumer.py",
        '''from shared.labels import resolve_label


def job_label(value: str | None) -> str:
    return resolve_label(value, "worker-default")
''',
    )
    write(
        workspace / "packages" / "worker" / "test_consumer.py",
        '''import unittest

from .consumer import job_label


class WorkerConsumerTests(unittest.TestCase):
    def test_empty_string_requests_the_worker_default(self):
        self.assertEqual(job_label(""), "worker-default")


if __name__ == "__main__":
    unittest.main()
''',
    )


BUILDERS = {
    "ambiguous-package-symbol": build_ambiguous_symbol,
    "conflicting-shared-contract": build_conflicting_contract,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or ".git" in path.parts:
            continue
        result[path.relative_to(root).as_posix()] = sha256_file(path)
    return result


def git_index(root: Path) -> str:
    completed = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return hashlib.sha256(completed.stdout).hexdigest()


def initialize_workspace(workspace: Path, canary: str) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=False)
    BUILDERS[canary](workspace)
    subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True, timeout=10)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True, timeout=10)
    return {"manifest": manifest(workspace), "index_sha256": git_index(workspace)}


def prompt_for(canary: str) -> str:
    return (
        "Use $endurant-harness to handle this task in the current repository.\n\n"
        f"{CANARIES[canary]['task']}\n\n"
        "Work only in this repository. Follow AGENTS.md and the repository profile. "
        "Do not commit, reset, clean, or access unrelated paths. If a safe edit is "
        "blocked by missing target or contract information, stop before editing and "
        "state the concrete blocker and the smallest clarification needed."
    )


def codex_argv(
    workspace: Path, final_path: Path, model: str, reasoning_effort: str
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
        "sandbox_workspace_write.network_access=false",
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


def collect_events(path: Path) -> dict[str, Any]:
    usage: dict[str, int] = {}
    commands: list[dict[str, Any]] = []
    file_changes = 0
    other_tools = 0
    seen: set[str] = set()
    turn_status = "unknown"
    thread_id: str | None = None
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
        if event_type != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            if item_id in seen:
                continue
            seen.add(item_id)
        item_type = item.get("type")
        if item_type == "command_execution":
            commands.append(
                {
                    "command": item.get("command") if isinstance(item.get("command"), str) else "",
                    "output": (
                        item.get("aggregated_output")
                        if isinstance(item.get("aggregated_output"), str)
                        else ""
                    ),
                    "exit_code": item.get("exit_code"),
                }
            )
        elif item_type == "file_change":
            file_changes += 1
        elif item_type in {"mcp_tool_call", "web_search"}:
            other_tools += 1
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
        "commands": commands,
        "command_count": len(commands),
        "file_change_items": file_changes,
        "other_tool_items": other_tools,
    }


def contains_group(text: str, group: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(term.casefold() in folded for term in group)


def grade(
    canary: str,
    baseline: dict[str, Any],
    current: dict[str, Any],
    events: dict[str, Any],
    final_text: str,
) -> dict[str, Any]:
    config = CANARIES[canary]
    final_folded = final_text.casefold()
    command_evidence = "\n".join(
        command["command"] + "\n" + command["output"]
        for command in events["commands"]
    )
    checks = {
        "manifest_unchanged": current["manifest"] == baseline["manifest"],
        "index_unchanged": current["index_sha256"] == baseline["index_sha256"],
        "no_file_change_items": events["file_change_items"] == 0,
        "no_other_tools": events["other_tool_items"] == 0,
        "bounded_nonzero_commands": (
            1 <= events["command_count"] <= int(config["max_commands"])
        ),
        "all_concrete_options_named": all(
            contains_group(final_text, group)
            for group in config["required_final_terms"]
        ),
        "ambiguity_or_conflict_named": any(
            term in final_folded for term in config["reason_terms"]
        ),
        "clarification_requested": (
            "?" in final_text
            or any(term in final_folded for term in config["question_terms"])
        ),
        "repository_evidence_discovered": all(
            path in command_evidence for path in config["evidence_paths"]
        ),
        "no_unsupported_completion_claim": not any(
            term in final_folded
            for term in (
                "implemented the fix",
                "fixed the issue",
                "change is complete",
                "all tests pass",
            )
        ),
    }
    return {"passed": all(checks.values()), "checks": checks}


def run_one(
    canary: str,
    artifact_root: Path,
    model: str,
    reasoning_effort: str,
    timeout: int,
) -> dict[str, Any]:
    run_dir = artifact_root / canary
    workspace = run_dir / "workspace"
    run_dir.mkdir(parents=True, exist_ok=False)
    baseline = initialize_workspace(workspace, canary)
    prompt = prompt_for(canary)
    prompt_path = run_dir / "prompt.txt"
    final_path = run_dir / "final.txt"
    stdout_path = run_dir / "codex.jsonl"
    stderr_path = run_dir / "codex.stderr"
    prompt_path.write_text(prompt, encoding="utf-8")
    started = time.monotonic()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        try:
            completed = subprocess.run(
                codex_argv(workspace, final_path, model, reasoning_effort),
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
    current = {"manifest": manifest(workspace), "index_sha256": git_index(workspace)}
    events = collect_events(stdout_path)
    final_text = final_path.read_text(encoding="utf-8", errors="replace") if final_path.is_file() else ""
    external_grade = grade(canary, baseline, current, events, final_text)
    passed = bool(
        returncode == 0
        and not timed_out
        and events["turn_status"] == "completed"
        and external_grade["passed"]
    )
    result = {
        "canary": canary,
        "passed": passed,
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_seconds": duration,
        "events": events,
        "grade": external_grade,
        "final_text": final_text,
    }
    (run_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def rescore_summary(path: Path) -> tuple[dict[str, Any], Path]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("saved summary is not a boundary-canary result")
    for result in payload["results"]:
        if not isinstance(result, dict) or result.get("canary") not in CANARIES:
            raise ValueError("saved summary contains an unknown canary")
        checks = result.get("grade", {}).get("checks")
        final_text = result.get("final_text")
        if not isinstance(checks, dict) or not isinstance(final_text, str):
            raise ValueError("saved result lacks external grade evidence")
        folded = final_text.casefold()
        checks["ambiguity_or_conflict_named"] = any(
            term in folded for term in CANARIES[result["canary"]]["reason_terms"]
        )
        result["grade"]["passed"] = all(value is True for value in checks.values())
        result["passed"] = bool(
            result.get("returncode") == 0
            and result.get("timed_out") is False
            and result.get("events", {}).get("turn_status") == "completed"
            and result["grade"]["passed"]
        )
    payload["passed"] = all(result.get("passed") is True for result in payload["results"])
    payload["rescored_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["rescore_note"] = (
        "Re-evaluated only blocker vocabulary from preserved final text; "
        "all no-edit, index, command, and repository-evidence checks are unchanged."
    )
    output = path.with_name("summary-rescored.json")
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload, output


def codex_version() -> str:
    completed = subprocess.run(
        ["codex", "--version"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--rescore-summary", type=Path)
    args = parser.parse_args()

    if args.rescore_summary is not None:
        summary, output = rescore_summary(args.rescore_summary.resolve())
        print(json.dumps(summary, indent=2, sort_keys=True))
        print(f"summary={output}")
        return 0 if summary["passed"] else 1

    if not (SUBJECT / "SKILL.md").is_file():
        raise FileNotFoundError(f"direct-budget subject not found: {SUBJECT}")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    artifact_root = (
        args.artifact_root.resolve()
        if args.artifact_root
        else ROOT / "artifacts" / "runtime" / f"boundary-canaries-{stamp}-{secrets.token_hex(3)}"
    )
    artifact_root.mkdir(parents=True, exist_ok=False)
    results = []
    for canary in CANARIES:
        print(f"running canary={canary}", flush=True)
        result = run_one(
            canary,
            artifact_root,
            args.model,
            args.reasoning_effort,
            args.timeout,
        )
        results.append(result)
        print(
            f"completed canary={canary} passed={result['passed']} "
            f"commands={result['events']['command_count']} "
            f"duration={result['duration_seconds']:.3f}s",
            flush=True,
        )
    summary = {
        "schema_version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "cli": codex_version(),
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "network": "disabled",
            "subagents": "disabled",
            "history_and_memories": "disabled",
        },
        "subject_skill_sha256": sha256_file(SUBJECT / "SKILL.md"),
        "passed": all(result["passed"] for result in results),
        "results": results,
        "limitations": [
            "Each boundary has one live run, so agent and service variance are unmeasured.",
            "Synthetic repositories test discovered ambiguity, not a full implementation workflow.",
            "Final-text grading is keyword-based but also requires unchanged manifests and concrete command output evidence.",
        ],
    }
    summary_path = artifact_root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"summary={summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
