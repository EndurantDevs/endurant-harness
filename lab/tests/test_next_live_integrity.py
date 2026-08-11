from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any, Callable


LAB = Path(__file__).resolve().parents[1]
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))

from check_results import (  # noqa: E402
    canonical_sha256,
    named_live_evidence,
    next_live_checks,
)
from eval_lib import ARTIFACTS, read_json  # noqa: E402


class NextLiveIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.live = read_json(ARTIFACTS / "benchmarks" / "next-live.json")
        cls.deterministic = read_json(
            ARTIFACTS / "benchmarks" / "next-improvements.json"
        )

    def checks(self, live: dict[str, Any]) -> dict[str, bool]:
        result = next_live_checks(live, self.deterministic)
        self.assertTrue(result)
        self.assertTrue(all(isinstance(value, bool) for value in result.values()))
        return result

    def assert_rejected(self, live: dict[str, Any]) -> None:
        self.assertFalse(all(self.checks(live).values()))

    def test_current_live_receipt_passes_every_integrity_gate(self) -> None:
        checks = self.checks(self.live)
        self.assertTrue(all(checks.values()), checks)

    def test_stale_source_or_deterministic_reference_is_rejected(self) -> None:
        stale = copy.deepcopy(self.live)
        stale["source"]["input_sha256"]["lab/summarize_next_live.py"] = "0" * 64
        self.assert_rejected(stale)

        stale_reference = copy.deepcopy(self.live)
        stale_reference["probe_and_preflight_reference"]["sha256"] = "0" * 64
        self.assert_rejected(stale_reference)

    def test_discovery_and_boundary_mutations_are_rejected(self) -> None:
        comparison = copy.deepcopy(self.live)
        comparison["discovery_stop_budget"]["comparison"][
            "duration_seconds_reduction_fraction"
        ] = 0.9
        self.assert_rejected(comparison)

        unsafe_boundary = copy.deepcopy(self.live)
        unsafe_boundary["discovery_stop_budget"]["boundary_canaries"][0][
            "file_change_items"
        ] = 1
        self.assert_rejected(unsafe_boundary)

        failed_control = copy.deepcopy(self.live)
        failed_control["discovery_stop_budget"]["current"]["runs"][0].update(
            {
                "accepted": False,
                "functional_passed": False,
                "local_ci_passed": False,
            }
        )
        self.assert_rejected(failed_control)

    def test_lane_quality_or_timing_mutation_is_rejected(self) -> None:
        quality = copy.deepcopy(self.live)
        quality["lane_classification"]["arms"]["allowlist"]["accuracy"] = 0.95
        self.assert_rejected(quality)

        timing = copy.deepcopy(self.live)
        timing["lane_classification"]["comparison"][
            "median_duration_change_fraction"
        ] = -0.1
        self.assert_rejected(timing)

    def test_red_order_and_nonbug_boundary_mutations_are_rejected(self) -> None:
        red = copy.deepcopy(self.live)
        red["red_before_green"]["candidate"]["runs"][0][
            "failing_regression_before_production_edit"
        ] = False
        self.assert_rejected(red)

        overactive = copy.deepcopy(self.live)
        overactive["red_before_green"]["nonbug_canaries"][0][
            "pre_edit_failing_test_count"
        ] = 1
        self.assert_rejected(overactive)

        failed_control = copy.deepcopy(self.live)
        failed_control["red_before_green"]["current"]["runs"][0].update(
            {
                "accepted": False,
                "functional_passed": False,
                "local_ci_passed": False,
            }
        )
        self.assert_rejected(failed_control)

    def test_benchmark_duplicate_and_comparison_mutations_are_rejected(self) -> None:
        duplicate = copy.deepcopy(self.live)
        duplicate["benchmark_comparator"]["runs"][
            "repository_contract_current_skill"
        ]["synthetic_agent_gate_count"] = 3
        self.assert_rejected(duplicate)

        forged = copy.deepcopy(self.live)
        forged["benchmark_comparator"]["comparison"][
            "repository_contract_vs_historical_wall_reduction_fraction"
        ] = 0.99
        self.assert_rejected(forged)

    def test_provenance_and_top_level_claims_fail_closed(self) -> None:
        overclaim = copy.deepcopy(self.live)
        overclaim["version_provenance"]["active_session_reload_live_tested"] = True
        self.assert_rejected(overclaim)

        top = copy.deepcopy(self.live)
        top["passed"] = False
        self.assert_rejected(top)

    def test_environment_and_skill_evidence_is_bound(self) -> None:
        environment = copy.deepcopy(self.live)
        environment["environment"]["model"] = "unrecorded-model"
        self.assert_rejected(environment)

        skill = copy.deepcopy(self.live)
        skill["discovery_stop_budget"]["candidate"]["runs"][0][
            "subject_skill_sha256"
        ] = "f" * 64
        self.assert_rejected(skill)

    def test_raw_receipt_evidence_is_bound_when_available(self) -> None:
        raw = copy.deepcopy(self.live)
        run = raw["benchmark_comparator"]["runs"][
            "repository_contract_current_skill"
        ]
        raw_summary = ARTIFACTS / "runs" / run["run_id"] / "summary.json"
        if not raw_summary.is_file():
            self.skipTest("ignored raw captures are not present in this checkout")
        run["raw_receipts"]["summary"]["sha256"] = "f" * 64
        raw["evidence_manifest_sha256"] = canonical_sha256(
            named_live_evidence(raw)
        )
        self.assert_rejected(raw)

    def test_coherent_metric_forgery_is_rejected_by_local_resummary(self) -> None:
        first_run = self.live["discovery_stop_budget"]["candidate"]["runs"][0]
        raw_summary = ARTIFACTS / "runs" / first_run["run_id"] / "summary.json"
        if not raw_summary.is_file():
            self.skipTest("ignored raw captures are not present in this checkout")

        discovery = copy.deepcopy(self.live)
        section = discovery["discovery_stop_budget"]
        for run in section["candidate"]["runs"]:
            run["duration_seconds"] = 1.0
        section["candidate"]["median"]["duration_seconds"] = 1.0
        current = section["current"]["median"]["duration_seconds"]
        section["comparison"]["duration_seconds_reduction_fraction"] = round(
            (current - 1.0) / current, 6
        )
        self.assert_rejected(discovery)

        benchmark = copy.deepcopy(self.live)
        section = benchmark["benchmark_comparator"]
        runs = section["runs"]
        contract = runs["repository_contract_current_skill"]
        historical = runs["historical_without_receipt"]
        wording = runs["extra_core_wording"]
        contract["duration_seconds"] = 1.0
        section["comparison"][
            "repository_contract_vs_historical_wall_reduction_fraction"
        ] = round(
            (historical["duration_seconds"] - 1.0)
            / historical["duration_seconds"],
            6,
        )
        section["comparison"]["extra_wording_vs_contract_wall_change_fraction"] = round(
            wording["duration_seconds"] - 1.0,
            6,
        )
        self.assert_rejected(benchmark)


if __name__ == "__main__":
    unittest.main()
