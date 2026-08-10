from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
ROOT = LAB.parent
sys.path.insert(0, str(LAB))

from eval_lib import git_state, tree_manifest  # noqa: E402
from grade_run import (  # noqa: E402
    event_at_or_after,
    event_before,
    events_for_run,
    load_observed_agent_events,
    observed_at_or_after,
    observed_before,
)
from run_agent import observe_agent_events, scoped_manifest  # noqa: E402


class EvaluationIntegrityTests(unittest.TestCase):
    def test_subject_manifest_detects_symlink_and_mode_changes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="subject-manifest-") as value:
            root = Path(value)
            source = root / "SKILL.md"
            source.write_text("baseline\n", encoding="utf-8")
            baseline = tree_manifest(root)

            link = root / "added-link"
            link.symlink_to(source)
            with_link = tree_manifest(root)
            self.assertNotEqual(with_link, baseline)
            self.assertEqual(with_link["added-link"]["type"], "symlink")

            link.unlink()
            source.chmod(0o755)
            self.assertNotEqual(tree_manifest(root), baseline)

    def test_git_state_detects_index_and_head_changes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="git-state-") as value:
            root = Path(value)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            source = root / "source.txt"
            source.write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "add", "source.txt"], cwd=root, check=True)
            baseline = git_state(root)

            source.write_text("changed\n", encoding="utf-8")
            subprocess.run(["git", "add", "source.txt"], cwd=root, check=True)
            self.assertNotEqual(git_state(root), baseline)

            subprocess.run(["git", "config", "user.name", "Synthetic Test"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "synthetic@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=root, check=True)
            self.assertNotEqual(git_state(root), baseline)

    def test_event_binding_and_order_helpers(self) -> None:
        events = [
            {"actor": "agent", "run_id": "wanted", "timestamp_ns": 10},
            {"actor": "agent", "run_id": "other", "timestamp_ns": 20},
            {"actor": "grader", "run_id": "wanted", "timestamp_ns": 30},
        ]
        selected = events_for_run(events, "agent", "wanted")
        self.assertEqual(selected, [events[0]])
        self.assertTrue(event_before(selected[0], 11))
        self.assertFalse(event_before(selected[0], 10))
        self.assertTrue(event_at_or_after(selected[0], 10))
        self.assertFalse(event_at_or_after(selected[0], 11))
        self.assertFalse(event_before(selected[0], True))

    def test_first_edit_scope_ignores_workflow_artifacts(self) -> None:
        manifest = {
            "src/settings.py": "source",
            "tests/test_settings.py": "test",
            ".endurant-proof-plan.json": "plan",
        }
        self.assertEqual(
            scoped_manifest(manifest, ["src/"]),
            {"src/settings.py": "source"},
        )

    def test_runner_observation_ignores_backdated_agent_timestamp(self) -> None:
        with tempfile.TemporaryDirectory(prefix="observed-events-") as value:
            root = Path(value)
            observed = root / "observed.jsonl"
            observed.write_text(
                json.dumps(
                    {
                        "sequence": 0,
                        "observed_monotonic_ns": 200,
                        "event": {
                            "actor": "agent",
                            "run_id": "run",
                            "timestamp_ns": 1,
                            "gate": "synthetic",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events, valid = load_observed_agent_events(observed)
            self.assertTrue(valid)
            self.assertFalse(observed_before(events[0], 100))
            self.assertTrue(observed_at_or_after(events[0], 100))

    def test_runner_observer_detects_agent_log_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="event-observer-") as value:
            root = Path(value)
            source = root / "agent.jsonl"
            observed = root / "observed.jsonl"
            source.write_text('{"gate":"first"}\n', encoding="utf-8")
            lines, tampered = observe_agent_events(source, observed, [])
            self.assertFalse(tampered)
            self.assertEqual(len(lines), 1)
            source.write_text('{"gate":"backdated"}\n', encoding="utf-8")
            _, tampered = observe_agent_events(source, observed, lines)
            self.assertTrue(tampered)

    def _grade_settings(
        self,
        relevant_tests: bool,
        sentinel_only: bool = False,
        import_only_cli: bool = False,
        ignored_cli_output: bool = False,
    ) -> dict[str, object]:
        fixture = ROOT / "fixtures" / "settings-override-correctness"
        with tempfile.TemporaryDirectory(prefix="settings-grade-") as value:
            workspace = Path(value) / "workspace"
            shutil.copytree(fixture / "template", workspace)
            (workspace / "src" / "settings.py").write_text(
                '"""Settings merge behavior."""\n\n'
                + ("SENTINEL = 1\n\n" if sentinel_only else "")
                + "def merge_settings(defaults, overrides):\n"
                "    return {\n"
                "        key: overrides[key] if key in overrides and overrides[key] is not None else default\n"
                "        for key, default in defaults.items()\n"
                "    }\n",
                encoding="utf-8",
            )
            if sentinel_only:
                unit_source = (
                    "import unittest\n"
                    "from src.settings import SENTINEL\n\n"
                    "class UnrelatedUnit(unittest.TestCase):\n"
                    "    def test_sentinel(self): self.assertEqual(SENTINEL, 1)\n"
                )
                cli_source = (
                    "import unittest\n"
                    "from src.settings import SENTINEL\n\n"
                    "class UnrelatedCli(unittest.TestCase):\n"
                    "    def test_sentinel(self): self.assertEqual(SENTINEL, 1)\n"
                )
            elif relevant_tests:
                unit_source = (
                    "import unittest\n"
                    "from src.settings import merge_settings\n\n"
                    "class SettingsRegression(unittest.TestCase):\n"
                    "    def test_falsy_values(self):\n"
                    "        self.assertEqual(merge_settings({'v': True}, {'v': False}), {'v': False})\n"
                )
                cli_source = (
                    "import json, subprocess, sys, unittest\n\n"
                    "class CliRegression(unittest.TestCase):\n"
                    "    def test_false_override(self):\n"
                    "        result = subprocess.run([sys.executable, '-m', 'src.settings_cli'], "
                    "input=json.dumps({'defaults': {'v': True}, 'overrides': {'v': False}}), "
                    "text=True, stdout=subprocess.PIPE, check=True)\n"
                    "        self.assertEqual(json.loads(result.stdout), {'v': False})\n"
                )
                if import_only_cli:
                    cli_source = (
                        "import subprocess, unittest\n"
                        "import src.settings_cli\n"
                        "from src.settings import merge_settings\n\n"
                        "class NotACliRegression(unittest.TestCase):\n"
                        "    def test_false_directly(self):\n"
                        "        self.assertEqual(merge_settings({'v': True}, {'v': False}), {'v': False})\n"
                    )
                elif ignored_cli_output:
                    cli_source = (
                        "import json, subprocess, sys, unittest\n"
                        "from src.settings import merge_settings\n\n"
                        "class IgnoredCliOutput(unittest.TestCase):\n"
                        "    def test_false_directly(self):\n"
                        "        subprocess.run([sys.executable, '-m', 'src.settings_cli'], "
                        "input=json.dumps({'defaults': {'v': True}, 'overrides': {'v': False}}), "
                        "text=True, stdout=subprocess.PIPE, check=True)\n"
                        "        self.assertEqual(merge_settings({'v': True}, {'v': False}), {'v': False})\n"
                    )
            else:
                unit_source = "import unittest\nclass Placeholder(unittest.TestCase):\n    def test_one(self): self.assertTrue(True)\n"
                cli_source = "import unittest\nclass Placeholder(unittest.TestCase):\n    def test_two(self): self.assertTrue(True)\n"
            (workspace / "tests" / "test_settings.py").write_text(unit_source, encoding="utf-8")
            (workspace / "tests" / "test_settings_cli.py").write_text(
                cli_source, encoding="utf-8"
            )
            completed = subprocess.run(
                [sys.executable, str(fixture / "hidden_grade.py"), str(workspace)],
                cwd=fixture,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertTrue(completed.stdout.strip(), completed.stderr)
            return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_hidden_grade_rejects_placeholder_regressions(self) -> None:
        result = self._grade_settings(relevant_tests=False)
        self.assertFalse(result["passed"])
        checks = result["checks"]
        self.assertFalse(checks["unit_regression_detects_bug"])
        self.assertFalse(checks["cli_regression_detects_bug"])

    def test_hidden_grade_accepts_unit_and_cli_regressions(self) -> None:
        result = self._grade_settings(relevant_tests=True)
        self.assertTrue(result["passed"], result)

    def test_hidden_grade_rejects_candidate_only_sentinel_tests(self) -> None:
        result = self._grade_settings(relevant_tests=False, sentinel_only=True)
        self.assertFalse(result["passed"])
        checks = result["checks"]
        self.assertFalse(checks["unit_regression_detects_bug"])
        self.assertFalse(checks["cli_regression_detects_bug"])
        self.assertFalse(checks["cli_regression_invokes_entrypoint"])

    def test_hidden_grade_rejects_cli_import_and_direct_function_test(self) -> None:
        result = self._grade_settings(relevant_tests=True, import_only_cli=True)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["cli_regression_invokes_entrypoint"])

    def test_hidden_grade_rejects_ignored_cli_output(self) -> None:
        result = self._grade_settings(relevant_tests=True, ignored_cli_output=True)
        self.assertFalse(result["passed"])
        self.assertTrue(result["checks"]["cli_regression_invokes_entrypoint"])
        self.assertFalse(result["checks"]["cli_regression_detects_bug"])


if __name__ == "__main__":
    unittest.main()
