from __future__ import annotations

import copy
import importlib.util
import json
import platform
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "lab"
sys.path.insert(0, str(LAB))

SCRIPT = LAB / "run_promotion_campaign.py"
SPEC = importlib.util.spec_from_file_location("run_promotion_campaign", SCRIPT)
campaign = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(campaign)
RUN_AGENT_SPEC = importlib.util.spec_from_file_location("run_agent", LAB / "run_agent.py")
run_agent = importlib.util.module_from_spec(RUN_AGENT_SPEC)
assert RUN_AGENT_SPEC.loader is not None
RUN_AGENT_SPEC.loader.exec_module(run_agent)


class PromotionCampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan_path = ROOT / "lab" / "campaigns" / "adaptive-v6.json"
        self.audit_fixture = ROOT / "fixtures" / "record-selection-receipt"
        self.codex_version = "codex-cli-test"

    def freeze(self, root: Path) -> Path:
        target = root / "campaign"
        campaign.freeze(
            self.plan_path,
            target,
            audit_fixture=self.audit_fixture,
            codex_version=self.codex_version,
        )
        return target

    def record(
        self,
        *,
        phase: str,
        pair: int,
        arm: str,
        duration: float,
        task_passed: bool = True,
        fixture: str = "settings-override-correctness",
    ) -> dict[str, object]:
        return {
            "arm": arm,
            "capture": "artifacts/runs/synthetic",
            "capture_hashes": {},
            "command_items": 2,
            "codex_version": self.codex_version,
            "duration_seconds": duration,
            "fixture": fixture,
            "frozen_sha256": "set-by-append-phase",
            "integrity_gates": {"synthetic": True},
            "model": "gpt-5.6-terra",
            "pair": pair,
            "phase": phase,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "reasoning_effort": "low",
            "run_id": f"{phase}-{pair}-{fixture}-{arm}",
            "run_integrity_passed": True,
            "runner_returncode": 0,
            "task_passed": task_passed,
            "uncached_input_tokens": 1000,
            "usage": {},
        }

    def append_phase(
        self,
        target: Path,
        phase: str,
        pairs: int,
        parent_duration: float,
        candidate_duration: float,
        *,
        candidate_task_passed: bool = True,
        parent_task_passed: bool = True,
        fixture: str | None = None,
    ) -> None:
        frozen_sha256 = campaign.validate_frozen(target)["frozen_sha256"]
        changed = "noop" if phase == "aa" else "candidate"
        for pair in range(1, pairs + 1):
            for fixture_id in [fixture] if fixture else self.plan()["fixtures"]:
                records = [
                    self.record(
                        phase=phase,
                        pair=pair,
                        arm="parent",
                        duration=parent_duration,
                        task_passed=parent_task_passed,
                        fixture=fixture_id,
                    ),
                    self.record(
                        phase=phase,
                        pair=pair,
                        arm=changed,
                        duration=candidate_duration,
                        task_passed=candidate_task_passed,
                        fixture=fixture_id,
                    ),
                ]
                if pair % 2 == 0:
                    records.reverse()
                for record in records:
                    record["frozen_sha256"] = frozen_sha256
                    campaign.append_event(target, {"kind": "run", "record": record})

    def rewrite_events(self, target: Path, events: list[dict[str, object]]) -> None:
        previous = "0" * 64
        lines: list[str] = []
        for sequence, event in enumerate(events):
            event["sequence"] = sequence
            event["previous_event_sha256"] = previous
            event.pop("event_sha256", None)
            event["event_sha256"] = campaign.canonical_sha256(event)
            previous = event["event_sha256"]
            lines.append(json.dumps(event, separators=(",", ":"), sort_keys=True))
        (target / "campaign-events.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def seal_lineage(self, target: Path) -> None:
        frozen = campaign.validate_frozen(target)
        attestation = target.parent / "lineage-input.json"
        attestation.write_text(
            json.dumps(
                {
                    "attested_by": "synthetic-maintainer",
                    "candidate_sha256": frozen["candidate"]["sha256"],
                    "causal_minimum": True,
                    "frozen_sha256": frozen["frozen_sha256"],
                    "lineage_kind": "single-candidate",
                    "rationale": "The one proposed mechanism has no independent addition.",
                    "schema_version": 1,
                    "tested_removals": [],
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(campaign, "verify_capture_hashes", return_value=[]):
            campaign.seal_lineage(target, attestation)

    def test_mining_clusters_recurring_failures_and_retains_successes(self) -> None:
        traces = campaign.load_traces(ROOT / "lab" / "evals" / "adaptive-traces.json")
        result = campaign.mine_traces(traces, min_support=2)
        self.assertEqual(result["trace_count"], 8)
        self.assertEqual([item["support"] for item in result["clusters"]], [3, 3])
        self.assertEqual(len(result["protected_success_ids"]), 2)
        self.assertIn("authorized-recovery", self.plan()["fixtures"])

    def test_freeze_binds_parent_candidate_noop_and_runner_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="promotion-freeze-") as raw:
            target = self.freeze(Path(raw))
            frozen = campaign.validate_frozen(target)
            self.assertEqual(frozen["parent"]["sha256"], frozen["noop"]["sha256"])
            self.assertEqual(frozen["parent"]["revision"], self.plan()["parent_revision"])
            self.assertEqual(frozen["mining"]["trace_count"], 8)
            self.assertIn("lab/eval_lib.py", frozen["runner_inputs"])
            self.assertIn("lab/run_promotion_campaign.py", frozen["runner_inputs"])
            self.assertEqual(
                frozen["audit_fixture"]["sha256"], self.plan()["audit_fixture_sha256"]
            )
            self.assertNotIn("record-selection-receipt", json.dumps(frozen))
            audit = target / frozen["audit_fixture"]["path"]
            self.assertEqual(stat.S_IMODE(audit.stat().st_mode), 0)
            with self.assertRaises(PermissionError):
                (audit / "task.txt").read_text(encoding="utf-8")
            with run_agent.fixture_source_access(audit, True):
                self.assertIn("repository", (audit / "task.txt").read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(audit.stat().st_mode), 0)
            audit.chmod(0o700)
            with self.assertRaisesRegex(campaign.CampaignError, "not sealed"):
                campaign.validate_frozen(target)
            audit.chmod(0)
            candidate = target / frozen["candidate"]["path"] / "references" / "protocol.md"
            candidate.write_text(candidate.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "candidate package drift"):
                campaign.validate_frozen(target)

    def plan(self) -> dict[str, object]:
        return json.loads(self.plan_path.read_text(encoding="utf-8"))

    def test_plan_rejects_small_samples_and_multiple_or_outside_mechanisms(self) -> None:
        with tempfile.TemporaryDirectory(prefix="promotion-plan-") as raw:
            path = Path(raw) / "plan.json"
            value = self.plan()
            value["aa_pairs"] = 4
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "aa_pairs"):
                campaign.validate_plan(path)

            value = self.plan()
            value["proposal"]["mechanism"] = ""
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "mechanism"):
                campaign.validate_plan(path)

            value = self.plan()
            value["audit_fixture_sha256"] = "A" * 64
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "lowercase SHA-256"):
                campaign.validate_plan(path)

            value = self.plan()
            value["proposal"]["proposer_model"] = None
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "proposer model and effort"):
                campaign.validate_plan(path)

            value = self.plan()
            value["proposal"]["origin"] = "human-authored"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "must not name"):
                campaign.validate_plan(path)

            value = self.plan()
            value["fixtures"] = ["../lab"]
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "fixture"):
                campaign.freeze(
                    path,
                    Path(raw) / "escape",
                    audit_fixture=self.audit_fixture,
                    codex_version=self.codex_version,
                )

    def test_plan_binds_proposal_and_evaluation_lineage_separately(self) -> None:
        plan = campaign.validate_plan(self.plan_path)
        self.assertEqual(plan["proposal"]["origin"], "human-directed Codex task")
        self.assertEqual(plan["proposal"]["proposer_model"], "gpt-5.6-sol")
        self.assertEqual(plan["proposal"]["proposer_reasoning_effort"], "xhigh")
        self.assertEqual((plan["model"], plan["reasoning_effort"]), ("gpt-5.6-terra", "low"))

    def test_merged_winner_attestation_requires_a_tested_removal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="promotion-lineage-") as raw:
            target = self.freeze(Path(raw))
            frozen = campaign.validate_frozen(target)
            attestation = {
                "attested_by": "synthetic-maintainer",
                "candidate_sha256": frozen["candidate"]["sha256"],
                "causal_minimum": True,
                "frozen_sha256": frozen["frozen_sha256"],
                "lineage_kind": "merged-winner",
                "rationale": "No removal evidence was supplied.",
                "schema_version": 1,
                "tested_removals": [],
            }
            with self.assertRaisesRegex(campaign.CampaignError, "requires tested_removals"):
                campaign.validate_lineage_attestation(attestation, frozen)

    def test_hash_chained_ledger_rejects_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="promotion-ledger-") as raw:
            target = self.freeze(Path(raw))
            campaign.append_event(target, {"kind": "note", "value": 1})
            campaign.append_event(target, {"kind": "note", "value": 2})
            self.assertEqual(len(campaign.ledger_events(target)), 2)
            path = target / "campaign-events.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            first = json.loads(lines[0])
            first["value"] = 9
            lines[0] = json.dumps(first, sort_keys=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "hash chain"):
                campaign.ledger_events(target)

    def test_aa_noise_candidate_confirmation_and_audit_decide_eligibility(self) -> None:
        with tempfile.TemporaryDirectory(prefix="promotion-decision-") as raw:
            target = self.freeze(Path(raw))
            self.append_phase(target, "aa", 5, 10.0, 10.2)
            aa = campaign.phase_summary(target, "aa")
            self.assertTrue(aa["passed"])
            self.assertEqual(aa["decision"], "continue")

            self.append_phase(target, "development", 5, 10.0, 8.0)
            development = campaign.phase_summary(target, "development")
            self.assertTrue(development["passed"])
            self.assertEqual(development["decision"], "candidate")
            self.assertGreaterEqual(development["median_improvement_fraction"], 0.15)

            self.append_phase(target, "confirmation", 5, 10.0, 8.0)
            with self.assertRaisesRegex(campaign.CampaignError, "lineage must be sealed"):
                campaign._run_phase(target, "audit")
            self.seal_lineage(target)
            self.append_phase(
                target,
                "audit",
                1,
                10.0,
                8.0,
                fixture="sealed-audit",
            )
            result = campaign.campaign_decision(target)
            self.assertEqual(result["decision"], "candidate")
            self.assertTrue(result["human_authorization_required"])
            self.assertEqual(result["evidence_tier"], "promotion-audited")
            self.assertNotIn("promotion_eligible", result)
            self.assertEqual(result["lineage_seal"]["lineage_kind"], "single-candidate")
            attestation = target / "lineage-attestation.json"
            changed = json.loads(attestation.read_text(encoding="utf-8"))
            changed["rationale"] = "rewritten after the audit"
            attestation.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "lineage seal differs"):
                campaign.campaign_decision(target)

    def test_noop_is_valid_and_protected_success_cannot_regress(self) -> None:
        with tempfile.TemporaryDirectory(prefix="promotion-noop-") as raw:
            target = self.freeze(Path(raw))
            self.append_phase(target, "aa", 5, 10.0, 10.0)
            self.append_phase(target, "development", 5, 10.0, 9.8)
            self.assertEqual(
                campaign.phase_summary(target, "development")["decision"], "no-op"
            )
            self.assertEqual(campaign.campaign_decision(target)["decision"], "no-op")

        with tempfile.TemporaryDirectory(prefix="promotion-regression-") as raw:
            target = self.freeze(Path(raw))
            self.append_phase(target, "aa", 5, 10.0, 10.0)
            self.append_phase(
                target,
                "development",
                5,
                10.0,
                7.0,
                candidate_task_passed=False,
            )
            summary = campaign.phase_summary(target, "development")
            self.assertFalse(summary["no_pair_regressed"])
            self.assertFalse(summary["passed"])
            self.assertEqual(summary["decision"], "reject")

    def test_universal_failure_and_runtime_drift_cannot_select_candidate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="promotion-failure-") as raw:
            target = self.freeze(Path(raw))
            self.append_phase(target, "aa", 5, 10.0, 10.0)
            self.append_phase(
                target,
                "development",
                5,
                10.0,
                7.0,
                parent_task_passed=False,
                candidate_task_passed=False,
            )
            summary = campaign.phase_summary(target, "development")
            self.assertEqual(summary["candidate_successes"], 0)
            self.assertEqual(summary["decision"], "reject")

        with tempfile.TemporaryDirectory(prefix="promotion-runtime-") as raw:
            target = self.freeze(Path(raw))
            self.append_phase(target, "aa", 5, 10.0, 10.0)
            self.append_phase(target, "development", 5, 10.0, 7.0)
            events = campaign.ledger_events(target)
            record = next(
                event["record"]
                for event in events
                if event.get("kind") == "run"
                and event["record"]["phase"] == "development"
            )
            record["python"] = "different-runtime"
            self.rewrite_events(target, events)
            summary = campaign.phase_summary(target, "development")
            self.assertFalse(summary["environment_fixed"])
            self.assertEqual(summary["decision"], "insufficient-evidence")

    def test_incomplete_start_raw_capture_and_lock_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="promotion-resume-") as raw:
            target = self.freeze(Path(raw))
            campaign.append_event(
                target,
                {
                    "kind": "started",
                    "slot": {
                        "arm": "parent",
                        "fixture": "settings-override-correctness",
                        "pair": 1,
                        "phase": "aa",
                    },
                },
            )
            with self.assertRaisesRegex(campaign.CampaignError, "incomplete started"):
                campaign.validate_resume_state(campaign.ledger_events(target))

            capture = Path(raw) / "capture"
            capture.mkdir()
            with self.assertRaisesRegex(campaign.CampaignError, "incomplete"):
                campaign.run_record(
                    capture,
                    campaign.validate_frozen(target),
                    target,
                    phase="aa",
                    pair=1,
                    arm="parent",
                    fixture="settings-override-correctness",
                )

            lock = target / ".campaign.lock"
            lock.mkdir()
            with self.assertRaisesRegex(campaign.CampaignError, "locked"):
                campaign.run_phase(target, "aa")
            with self.assertRaisesRegex(campaign.CampaignError, "locked"):
                campaign.verify_campaign(target)

    def test_completed_record_is_recomputed_from_raw_capture(self) -> None:
        (ROOT / "artifacts" / "runs").mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="promotion-raw-") as raw:
            raw_path = Path(raw)
            plan = self.plan()
            plan["id"] = raw_path.name.replace("_", "-")
            plan_path = raw_path / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            target = raw_path / "campaign"
            campaign.freeze(
                plan_path,
                target,
                audit_fixture=self.audit_fixture,
                codex_version=self.codex_version,
            )
            frozen = campaign.validate_frozen(target)
            run_id = f"{plan['id']}-aa-1-settings-override-correctness-parent"
            capture = ROOT / "artifacts" / "runs" / run_id
            self.addCleanup(shutil.rmtree, capture, ignore_errors=True)
            capture.mkdir(parents=True)
            campaign.write_json_atomic(
                capture / "metadata.json",
                {
                    "codex_version": self.codex_version,
                    "fixture": "settings-override-correctness",
                    "fixture_source": str(
                        (ROOT / "fixtures" / "settings-override-correctness").resolve()
                    ),
                    "fixture_tree_manifest": campaign.tree_manifest(
                        ROOT / "fixtures" / "settings-override-correctness"
                    ),
                    "model": "gpt-5.6-terra",
                    "reasoning_effort": "low",
                    "repeat": 1,
                    "run_id": run_id,
                    "subject": "promotion-parent",
                    "subject_source": str((target / frozen["parent"]["path"]).resolve()),
                    "subject_tree_manifest": campaign.tree_manifest(
                        target / frozen["parent"]["path"]
                    ),
                    "agent_started_monotonic_ns": 1,
                    "agent_started_timestamp_ns": 1,
                },
            )
            campaign.write_json_atomic(
                capture / "summary.json",
                {
                    "agent": {
                        "duration_seconds": 1.0,
                        "item_counts": {"command_execution": 1},
                        "turn_status": "completed",
                        "uncached_input_tokens": 10,
                        "usage": {},
                    },
                    "passed": True,
                    "run_id": run_id,
                },
            )
            campaign.write_json_atomic(
                capture / "grade.json",
                {
                    "agent_event_log_tampered": False,
                    "agent_event_log_valid": True,
                    "fixture_tree_unchanged": True,
                    "git_state_unchanged": True,
                    "protected_changed_paths": [],
                    "subject_tree_unchanged": True,
                    "unexpected_changed_paths": [],
                },
            )
            for name in set(campaign.CAPTURE_FILES) - {
                "grade.json",
                "metadata.json",
                "summary.json",
            }:
                (capture / name).write_text(f"{name}\n", encoding="utf-8")
            record = campaign.run_record(
                capture,
                frozen,
                target,
                phase="aa",
                pair=1,
                arm="parent",
                fixture="settings-override-correctness",
            )
            record["runner_returncode"] = 0
            slot = {"arm": "parent", "fixture": record["fixture"], "pair": 1, "phase": "aa"}
            campaign.append_event(
                target,
                {
                    "frozen_sha256": frozen["frozen_sha256"],
                    "kind": "started",
                    "run_id": run_id,
                    "slot": slot,
                },
            )
            campaign.append_event(
                target,
                {
                    "frozen_sha256": frozen["frozen_sha256"],
                    "kind": "run",
                    "record": record,
                },
            )
            self.assertEqual(campaign.verify_capture_hashes(target), [])
            with self.assertRaisesRegex(campaign.CampaignError, "run slot"):
                campaign.run_record(
                    capture,
                    frozen,
                    target,
                    phase="aa",
                    pair=2,
                    arm="parent",
                    fixture="settings-override-correctness",
                )
            replayed = campaign.ledger_events(target)
            replayed[-1]["frozen_sha256"] = "0" * 64
            with self.assertRaisesRegex(campaign.CampaignError, "frozen contract"):
                campaign.validate_resume_state(replayed, frozen["frozen_sha256"])

            events = campaign.ledger_events(target)
            events[-1]["record"]["capture_hashes"]["../../not-evidence"] = "0" * 64
            self.rewrite_events(target, events)
            self.assertTrue(
                any(
                    "capture evidence incomplete" in error
                    for error in campaign.verify_capture_hashes(target)
                )
            )
            (capture / "codex-observed.jsonl").write_text("tampered\n", encoding="utf-8")
            self.assertTrue(campaign.verify_capture_hashes(target))

    def test_missing_metric_and_single_success_gain_do_not_select_candidate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="promotion-metric-") as raw:
            target = self.freeze(Path(raw))
            self.append_phase(target, "aa", 5, 10.0, 10.0)
            self.append_phase(target, "development", 5, 10.0, 9.9)
            events = campaign.ledger_events(target)
            first = next(
                event
                for event in events
                if event.get("kind") == "run"
                and event["record"]["phase"] == "development"
            )
            first["record"]["duration_seconds"] = None
            self.rewrite_events(target, events)
            summary = campaign.phase_summary(target, "development")
            self.assertFalse(summary["paired_metrics_complete"])
            self.assertEqual(summary["decision"], "insufficient-evidence")

    def test_verify_errors_override_positive_decision(self) -> None:
        with tempfile.TemporaryDirectory(prefix="promotion-verify-") as raw:
            target = self.freeze(Path(raw))
            self.append_phase(target, "aa", 5, 10.0, 10.0)
            self.append_phase(target, "development", 5, 10.0, 7.0)
            self.append_phase(target, "confirmation", 5, 10.0, 7.0)
            self.seal_lineage(target)
            self.append_phase(
                target, "audit", 1, 10.0, 7.0, fixture="sealed-audit"
            )
            self.assertEqual(campaign.campaign_decision(target)["decision"], "candidate")
            result = campaign.verify_campaign(target)
            self.assertTrue(result["errors"])
            self.assertEqual(result["decision"], "insufficient-evidence")
            self.assertFalse(result["promotion_eligible"])
            self.assertFalse(result["verification_integrity_passed"])
            saved = dict(result)
            digest = saved.pop("receipt_sha256")
            self.assertEqual(digest, campaign.canonical_sha256(saved))

    def test_frozen_paths_remain_inside_campaign(self) -> None:
        with tempfile.TemporaryDirectory(prefix="promotion-path-") as raw:
            target = self.freeze(Path(raw))
            frozen = campaign.read_json(target / "frozen.json")
            frozen["candidate"]["path"] = "../../outside"
            frozen["frozen_sha256"] = campaign.canonical_sha256(
                {key: value for key, value in frozen.items() if key != "frozen_sha256"}
            )
            campaign.write_json_atomic(target / "frozen.json", frozen)
            with self.assertRaisesRegex(campaign.CampaignError, "frozen layout"):
                campaign.validate_frozen(target)


if __name__ == "__main__":
    unittest.main()
