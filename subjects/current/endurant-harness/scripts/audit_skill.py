#!/usr/bin/env python3
"""Audit the Endurant Harness Agent Skill package using the standard library.

Exit codes: 0 = pass, 1 = validation failure, 2 = usage/runtime error.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ALLOWED_TOP_LEVEL = {
    "SKILL.md",
    "LICENSE.txt",
    "agents",
    "assets",
    "evals",
    "references",
    "scripts",
}
ALLOWED_FRONTMATTER = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\[[^]]*]\(([^)]+)\)")
KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$")
YAML_VALUE_RE = re.compile(r"^\s*([A-Za-z0-9_-]+):\s*(.*?)\s*$")
REQUIRED_FILES = {
    "agents/openai.yaml",
    "scripts/endurant.py",
    "scripts/benchmark_efficiency.py",
    "evals/evals.json",
    "evals/trigger-cases.json",
    "evals/efficiency-baseline.json",
    "evals/capability-contract.json",
    "references/protocol.md",
    "references/batch-plan.md",
    "references/risk.md",
    "references/checkpoint.md",
    "references/repository-profile.md",
    "references/maintenance.md",
}
REQUIRED_CAPABILITIES = {
    'trigger-boundaries',
    'preserve-unrelated-user-work',
    'repository-orientation',
    'durable-repository-profile',
    'acceptance-contract',
    'baseline-before-edit',
    'trace-hypothesis-disproof',
    'analogy-and-ambiguity-policy',
    'write-replan-done-gates',
    'dependency-aware-batching',
    'packet-contract',
    'output-and-service-hygiene',
    'single-write-owner-and-subagents',
    'smallest-coherent-change',
    'generated-output-discipline',
    'evidence-strength-hierarchy',
    'risk-verification-matrix',
    'failure-classification',
    'false-green-defense',
    'checkpoint-operational-state',
    'claim-honesty-and-handoff',
    'tiny-task-scaling',
    'rollout-and-operator-review',
    'runner-safety',
    'self-dogfood-and-parity',
}
ALLOWED_ACTIVATIONS = {"core", "conditional", "runtime"}


def scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
            return parsed if isinstance(parsed, str) else value
        except (SyntaxError, ValueError):
            return value[1:-1]
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, str], str | None]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "SKILL.md must begin with YAML frontmatter"
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, "SKILL.md frontmatter is not closed"
    data: dict[str, str] = {}
    index = 1
    while index < end:
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[:1].isspace():
            index += 1
            continue
        match = KEY_RE.match(line)
        if not match:
            return {}, f"cannot parse frontmatter line {index + 1}: {line!r}"
        key, raw = match.group(1), (match.group(2) or "")
        if raw in {">", "|", ">-", "|-", ">+", "|+"}:
            chunks: list[str] = []
            folded = raw.startswith(">")
            index += 1
            while index < end and (not lines[index].strip() or lines[index][:1].isspace()):
                chunks.append(lines[index].strip())
                index += 1
            data[key] = (" " if folded else "\n").join(chunks).strip()
            continue
        data[key] = scalar(raw)
        index += 1
    return data, None


def parse_simple_yaml_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line.rstrip().endswith(":"):
            continue
        match = YAML_VALUE_RE.match(line)
        if match:
            values[match.group(1)] = scalar(match.group(2))
    return values


def add(result: dict[str, Any], level: str, code: str, message: str) -> None:
    result[level].append({"code": code, "message": message})


def json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run_process(argv: list[str], cwd: Path, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def validate_capability_contract(root: Path, result: dict[str, Any], skill_name: str) -> None:
    path = root / "evals" / "capability-contract.json"
    if not path.is_file():
        return
    try:
        payload = json_file(path)
    except (OSError, json.JSONDecodeError) as exc:
        add(result, "errors", "capabilities.json", f"invalid capability contract: {exc}")
        return
    if payload.get("skill_name") != skill_name:
        add(result, "errors", "capabilities.skill_name", "capability contract skill_name mismatch")
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list):
        add(result, "errors", "capabilities.list", "capability contract requires a list")
        return
    skill_text = (root / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    seen: set[str] = set()
    critical = 0
    activation_counts = {name: 0 for name in sorted(ALLOWED_ACTIVATIONS)}
    for index, capability in enumerate(capabilities):
        if not isinstance(capability, dict):
            add(result, "errors", "capabilities.entry", f"capability {index} must be an object")
            continue
        capability_id = capability.get("id")
        if not isinstance(capability_id, str) or not capability_id:
            add(result, "errors", "capabilities.id", f"capability {index} has invalid id")
            continue
        if capability_id in seen:
            add(result, "errors", "capabilities.duplicate", f"duplicate capability id: {capability_id}")
        seen.add(capability_id)
        importance = capability.get("importance")
        if importance not in {"critical", "important"}:
            add(result, "errors", "capabilities.importance", f"{capability_id}: invalid importance")
        critical += importance == "critical"
        activation = capability.get("activation")
        if activation not in ALLOWED_ACTIVATIONS:
            add(result, "errors", "capabilities.activation", f"{capability_id}: invalid activation tier")
        else:
            activation_counts[activation] += 1
        source = capability.get("source_v3")
        if not isinstance(source, list) or not source or not all(isinstance(item, str) and item for item in source):
            add(result, "errors", "capabilities.source", f"{capability_id}: source_v3 must be non-empty strings")
        evidence = capability.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            add(result, "errors", "capabilities.evidence", f"{capability_id}: evidence is required")
            continue
        evidence_paths: list[str] = []
        for clause_index, clause in enumerate(evidence):
            if not isinstance(clause, dict):
                add(result, "errors", "capabilities.clause", f"{capability_id}: clause {clause_index} must be an object")
                continue
            relative = clause.get("path")
            if not isinstance(relative, str) or not relative:
                add(result, "errors", "capabilities.path", f"{capability_id}: clause {clause_index} has no path")
                continue
            evidence_paths.append(relative)
            target = (root / relative).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                add(result, "errors", "capabilities.escape", f"{capability_id}: evidence path escapes root: {relative}")
                continue
            if not target.is_file():
                add(result, "errors", "capabilities.missing", f"{capability_id}: evidence file missing: {relative}")
                continue
            text = target.read_text(encoding="utf-8", errors="replace").casefold()
            all_terms = clause.get("all", [])
            any_terms = clause.get("any", [])
            if not isinstance(all_terms, list) or not all(isinstance(item, str) and item for item in all_terms):
                add(result, "errors", "capabilities.terms", f"{capability_id}: invalid all terms")
                continue
            if not isinstance(any_terms, list) or not all(isinstance(item, str) and item for item in any_terms):
                add(result, "errors", "capabilities.terms", f"{capability_id}: invalid any terms")
                continue
            if not all_terms and not any_terms:
                add(result, "errors", "capabilities.terms", f"{capability_id}: evidence clause has no terms")
                continue
            missing = [term for term in all_terms if term.casefold() not in text]
            if missing:
                add(result, "errors", "capabilities.unmet", f"{capability_id}: {relative} missing {missing}")
            if any_terms and not any(term.casefold() in text for term in any_terms):
                add(result, "errors", "capabilities.unmet", f"{capability_id}: {relative} matches none of {any_terms}")
        if activation == "core" and "SKILL.md" not in evidence_paths:
            add(result, "errors", "capabilities.core_reach", f"{capability_id}: core capability lacks SKILL.md evidence")
        if activation == "conditional":
            references = [item for item in evidence_paths if item.startswith("references/")]
            if not references:
                add(result, "errors", "capabilities.conditional_reach", f"{capability_id}: conditional capability lacks reference evidence")
            for relative in references:
                if relative not in skill_text:
                    add(result, "errors", "capabilities.conditional_link", f"{capability_id}: {relative} is not linked from SKILL.md")
        if activation == "runtime" and not any(item.startswith("scripts/") for item in evidence_paths):
            add(result, "errors", "capabilities.runtime_reach", f"{capability_id}: runtime capability lacks script evidence")
    missing_ids = sorted(REQUIRED_CAPABILITIES - seen)
    if missing_ids:
        add(result, "errors", "capabilities.required", f"missing required capabilities: {', '.join(missing_ids)}")
    result["metrics"]["capability_cases"] = len(capabilities)
    result["metrics"]["critical_capabilities"] = critical
    result["metrics"]["capability_activation"] = activation_counts


def smoke_runner(root: Path, result: dict[str, Any]) -> None:
    script = root / "scripts" / "endurant.py"
    with tempfile.TemporaryDirectory(prefix="endurant-audit-") as temp_value:
        temp = Path(temp_value)

        probe = run_process(
            [
                sys.executable,
                "-S",
                str(script),
                "probe",
                "--repo",
                str(root),
                "--task",
                "audit staged runner",
                "--format",
                "json",
                "--max-items",
                "10",
            ],
            root,
        )
        try:
            probe_payload = json.loads(probe.stdout)
        except json.JSONDecodeError:
            probe_payload = {}
        probe_root = Path(probe_payload.get("root", "/__missing__")).resolve()
        probe_path = Path(probe_payload.get("probe_path", "/__missing__")).resolve()
        try:
            root.resolve().relative_to(probe_root)
            root_is_within_probe_root = True
        except ValueError:
            root_is_within_probe_root = False
        if probe.returncode != 0 or probe_path != root.resolve() or not root_is_within_probe_root:
            add(result, "errors", "smoke.probe", f"probe failed: {probe.stderr or probe.stdout}")

        profile_repo = temp / "profile-repo"
        (profile_repo / ".agents").mkdir(parents=True)
        (profile_repo / ".agents" / "endurant-harness-profile.md").write_text(
            "# Profile\n\nFocused test: `python -m unittest`\n", encoding="utf-8"
        )
        profiled = run_process(
            [sys.executable, "-S", str(script), "probe", "--repo", str(profile_repo), "--task", "profile smoke", "--format", "json"],
            root,
        )
        try:
            profile_payload = json.loads(profiled.stdout)
        except json.JSONDecodeError:
            profile_payload = {}
        profiles = profile_payload.get("profiles", [])
        if profiled.returncode != 0 or not profiles or profiles[0].get("path") != ".agents/endurant-harness-profile.md":
            add(result, "errors", "smoke.profile", f"repository profile was not discovered: {profiled.stderr or profiled.stdout}")

        pass_plan = {
            "name": "audit-pass",
            "cwd": str(temp),
            "require_behavior_evidence": True,
            "stages": [
                {
                    "name": "parallel",
                    "parallel": True,
                    "commands": [
                        {"id": "one", "argv": [sys.executable, "-S", "-c", "print('one')"], "evidence": "behavior"},
                        {"id": "two", "argv": [sys.executable, "-S", "-c", "print('two')"], "evidence": "static"},
                    ],
                },
                {
                    "name": "always",
                    "run_if": "always",
                    "commands": [
                        {"id": "three", "argv": [sys.executable, "-S", "-c", "print('three')"]}
                    ],
                },
            ],
        }
        pass_path = temp / "pass.json"
        pass_path.write_text(json.dumps(pass_plan), encoding="utf-8")
        passed = run_process(
            [
                sys.executable,
                "-S",
                str(script),
                "run",
                str(pass_path),
                "--format",
                "json",
                "--log-dir",
                str(temp / "pass-logs"),
            ],
            root,
        )
        try:
            passed_payload = json.loads(passed.stdout)
        except json.JSONDecodeError:
            passed_payload = {}
        if passed.returncode != 0 or passed_payload.get("status") != "passed":
            add(result, "errors", "smoke.run_pass", f"passing plan failed: {passed.stderr or passed.stdout}")

        fail_plan = {
            "name": "audit-fail",
            "cwd": str(temp),
            "stages": [
                {
                    "name": "failure",
                    "commands": [
                        {"id": "fail", "argv": [sys.executable, "-S", "-c", "raise SystemExit(3)"]}
                    ],
                },
                {
                    "name": "must-skip",
                    "commands": [
                        {"id": "skip", "argv": [sys.executable, "-S", "-c", "print('wrong')"]}
                    ],
                },
                {
                    "name": "still-run",
                    "run_if": "always",
                    "commands": [
                        {"id": "always", "argv": [sys.executable, "-S", "-c", "print('always')"]}
                    ],
                },
            ],
        }
        fail_path = temp / "fail.json"
        fail_path.write_text(json.dumps(fail_plan), encoding="utf-8")
        failed = run_process(
            [
                sys.executable,
                "-S",
                str(script),
                "run",
                str(fail_path),
                "--format",
                "json",
                "--log-dir",
                str(temp / "fail-logs"),
            ],
            root,
        )
        try:
            failed_payload = json.loads(failed.stdout)
            statuses = [stage.get("status") for stage in failed_payload.get("stages", [])]
        except json.JSONDecodeError:
            failed_payload, statuses = {}, []
        if failed.returncode != 1 or statuses != ["failed", "skipped", "passed"]:
            add(
                result,
                "errors",
                "smoke.run_failure_path",
                f"failure/skip/always semantics incorrect: return={failed.returncode} statuses={statuses}",
            )

        unsafe_plan = {
            "name": "unsafe-shell",
            "cwd": str(temp),
            "stages": [
                {"name": "unsafe", "commands": [{"id": "shell", "shell": "echo unsafe"}]}
            ],
        }
        unsafe_path = temp / "unsafe.json"
        unsafe_path.write_text(json.dumps(unsafe_plan), encoding="utf-8")
        unsafe = run_process(
            [sys.executable, "-S", str(script), "run", str(unsafe_path), "--format", "json"],
            root,
        )
        if unsafe.returncode != 2 or "--allow-shell" not in unsafe.stderr:
            add(result, "errors", "smoke.shell_guard", "shell plans must be rejected by default")

        inline_shell_plan = {
            "name": "unsafe-inline-shell",
            "cwd": str(temp),
            "stages": [
                {
                    "name": "unsafe",
                    "commands": [
                        {"id": "inline-shell", "argv": ["sh", "-c", "echo unsafe"]}
                    ],
                }
            ],
        }
        inline_shell_path = temp / "unsafe-inline-shell.json"
        inline_shell_path.write_text(json.dumps(inline_shell_plan), encoding="utf-8")
        inline_shell = run_process(
            [sys.executable, "-S", str(script), "run", str(inline_shell_path), "--format", "json"],
            root,
        )
        if inline_shell.returncode != 2 or "--allow-shell" not in inline_shell.stderr:
            add(result, "errors", "smoke.inline_shell_guard", "inline shell argv must be rejected by default")

        escape_plan = {
            "name": "cwd-escape",
            "cwd": str(temp),
            "stages": [
                {
                    "name": "escape",
                    "commands": [
                        {"id": "escape", "cwd": "..", "argv": [sys.executable, "-S", "-c", "print(1)"]}
                    ],
                }
            ],
        }
        escape_path = temp / "escape.json"
        escape_path.write_text(json.dumps(escape_plan), encoding="utf-8")
        escaped = run_process(
            [sys.executable, "-S", str(script), "run", str(escape_path), "--format", "json"],
            root,
        )
        if escaped.returncode != 2 or "escapes plan root" not in escaped.stderr:
            add(result, "errors", "smoke.cwd_guard", "command cwd escape must be rejected")

        expected_plan = {
            "name": "expected-nonzero",
            "cwd": str(temp),
            "stages": [
                {
                    "name": "expected",
                    "commands": [
                        {
                            "id": "expected",
                            "argv": [sys.executable, "-S", "-c", "raise SystemExit(3)"],
                            "expected_exit_codes": [3],
                        }
                    ],
                }
            ],
        }
        expected_path = temp / "expected.json"
        expected_path.write_text(json.dumps(expected_plan), encoding="utf-8")
        expected = run_process(
            [sys.executable, "-S", str(script), "run", str(expected_path), "--format", "json"],
            root,
        )
        if expected.returncode != 0:
            add(result, "errors", "smoke.expected_exit", "expected nonzero exit must be accepted")

        timeout_plan = {
            "name": "timeout",
            "cwd": str(temp),
            "stages": [
                {
                    "name": "timeout",
                    "commands": [
                        {
                            "id": "timeout",
                            "argv": [sys.executable, "-S", "-c", "import time; time.sleep(2)"],
                            "timeout": 1,
                        }
                    ],
                }
            ],
        }
        timeout_path = temp / "timeout.json"
        timeout_path.write_text(json.dumps(timeout_plan), encoding="utf-8")
        timed = run_process(
            [sys.executable, "-S", str(script), "run", str(timeout_path), "--format", "json"],
            root,
            timeout=8,
        )
        if timed.returncode != 1 or "timeout" not in timed.stdout.lower():
            add(result, "errors", "smoke.timeout", "timed command must fail with timeout evidence")

        invalid_bool_plan = {
            "name": "invalid-bool",
            "cwd": str(temp),
            "stages": [
                {
                    "name": "invalid",
                    "parallel": "false",
                    "commands": [{"id": "noop", "argv": [sys.executable, "-S", "-c", "pass"]}],
                }
            ],
        }
        invalid_bool_path = temp / "invalid-bool.json"
        invalid_bool_path.write_text(json.dumps(invalid_bool_plan), encoding="utf-8")
        invalid_bool = run_process(
            [sys.executable, "-S", str(script), "run", str(invalid_bool_path), "--format", "json"],
            root,
        )
        if invalid_bool.returncode != 2 or "must be a boolean" not in invalid_bool.stderr:
            add(result, "errors", "smoke.boolean_guard", "string booleans must be rejected")

        missing_behavior_plan = {
            "name": "missing-behavior",
            "cwd": str(temp),
            "require_behavior_evidence": True,
            "stages": [{"name": "static", "commands": [{"id": "lint", "argv": [sys.executable, "-S", "-c", "pass"], "evidence": "static"}]}],
        }
        missing_behavior_path = temp / "missing-behavior.json"
        missing_behavior_path.write_text(json.dumps(missing_behavior_plan), encoding="utf-8")
        missing_behavior = run_process(
            [sys.executable, "-S", str(script), "run", str(missing_behavior_path), "--format", "json"], root
        )
        if missing_behavior.returncode != 2 or "requires at least one behavior command" not in missing_behavior.stderr:
            add(result, "errors", "smoke.behavior_gate", "static-only proof must be rejected when behavior evidence is required")

        invalid_evidence_plan = {
            "name": "invalid-evidence",
            "cwd": str(temp),
            "stages": [{"name": "invalid", "commands": [{"id": "bad", "argv": [sys.executable, "-S", "-c", "pass"], "evidence": "magic"}]}],
        }
        invalid_evidence_path = temp / "invalid-evidence.json"
        invalid_evidence_path.write_text(json.dumps(invalid_evidence_plan), encoding="utf-8")
        invalid_evidence = run_process(
            [sys.executable, "-S", str(script), "run", str(invalid_evidence_path), "--format", "json"], root
        )
        if invalid_evidence.returncode != 2 or "evidence must be one of" not in invalid_evidence.stderr:
            add(result, "errors", "smoke.evidence_kind", "unknown evidence kinds must be rejected")

        unknown_plan = {
            "name": "unknown-field",
            "cwd": str(temp),
            "stages": [
                {
                    "name": "unknown",
                    "commands": [
                        {"id": "noop", "argv": [sys.executable, "-S", "-c", "pass"], "mystery": True}
                    ],
                }
            ],
        }
        unknown_path = temp / "unknown.json"
        unknown_path.write_text(json.dumps(unknown_plan), encoding="utf-8")
        unknown = run_process(
            [sys.executable, "-S", str(script), "run", str(unknown_path), "--format", "json"],
            root,
        )
        if unknown.returncode != 2 or "unexpected keys" not in unknown.stderr:
            add(result, "errors", "smoke.unknown_field", "unknown plan fields must be rejected")

        benchmark = root / "scripts" / "benchmark_efficiency.py"
        benchmark_check = run_process(
            [sys.executable, "-S", str(benchmark), str(root), "--validate-only", "--format", "json"],
            root,
        )
        try:
            benchmark_payload = json.loads(benchmark_check.stdout)
        except json.JSONDecodeError:
            benchmark_payload = {}
        if benchmark_check.returncode != 0 or not benchmark_payload.get("ok"):
            add(result, "errors", "smoke.benchmark_wiring", f"benchmark wiring failed: {benchmark_check.stderr or benchmark_check.stdout}")

    result["metrics"]["runtime_smoke_cases"] = 14


def audit(skill_root: Path, strict: bool, smoke: bool) -> dict[str, Any]:
    root = skill_root.resolve()
    result: dict[str, Any] = {
        "ok": False,
        "skill_root": str(root),
        "errors": [],
        "warnings": [],
        "metrics": {},
    }
    if not root.is_dir():
        add(result, "errors", "root.missing", f"skill root is not a directory: {root}")
        return result

    for child in root.iterdir():
        if child.name not in ALLOWED_TOP_LEVEL:
            add(result, "errors", "structure.extraneous", f"unexpected top-level entry: {child.name}")

    for required in sorted(REQUIRED_FILES):
        if not (root / required).is_file():
            add(result, "errors", "structure.required", f"missing required file: {required}")

    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if path.name in {"README.md", "CHANGELOG.md", "__pycache__", ".DS_Store", "Thumbs.db"}:
            add(result, "errors", "structure.artifact", f"remove runtime/package clutter: {rel}")
        if path.suffix in {".pyc", ".pyo"} or path.name.endswith(("~", ".bak", ".tmp", ".orig", ".rej")):
            add(result, "errors", "structure.artifact", f"remove generated or temporary artifact: {rel}")
        if path.is_symlink():
            try:
                path.resolve().relative_to(root)
            except ValueError:
                add(result, "errors", "structure.symlink_escape", f"symlink escapes skill root: {rel}")

    content_hashes: dict[str, list[str]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.name == "SKILL.md":
            continue
        if path.parts[-2] not in {"references", "scripts", "evals"}:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        content_hashes.setdefault(digest, []).append(str(path.relative_to(root)))
    for paths in content_hashes.values():
        if len(paths) > 1:
            add(result, "errors", "structure.duplicate", f"duplicate file contents: {', '.join(sorted(paths))}")

    skill_path = root / "SKILL.md"
    if not skill_path.is_file():
        return result
    text = skill_path.read_text(encoding="utf-8")
    frontmatter, error = parse_frontmatter(text)
    if error:
        add(result, "errors", "frontmatter.parse", error)
        return result
    unexpected = sorted(set(frontmatter) - ALLOWED_FRONTMATTER)
    if unexpected:
        add(result, "errors", "frontmatter.keys", f"unexpected keys: {', '.join(unexpected)}")

    name = frontmatter.get("name", "")
    description = frontmatter.get("description", "").strip()
    if not NAME_RE.fullmatch(name) or len(name) > 64:
        add(result, "errors", "frontmatter.name", f"invalid skill name: {name!r}")
    if root.name != name:
        add(result, "errors", "structure.name_mismatch", f"directory {root.name!r} != name {name!r}")
    if not description or len(description) > 1024:
        add(result, "errors", "frontmatter.description", "description is missing or too long")
    if "Use for" not in description and "Use when" not in description:
        add(result, "warnings", "trigger.use", "description should state when to use the skill")
    if "Do not use" not in description:
        add(result, "warnings", "trigger.boundary", "description should state important non-triggers")

    words = len(re.findall(r"\S+", text))
    bytes_count = len(text.encode("utf-8"))
    lines = len(text.splitlines())
    result["metrics"].update(
        {
            "skill_md_words": words,
            "skill_md_bytes": bytes_count,
            "skill_md_lines": lines,
            "skill_md_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
    )
    if words > 450:
        add(result, "errors", "efficiency.skill_words", f"SKILL.md has {words} words; maximum is 450")
    for required_phrase in ("scan -> change -> prove", "Efficiency budget", "scripts/endurant.py run", "references/repository-profile.md", "exact baseline or reproduction", "never reset, clean, overwrite", "zero-test or stale-cache greens", "bounded readiness and guaranteed cleanup"):
        if required_phrase not in text:
            add(result, "errors", "protocol.missing", f"SKILL.md missing required phrase: {required_phrase}")
    if re.search(r"\bTODO\b|\[TODO", text, flags=re.IGNORECASE):
        add(result, "errors", "skill.placeholder", "SKILL.md contains a TODO placeholder")

    baseline_path = root / "evals" / "efficiency-baseline.json"
    if baseline_path.is_file():
        try:
            efficiency = json_file(baseline_path)
            baseline = efficiency["baseline"]
            targets = efficiency["targets"]
            ratio = float(baseline["words"]) / words
            result["metrics"]["instruction_reduction_ratio"] = round(ratio, 3)
            if words > int(targets["maximum_candidate_words"]):
                add(result, "errors", "efficiency.word_target", "candidate exceeds recorded word target")
            if ratio < float(targets["minimum_instruction_reduction_ratio"]):
                add(result, "errors", "efficiency.ratio", f"instruction reduction is only {ratio:.3f}x")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            add(result, "errors", "efficiency.baseline", f"invalid efficiency baseline: {exc}")

    links = 0
    for markdown in sorted(root.rglob("*.md")):
        markdown_text = markdown.read_text(encoding="utf-8", errors="replace")
        for match in LINK_RE.finditer(markdown_text):
            target = match.group(1).split("#", 1)[0].strip()
            if not target or re.match(r"^[a-z][a-z0-9+.-]*://", target, re.IGNORECASE):
                continue
            resolved = (markdown.parent / target).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                add(result, "errors", "links.escape", f"{markdown.relative_to(root)} link escapes root: {target}")
                continue
            links += 1
            if not resolved.exists():
                add(result, "errors", "links.missing", f"{markdown.relative_to(root)} linked file missing: {target}")
            if markdown == skill_path and len(Path(target).parts) > 2:
                add(result, "warnings", "links.deep", f"keep conditional references one level deep: {target}")
    result["metrics"]["relative_links_checked"] = links

    yaml_path = root / "agents" / "openai.yaml"
    if yaml_path.is_file():
        values = parse_simple_yaml_values(yaml_path)
        short = values.get("short_description", "")
        prompt = values.get("default_prompt", "")
        if not 25 <= len(short) <= 64:
            add(result, "errors", "openai.short", f"short_description length is {len(short)}")
        if f"${name}" not in prompt:
            add(result, "errors", "openai.prompt", f"default_prompt must mention ${name}")

    compiled = 0
    help_checked = 0
    nonstdlib: dict[str, list[str]] = {}
    stdlib = set(getattr(sys, "stdlib_module_names", set()))
    for script in sorted((root / "scripts").glob("*.py")):
        try:
            source = script.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(script))
            compile(source, str(script), "exec")
            compiled += 1
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            add(result, "errors", "scripts.syntax", f"{script.name}: {exc}")
            continue
        imports: set[str] = set()
        local_python_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value.strip()
                if re.fullmatch(r"[A-Za-z0-9_.-]+\.py", value):
                    local_python_names.add(value)
        external = sorted(name for name in imports if name not in stdlib and name != "__future__")
        if external:
            nonstdlib[script.name] = external
        for local_name in sorted(local_python_names):
            if not (root / "scripts" / local_name).is_file():
                add(result, "errors", "scripts.local_reference", f"{script.name} references missing scripts/{local_name}")
        if "ArgumentParser" not in source:
            add(result, "warnings", "scripts.help", f"{script.name} lacks an argparse interface")
        if not os.access(script, os.X_OK):
            add(result, "warnings", "scripts.executable", f"{script.name} is not executable")
        help_result = run_process([sys.executable, "-S", str(script), "--help"], root, timeout=15)
        if help_result.returncode != 0:
            add(result, "errors", "scripts.help_runtime", f"{script.name} --help failed: {help_result.stderr or help_result.stdout}")
        else:
            help_checked += 1
    if nonstdlib:
        add(result, "errors", "scripts.dependencies", f"non-standard-library imports: {nonstdlib}")
    result["metrics"]["python_scripts_compiled"] = compiled
    result["metrics"]["script_help_checked"] = help_checked

    runtime_artifacts = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}
    )
    if runtime_artifacts:
        add(result, "errors", "scripts.audit_side_effect", f"audit/runtime created artifacts: {', '.join(runtime_artifacts)}")

    script_reference_re = re.compile(r"scripts/([A-Za-z0-9_.-]+\.py)")
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".json", ".yaml", ".yml", ".py"}:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for local_name in script_reference_re.findall(content):
            if not (root / "scripts" / local_name).is_file():
                add(result, "errors", "scripts.document_reference", f"{path.relative_to(root)} references missing scripts/{local_name}")

    for filename in ("evals.json", "trigger-cases.json"):
        path = root / "evals" / filename
        if not path.is_file():
            continue
        try:
            payload = json_file(path)
            if payload.get("skill_name") != name:
                add(result, "errors", "evals.skill_name", f"{filename} skill_name mismatch")
            key = "evals" if filename == "evals.json" else "cases"
            collection = payload.get(key)
            if not isinstance(collection, list) or not collection:
                add(result, "errors", "evals.cases", f"{filename} requires non-empty cases")
                continue
            ids: set[str] = set()
            for index, case in enumerate(collection):
                if not isinstance(case, dict):
                    add(result, "errors", "evals.entry", f"{filename} case {index} must be an object")
                    continue
                if filename == "evals.json":
                    case_id = case.get("id")
                    if not isinstance(case_id, str) or not case_id:
                        add(result, "errors", "evals.id", f"{filename} case {index} requires id")
                    elif case_id in ids:
                        add(result, "errors", "evals.duplicate", f"duplicate eval id: {case_id}")
                    else:
                        ids.add(case_id)
                    if not isinstance(case.get("prompt"), str) or not case.get("prompt"):
                        add(result, "errors", "evals.prompt", f"{case_id or index}: prompt required")
                    assertions = case.get("assertions")
                    if not isinstance(assertions, list) or not assertions:
                        add(result, "errors", "evals.assertions", f"{case_id or index}: assertions required")
                else:
                    if not isinstance(case.get("prompt"), str) or not isinstance(case.get("should_trigger"), bool):
                        add(result, "errors", "evals.trigger", f"trigger case {index} requires prompt and boolean should_trigger")
            result["metrics"]["eval_cases" if filename == "evals.json" else "trigger_cases"] = len(collection)
        except (OSError, json.JSONDecodeError) as exc:
            add(result, "errors", "evals.json", f"{filename}: {exc}")

    validate_capability_contract(root, result, name)

    if smoke and not result["errors"]:
        smoke_runner(root, result)

    if strict and result["warnings"]:
        for warning in result["warnings"]:
            result["errors"].append(
                {"code": f"strict.{warning['code']}", "message": warning["message"]}
            )

    result["ok"] = not result["errors"]
    return result


def render_text(result: dict[str, Any]) -> str:
    lines = [f"{'PASS' if result['ok'] else 'FAIL'}: {result['skill_root']}"]
    lines.extend(f"ERROR {item['code']}: {item['message']}" for item in result["errors"])
    lines.extend(f"WARN  {item['code']}: {item['message']}" for item in result["warnings"])
    if result["metrics"]:
        lines.append(
            "METRICS "
            + ", ".join(f"{key}={value}" for key, value in sorted(result["metrics"].items()))
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_root", nargs="?", default=".")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args()
    try:
        result = audit(
            Path(args.skill_root),
            strict=args.strict,
            smoke=(args.strict and not args.skip_smoke),
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "runtime_error": str(exc)}, indent=2))
        return 2
    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_text(result), end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
