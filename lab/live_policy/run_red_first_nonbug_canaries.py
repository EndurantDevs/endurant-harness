#!/usr/bin/env python3
"""Run externally graded feature/refactor canaries for conditional red-first policy."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "lab"
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))

from eval_lib import (  # noqa: E402
    file_manifest,
    git_state,
    manifest_delta,
    run_process,
    tree_manifest,
    write_json,
)


SUBJECT = ROOT / "subjects" / "red-before-green" / "endurant-harness"
RUNTIME_ROOT = ROOT / "artifacts" / "runtime"
CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
INSTALLED_SKILL = CODEX_HOME / "skills" / "endurant-harness" / "SKILL.md"

VERIFY_SOURCE = '''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import compileall
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def tests(*args: str) -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", *args, "-v"],
        cwd=ROOT,
        check=False,
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=("focused", "local"))
    args = parser.parse_args()
    if args.gate == "focused":
        passed = tests("{focused_module}")
    else:
        passed = tests("discover", "-s", "tests")
        passed = passed and compileall.compile_dir(ROOT / "src", quiet=1)
        passed = passed and compileall.compile_dir(ROOT / "tests", quiet=1)
        if passed:
            print("LOCAL_CI_PASS")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


CASES: dict[str, dict[str, Any]] = {
    "feature": {
        "task": (
            "Add the new internal `build_label_key(parts)` feature to `src/labels.py`. "
            "It must strip and lowercase every string part, discard empty or whitespace-only "
            "parts, join the rest with `-`, raise `TypeError` for a non-string part, and never "
            "mutate the input. Add focused unit coverage. This is a feature request, not a bug."
        ),
        "module": "labels",
        "focused_module": "tests.test_labels",
        "source": '''"""Small label helpers."""


def display_label(value: str) -> str:
    """Collapse surrounding and repeated whitespace for display."""
    return " ".join(value.split())
''',
        "tests": '''import unittest

from src.labels import display_label


class DisplayLabelTests(unittest.TestCase):
    def test_collapses_whitespace(self):
        self.assertEqual(display_label("  Alpha   Beta  "), "Alpha Beta")


if __name__ == "__main__":
    unittest.main()
''',
        "hidden": '''from src.labels import build_label_key

parts = ["  Alpha ", "", " BETA  ", "   "]
before = list(parts)
assert build_label_key(parts) == "alpha-beta"
assert parts == before
assert build_label_key([]) == ""
try:
    build_label_key(["ok", 3])
except TypeError:
    pass
else:
    raise AssertionError("non-string parts must raise TypeError")
print("HIDDEN_FEATURE_PASS")
''',
        "required_changes": {"src/labels.py", "tests/test_labels.py"},
    },
    "refactor": {
        "task": (
            "Refactor the internal implementation of `canonicalize_tags` in `src/tags.py` "
            "so whitespace/lowercasing logic lives in a private `_clean_tag(value)` helper. "
            "Preserve the public signature, first-seen order, duplicate handling, and all "
            "existing behavior. Existing tests are sufficient unless you discover a gap. "
            "This is an internal refactor, not a behavior bug or performance task."
        ),
        "module": "tags",
        "focused_module": "tests.test_tags",
        "source": '''"""Tag normalization."""


def canonicalize_tags(values):
    """Normalize non-empty tags and retain their first-seen order."""
    result = []
    for value in values:
        cleaned = " ".join(value.strip().lower().split())
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result
''',
        "tests": '''import unittest

from src.tags import canonicalize_tags


class CanonicalizeTagsTests(unittest.TestCase):
    def test_normalizes_and_deduplicates_in_first_seen_order(self):
        values = [" Blue ", "RED", "blue", "  deep   blue ", ""]
        self.assertEqual(canonicalize_tags(values), ["blue", "red", "deep blue"])

    def test_does_not_mutate_input(self):
        values = [" A ", "B"]
        before = list(values)
        canonicalize_tags(values)
        self.assertEqual(values, before)


if __name__ == "__main__":
    unittest.main()
''',
        "hidden": '''from src.tags import _clean_tag, canonicalize_tags

values = [" Blue ", "RED", "blue", "  deep   blue ", ""]
before = list(values)
assert _clean_tag("  Mixed   CASE ") == "mixed case"
assert canonicalize_tags(values) == ["blue", "red", "deep blue"]
assert values == before
print("HIDDEN_REFACTOR_PASS")
''',
        "required_changes": {"src/tags.py"},
    },
}


def filtered_manifest(root: Path) -> dict[str, str]:
    return {
        path: digest
        for path, digest in file_manifest(root).items()
        if not path.startswith(".agents/")
    }


def materialize(case_name: str, run_root: Path) -> tuple[Path, dict[str, Any]]:
    case = CASES[case_name]
    workspace = run_root / "workspace"
    for relative in ("src", "tests", "scripts", ".agents/skills"):
        (workspace / relative).mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "__init__.py").write_text("", encoding="utf-8")
    (workspace / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (workspace / "src" / f"{case['module']}.py").write_text(
        case["source"], encoding="utf-8"
    )
    (workspace / "tests" / f"test_{case['module']}.py").write_text(
        case["tests"], encoding="utf-8"
    )
    verify = VERIFY_SOURCE.format(focused_module=case["focused_module"])
    verify_path = workspace / "scripts" / "verify.py"
    verify_path.write_text(verify, encoding="utf-8")
    verify_path.chmod(0o755)
    (workspace / "AGENTS.md").write_text(
        """# Synthetic repository instructions

