#!/usr/bin/env python3
"""External functional grader for the settings fixture."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def load_function(workspace: Path):
    path = workspace / "src" / "settings.py"
    spec = importlib.util.spec_from_file_location("graded_settings", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.merge_settings


def main_insertion(path: Path) -> tuple[list[str], int, str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "main"
        ),
        None,
    )
    if main_function is None or not main_function.body:
        raise RuntimeError("settings CLI has no instrumentable main()")
    first_body = main_function.body[0]
    insertion_line = first_body.lineno - 1
    if (
        isinstance(first_body, ast.Expr)
        and isinstance(first_body.value, ast.Constant)
        and isinstance(first_body.value.value, str)
    ):
        insertion_line = int(first_body.end_lineno or first_body.lineno)
    indentation = " " * int(first_body.col_offset)
    return source.splitlines(keepends=True), insertion_line, indentation


def inject_cli_marker(path: Path) -> None:
    lines, insertion_line, indentation = main_insertion(path)
    marker = (
        indentation
        + "import os as _eval_os; from pathlib import Path as _EvalPath; "
        "_eval_marker = _eval_os.environ.get('EVAL_CLI_INVOCATION_MARKER'); "
        "_eval_marker and _EvalPath(_eval_marker).write_text("
        "f'{_eval_os.getpid()}:{_eval_os.getppid()}')\n"
    )
    lines.insert(insertion_line, marker)
    path.write_text("".join(lines), encoding="utf-8")


def inject_cli_false_mutant(path: Path) -> None:
    lines, insertion_line, indentation = main_insertion(path)
    mutation = (
        indentation + "_eval_original_merge = globals()['merge_settings']\n"
        + indentation + "def _eval_cli_merge(defaults, overrides):\n"
        + indentation + "    result = _eval_original_merge(defaults, overrides)\n"
        + indentation + "    for key, default in defaults.items():\n"
        + indentation + "        if key in overrides and overrides[key] is False:\n"
        + indentation + "            result[key] = default\n"
        + indentation + "    return result\n"
        + indentation + "merge_settings = _eval_cli_merge\n"
    )
    lines.insert(insertion_line, mutation)
    path.write_text("".join(lines), encoding="utf-8")


def assertion_failure(completed: subprocess.CompletedProcess[str]) -> bool:
    output = completed.stdout + "\n" + completed.stderr
    has_assertion = "FAIL:" in output or "AssertionError" in output
    has_unrelated_error = any(
        marker in output
        for marker in (
            "ERROR:",
            "ImportError",
            "ModuleNotFoundError",
            "SyntaxError",
            "IndentationError",
        )
    )
    return completed.returncode != 0 and has_assertion and not has_unrelated_error


def run_test_module(
    workspace: Path, module: str, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "unittest", module, "-v"],
        cwd=workspace,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )


def regression_tests_reject_false_mutant(workspace: Path) -> dict[str, bool]:
    with tempfile.TemporaryDirectory(prefix="settings-mutation-grade-") as temp_value:
        mutated = Path(temp_value)
        shutil.copytree(workspace / "src", mutated / "src")
        shutil.copytree(workspace / "tests", mutated / "tests")
        cli_path = mutated / "src" / "settings_cli.py"
        marker_path = mutated / ".cli-invoked"
        inject_cli_marker(cli_path)
        environment = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "EVAL_CLI_INVOCATION_MARKER": str(marker_path),
        }
        candidate_unit = run_test_module(mutated, "tests.test_settings", environment)
        marker_path.unlink(missing_ok=True)
        candidate_cli = run_test_module(mutated, "tests.test_settings_cli", environment)
        candidate_cli_invoked = marker_path.is_file()

        settings_path = mutated / "src" / "settings.py"
        candidate_source = settings_path.read_text(encoding="utf-8")
        settings_path.write_text(
            candidate_source
            + "\n\n_endurant_candidate_merge_settings = merge_settings\n"
            + "def merge_settings(defaults, overrides):\n"
            + "    result = _endurant_candidate_merge_settings(defaults, overrides)\n"
            + "    for key, default in defaults.items():\n"
            + "        if key in overrides and overrides[key] is False:\n"
            + "            result[key] = default\n"
            + "    return result\n",
            encoding="utf-8",
        )
        mutant_unit = run_test_module(mutated, "tests.test_settings", environment)
        settings_path.write_text(candidate_source, encoding="utf-8")
        inject_cli_false_mutant(cli_path)
        marker_path.unlink(missing_ok=True)
        mutant_cli = run_test_module(mutated, "tests.test_settings_cli", environment)
        mutant_cli_invoked = marker_path.is_file()
        marker_parent_is_test_process = False
        if candidate_cli_invoked and mutant_cli_invoked:
            try:
                marker_pid, marker_parent = (
                    int(value)
                    for value in marker_path.read_text(encoding="utf-8").split(":", 1)
                )
                marker_parent_is_test_process = bool(
                    marker_pid != marker_parent and marker_parent != os.getpid()
                )
            except (OSError, ValueError):
                marker_parent_is_test_process = False
        cli_test_source = (mutated / "tests" / "test_settings_cli.py").read_text(
            encoding="utf-8", errors="replace"
        )
        return {
            "unit_regression_passes_candidate": candidate_unit.returncode == 0,
            "cli_regression_passes_candidate": candidate_cli.returncode == 0,
            "cli_regression_invokes_entrypoint": bool(
                candidate_cli_invoked
                and mutant_cli_invoked
                and marker_parent_is_test_process
                and "src.settings_cli" in cli_test_source
                and "subprocess" in cli_test_source
            ),
            "unit_regression_detects_bug": assertion_failure(mutant_unit),
            "cli_regression_detects_bug": assertion_failure(mutant_cli),
        }


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    merge_settings = load_function(workspace)
    defaults = {
        "enabled": True,
        "visible": False,
        "limit": 10,
        "retries": 3,
        "label": "default",
    }
    overrides = {
        "enabled": False,
        "visible": True,
        "limit": None,
        "retries": 0,
        "label": "",
        "unknown": "ignored",
    }
    defaults_before = dict(defaults)
    overrides_before = dict(overrides)
    result = merge_settings(defaults, overrides)
    cli = subprocess.run(
        [sys.executable, "-m", "src.settings_cli"],
        cwd=workspace,
        input=json.dumps({"defaults": defaults, "overrides": overrides}),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    cli_result = json.loads(cli.stdout) if cli.returncode == 0 else None
    expected = {
        "enabled": False,
        "visible": True,
        "limit": 10,
        "retries": 0,
        "label": "",
    }
    checks = {
        "signature": list(inspect.signature(merge_settings).parameters) == ["defaults", "overrides"],
        "false_preserved": result["enabled"] is False,
        "true_preserved": result["visible"] is True,
        "none_uses_default": result["limit"] == 10,
        "zero_preserved": result["retries"] == 0,
        "empty_string_preserved": result["label"] == "",
        "unknown_ignored": "unknown" not in result,
        "inputs_not_mutated": defaults == defaults_before and overrides == overrides_before,
        "cli_behavior": cli_result == expected,
        **regression_tests_reject_false_mutant(workspace),
    }
    print(json.dumps({"passed": all(checks.values()), "checks": checks}, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
