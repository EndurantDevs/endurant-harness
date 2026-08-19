from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "lab"
sys.path.insert(0, str(LAB))

import run_delegation_experiment as experiment  # noqa: E402


class DelegationExperimentTests(unittest.TestCase):
    def test_balanced_schedule_and_collab_receipt(self) -> None:
        schedule = experiment.schedule(1)
        self.assertEqual(len(schedule), 9)
        for arm in (item["name"] for item in experiment.ARMS):
            selected = [row for row in schedule if row["arm"] == arm]
            self.assertEqual(len(selected), 3)
            self.assertEqual({row["position"] for row in selected}, {1, 2, 3})

        with tempfile.TemporaryDirectory(prefix="delegation-events-") as raw:
            path = Path(raw) / "events.jsonl"
            events = [
                {
                    "type": "item.started",
                    "item": {
                        "id": "call-1",
                        "type": "collab_tool_call",
                        "tool": "spawn_agent",
                        "receiver_thread_ids": [],
                        "agents_states": {},
                        "status": "in_progress",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": "call-1",
                        "type": "collab_tool_call",
                        "tool": "spawn_agent",
                        "receiver_thread_ids": ["child-1"],
                        "agents_states": {"child-1": {"status": "running"}},
                        "status": "completed",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": "call-2",
                        "type": "collab_tool_call",
                        "tool": "wait",
                        "receiver_thread_ids": ["child-1"],
                        "agents_states": {"child-1": {"status": "completed"}},
                        "status": "completed",
                    },
                },
            ]
            path.write_text(
                "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
            )
            metrics = experiment.delegation_metrics(path)
        self.assertEqual(metrics["calls_by_tool"], {"spawn_agent": 1, "wait": 1})
        self.assertEqual(metrics["root_observed_spawned_agent_count"], 1)
        self.assertEqual(metrics["last_observed_agent_states"], {"child-1": "completed"})
        self.assertTrue(metrics["all_root_observed_children_terminal"])
        self.assertEqual(metrics["failed_call_count"], 0)
        self.assertFalse(metrics["child_token_usage_available"])
        self.assertFalse(metrics["total_usage_exact"])

    def test_contract_pins_binary_and_all_arms_enable_subagents(self) -> None:
        with tempfile.TemporaryDirectory(prefix="delegation-binary-") as raw:
            binary = Path(raw) / "codex"
            binary.write_text("#!/bin/sh\necho codex-cli-test\n", encoding="utf-8")
            binary.chmod(0o700)
            contract = experiment.build_contract(binary, repeats=1, timeout=60)
        self.assertEqual(contract["codex"]["version"], "codex-cli-test")
        self.assertEqual({arm["subagents"] for arm in contract["arms"]}, {"enabled"})
        self.assertEqual(
            [arm["reasoning_effort"] for arm in contract["arms"]],
            ["max", "ultra", "max"],
        )
        self.assertEqual(len(contract["contract_sha256"]), 64)

    def test_selective_policy_rejects_excess_or_unfinished_root_children(self) -> None:
        base = {
            "root_observed_spawned_agent_count": 0,
            "failed_call_count": 0,
            "all_root_observed_children_terminal": True,
        }
        self.assertTrue(
            experiment.observed_policy_pass(
                "max-selective-overlay", "settings-override-correctness", base
            )
        )
        self.assertFalse(
            experiment.observed_policy_pass(
                "max-selective-overlay",
                "settings-override-correctness",
                {**base, "root_observed_spawned_agent_count": 1},
            )
        )
        self.assertFalse(
            experiment.observed_policy_pass(
                "max-selective-overlay",
                "record-selection-performance",
                {**base, "all_root_observed_children_terminal": False},
            )
        )

    def test_summary_does_not_treat_missing_usage_as_zero(self) -> None:
        rows = []
        for usage in (
            {"input_tokens": 100, "output_tokens": 20},
            {},
            {"input_tokens": 180, "output_tokens": 40},
        ):
            rows.append(
                {
                    "arm": "max-harness-control",
                    "passed": True,
                    "functional_passed": True,
                    "duration_seconds": 1,
                    "root_reported_usage": usage,
                    "delegation": {"root_observed_spawned_agent_count": 0},
                }
            )
        summary = experiment.summarize(rows)["max-harness-control"]
        self.assertEqual(summary["median_root_reported_tokens"], 170)
        self.assertEqual(summary["root_usage_available"], 2)
        self.assertEqual(summary["root_usage_missing"], 1)


if __name__ == "__main__":
    unittest.main()