- Prefix every shell command with `rtk`.
- Do not commit, stage, reset, clean, or edit `.agents/` or `scripts/`.
- Keep changes within `src/` and `tests/`.
- Focused proof: `rtk python3 scripts/verify.py focused`.
- Local CI preflight: `rtk python3 scripts/verify.py local`.
- This task is explicitly categorized in the task text; do not reinterpret a feature or internal refactor as a behavior bug.
""",
        encoding="utf-8",
    )
    (workspace / ".agents" / "endurant-harness-profile.md").write_text(
        """# Repository Engineering Profile

## Canonical commands

- Focused test: `rtk python3 scripts/verify.py focused`
- Local CI preflight: `rtk python3 scripts/verify.py local`

## Completion policy

- Run focused behavior and local CI after the production edit.
""",
        encoding="utf-8",
    )
    shutil.copytree(SUBJECT, workspace / ".agents" / "skills" / "endurant-harness")
    for command in (
        ["git", "init", "--quiet"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Synthetic Evaluator",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "synthetic baseline",
        ],
    ):
        completed = run_process(command, workspace)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr)
    return workspace, case


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


def prompt(case: dict[str, Any]) -> str:
    return (
        "Use $endurant-harness to complete this task in the current repository.\n\n"
        + case["task"]
        + "\n\nFollow AGENTS.md. Make the smallest complete change, run the canonical "
        "focused and local preflight commands, inspect the diff, and do not commit."
    )


def is_test_command(command: str) -> bool:
    lowered = command.lower()
    return any(
        marker in lowered
        for marker in (
            "unittest",
            "pytest",
            "scripts/verify.py focused",
            "scripts/verify.py local",
        )
    )


def output_reports_failure(output: str) -> bool:
    return bool(
        re.search(
            r"(?im)^FAILED(?: \(|$)|=+ FAILURES =+|^ERROR: test_|^ERRORS?$",
            output,
        )
    )


def observed_commands(path: Path, first_edit_ns: int | None) -> list[dict[str, Any]]:
    starts: dict[str, int] = {}
    commands: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            wrapped = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = wrapped.get("event")
        observed = wrapped.get("observed_monotonic_ns")
        if not isinstance(event, dict) or not isinstance(observed, int):
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        if event.get("type") == "item.started":
            starts[item_id] = observed
            continue
        if event.get("type") != "item.completed":
            continue
        command = str(item.get("command", ""))
        output = str(item.get("aggregated_output", ""))
        exit_code = item.get("exit_code")
        started = starts.get(item_id, observed)
        test_like = is_test_command(command)
        failed = bool(
            test_like
            and (
                (isinstance(exit_code, int) and exit_code != 0)
                or output_reports_failure(output)
            )
        )
        commands.append(
            {
                "id": item_id,
                "command": command,
                "exit_code": exit_code,
                "started_seconds": None,
                "duration_seconds": round((observed - started) / 1_000_000_000, 6),
                "test_like": test_like,
                "failed_test": failed,
                "started_before_first_production_edit": bool(
                    first_edit_ns is not None and started < first_edit_ns
                ),
            }
        )
    return commands


def usage_metrics(path: Path) -> dict[str, Any]:
    usage: dict[str, int] = {}
    status = "unknown"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            status = "completed"
            raw = event.get("usage")
            if isinstance(raw, dict):
                usage = {
                    key: value
                    for key, value in raw.items()
                    if isinstance(value, int) and not isinstance(value, bool)
                }
        elif event.get("type") == "turn.failed":
            status = "failed"
    inputs = usage.get("input_tokens")
    cached = usage.get("cached_input_tokens")
    return {
        "turn_status": status,
        "usage": usage,
        "uncached_input_tokens": (
            max(inputs - cached, 0)
            if isinstance(inputs, int) and isinstance(cached, int)
            else None
        ),
    }


def execute_agent(
    workspace: Path,
    case: dict[str, Any],
    capture: Path,
    *,
    model: str,
    reasoning_effort: str,
    timeout: int,
    baseline_production: dict[str, str],
) -> dict[str, Any]:
    raw_path = capture / "codex.jsonl"
    timed_path = capture / "codex-observed.jsonl"
    stderr_path = capture / "codex.stderr"
    final_path = capture / "final.txt"
    task_prompt = prompt(case)
    (capture / "prompt.txt").write_text(task_prompt + "\n", encoding="utf-8")
    argv = codex_argv(workspace, final_path, model, reasoning_effort)
    started = time.monotonic()
    started_ns = time.monotonic_ns()
    proc = subprocess.Popen(
        argv,
        cwd=workspace,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
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
    proc.stdin.write(task_prompt)
    proc.stdin.close()

    def read_stdout() -> None:
        with raw_path.open("w", encoding="utf-8") as raw, timed_path.open(
            "w", encoding="utf-8"
        ) as timed:
            for line in proc.stdout:
                observed = time.monotonic_ns()
                raw.write(line)
                raw.flush()
                try:
                    event: Any = json.loads(line)
                except json.JSONDecodeError:
                    event = {"raw": line.rstrip("\n")}
                timed.write(
                    json.dumps(
                        {"observed_monotonic_ns": observed, "event": event},
                        sort_keys=True,
                    )
                    + "\n"
                )
                timed.flush()

    def read_stderr() -> None:
        with stderr_path.open("w", encoding="utf-8") as handle:
            for line in proc.stderr:
                handle.write(line)
                handle.flush()

    threads = [
        threading.Thread(target=read_stdout, daemon=True),
        threading.Thread(target=read_stderr, daemon=True),
    ]
    for thread in threads:
        thread.start()

    first_edit_ns: int | None = None
    timed_out = False
    while proc.poll() is None:
        current = {
            path: digest
            for path, digest in filtered_manifest(workspace).items()
            if path.startswith("src/")
        }
        if first_edit_ns is None and current != baseline_production:
            first_edit_ns = time.monotonic_ns()
        if time.monotonic() - started > timeout:
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
        time.sleep(0.02)

    exit_code = proc.wait()
    for thread in threads:
        thread.join(timeout=5)
    duration = time.monotonic() - started
    current = {
        path: digest
        for path, digest in filtered_manifest(workspace).items()
        if path.startswith("src/")
    }
    if first_edit_ns is None and current != baseline_production:
        first_edit_ns = time.monotonic_ns()
    commands = observed_commands(timed_path, first_edit_ns)
    for command in commands:
        command_start = None
        for line in timed_path.read_text(encoding="utf-8", errors="replace").splitlines():
            wrapped = json.loads(line)
            event = wrapped.get("event", {})
            item = event.get("item", {}) if isinstance(event, dict) else {}
            if (
                event.get("type") == "item.started"
                and item.get("id") == command["id"]
            ):
                command_start = wrapped.get("observed_monotonic_ns")
                break
        if isinstance(command_start, int):
            command["started_seconds"] = round(
                (command_start - started_ns) / 1_000_000_000, 6
            )
    metrics = usage_metrics(raw_path)
    metrics.update(
        {
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": round(duration, 6),
            "time_to_first_production_edit_seconds": (
                round((first_edit_ns - started_ns) / 1_000_000_000, 6)
                if first_edit_ns is not None
                else None
            ),
            "commands": commands,
            "command_count": len(commands),
            "pre_edit_failing_test_commands": [
                command["command"]
                for command in commands
                if command["failed_test"]
                and command["started_before_first_production_edit"]
            ],
        }
    )
    return metrics


def grade(
    workspace: Path,
    case_name: str,
    case: dict[str, Any],
    baseline_manifest: dict[str, str],
    baseline_git: dict[str, str | None],
    baseline_skill: dict[str, dict[str, Any]],
    agent: dict[str, Any],
) -> dict[str, Any]:
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    focused = run_process(
        [sys.executable, "scripts/verify.py", "focused"],
        workspace,
        env=environment,
    )
    local = run_process(
        [sys.executable, "scripts/verify.py", "local"], workspace, env=environment
    )
    hidden = run_process(
        [sys.executable, "-c", case["hidden"]], workspace, env=environment
    )
    focused_output = focused.stdout + focused.stderr
    local_output = local.stdout + local.stderr
    after = filtered_manifest(workspace)
    delta = manifest_delta(baseline_manifest, after)
    changed = sorted(
        set(delta["added"]) | set(delta["removed"]) | set(delta["modified"])
    )
    allowed = {
        f"src/{case['module']}.py",
        f"tests/test_{case['module']}.py",
    }
    checks = {
        "agent_completed": (
            agent["exit_code"] == 0
            and agent["timed_out"] is False
            and agent["turn_status"] == "completed"
        ),
        "production_edit_observed": agent["time_to_first_production_edit_seconds"]
        is not None,
        "no_pre_edit_failing_test": not agent["pre_edit_failing_test_commands"],
        "focused_passed": (
            focused.returncode == 0
            and re.search(r"Ran [1-9][0-9]* tests?", focused_output) is not None
            and "OK" in focused_output
        ),
        "local_ci_passed": (
            local.returncode == 0
            and "LOCAL_CI_PASS" in local_output
            and re.search(r"Ran [1-9][0-9]* tests?", local_output) is not None
        ),
        "hidden_behavior_passed": hidden.returncode == 0,
        "required_files_changed": set(case["required_changes"]).issubset(changed),
        "scope_preserved": set(changed).issubset(allowed),
        "subject_unchanged": tree_manifest(
            workspace / ".agents" / "skills" / "endurant-harness"
        )
        == baseline_skill,
        "git_head_and_index_unchanged": git_state(workspace) == baseline_git,
    }
    return {
        "case": case_name,
        "passed": all(checks.values()),
        "checks": checks,
        "changed_paths": changed,
        "focused": {
            "exit_code": focused.returncode,
            "signal": focused_output[-1000:],
        },
        "local_ci": {
            "exit_code": local.returncode,
            "signal": local_output[-1000:],
        },
        "hidden": {"exit_code": hidden.returncode, "signal": (hidden.stdout + hidden.stderr)[-1000:]},
    }


def run_case(
    case_name: str,
    output: Path,
    *,
    model: str,
    reasoning_effort: str,
    timeout: int,
) -> dict[str, Any]:
    capture = output / case_name
    capture.mkdir(parents=True, exist_ok=False)
    workspace, case = materialize(case_name, capture)
    baseline = filtered_manifest(workspace)
    baseline_production = {
        path: digest for path, digest in baseline.items() if path.startswith("src/")
    }
    baseline_git = git_state(workspace)
    baseline_skill = tree_manifest(workspace / ".agents" / "skills" / "endurant-harness")
    agent = execute_agent(
        workspace,
        case,
        capture,
        model=model,
        reasoning_effort=reasoning_effort,
        timeout=timeout,
        baseline_production=baseline_production,
    )
    result = grade(
        workspace,
        case_name,
        case,
        baseline,
        baseline_git,
        baseline_skill,
        agent,
    )
    result["agent"] = agent
    write_json(capture / "summary.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--output")
    args = parser.parse_args()
    if not SUBJECT.is_dir():
        raise FileNotFoundError(SUBJECT)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    output = (
        Path(args.output).resolve()
        if args.output
        else RUNTIME_ROOT / f"red-first-nonbug-{stamp}-{secrets.token_hex(3)}"
    )
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    cases = [
        run_case(
            case_name,
            output,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            timeout=args.timeout,
        )
        for case_name in CASES
    ]
    result = {
        "schema_version": 1,
        "subject": "subjects/red-before-green/endurant-harness",
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "network": "disabled",
        "subagents": "disabled",
        "cases": cases,
        "duration_seconds": round(time.monotonic() - started, 6),
        "passed": all(case["passed"] for case in cases),
    }
    write_json(output / "summary.json", result)
    print(json.dumps({**result, "output": str(output)}, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
