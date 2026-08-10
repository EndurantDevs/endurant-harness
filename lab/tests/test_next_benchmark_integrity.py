from __future__ import annotations

import copy
import math
import sys
import unittest
from pathlib import Path
from typing import Any, Callable


LAB = Path(__file__).resolve().parents[1]
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))

from check_results import next_improvements_checks  # noqa: E402
from eval_lib import ARTIFACTS, read_json  # noqa: E402


CORE_INPUT_PATHS = {
    "fixtures/settings-override-correctness/template/scripts/verify.py",
    "fixtures/settings-override-correctness/template/src/settings.py",
    "fixtures/settings-override-correctness/template/src/settings_cli.py",
    "fixtures/settings-override-correctness/template/tests/test_settings.py",
    "lab/benchmark_next_improvements.py",
    "lab/eval_lib.py",
    "lab/evals/lane-cases.json",
    "lab/proposals/benchmark_receipt.py",
    "lab/proposals/fast_preflight.py",
    "lab/proposals/lane_classifier.py",
    "lab/proposals/probe_relevance.py",
    "lab/proposals/version_provenance.py",
    "subjects/combined-candidate/endurant-harness/scripts/endurant.py",
}


class NextBenchmarkIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.receipt = read_json(ARTIFACTS / "benchmarks" / "next-improvements.json")

    def checks(self, receipt: dict[str, Any]) -> dict[str, bool]:
        checks = next_improvements_checks(receipt)
        self.assertIsInstance(checks, dict)
        self.assertTrue(checks, "next-improvements checker returned no gates")
        self.assertTrue(all(isinstance(value, bool) for value in checks.values()), checks)
        return checks

    def assert_rejected(self, receipt: dict[str, Any]) -> None:
        checks = self.checks(receipt)
        self.assertFalse(all(checks.values()), checks)

    def test_current_receipt_is_bound_and_passes_every_gate(self) -> None:
        source = self.receipt.get("source")
        self.assertIsInstance(source, dict)
        manifest = source.get("input_sha256")
        self.assertIsInstance(manifest, dict)
        self.assertTrue(CORE_INPUT_PATHS.issubset(manifest), sorted(manifest))
        self.assertTrue(
            all(
                isinstance(digest, str)
                and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
                for digest in manifest.values()
            ),
            manifest,
        )
        checks = self.checks(self.receipt)
        self.assertTrue(all(checks.values()), checks)

    def test_stale_benchmark_source_or_missing_input_is_rejected(self) -> None:
        stale = copy.deepcopy(self.receipt)
        stale["source"]["input_sha256"]["lab/benchmark_next_improvements.py"] = (
            "0" * 64
        )
        self.assert_rejected(stale)

        missing = copy.deepcopy(self.receipt)
        missing["source"]["input_sha256"].pop("lab/evals/lane-cases.json")
        self.assert_rejected(missing)

    def test_tampered_derived_summaries_are_rejected(self) -> None:
        mutations: dict[str, Callable[[dict[str, Any]], None]] = {
            "probe-top3": lambda value: value["probe_relevance"][
                "source_and_test_top3"
            ].__setitem__("candidate", 0),
            "probe-path-count": lambda value: value["probe_relevance"][
                "path_count"
            ].__setitem__("candidate", 25),
            "preflight-reduction": lambda value: value["fast_preflight"].__setitem__(
                "proof_slice_reduction_fraction", 0.999999
            ),
            "benchmark-improvement": lambda value: value["benchmark_receipt"][
                "comparison"
            ].__setitem__("improvement_fraction", 0.5),
            "benchmark-threshold": lambda value: value["benchmark_receipt"][
                "comparison"
            ].__setitem__("threshold_fraction", 0.0),
            "redundant-round-fraction": lambda value: value["benchmark_receipt"].__setitem__(
                "observed_redundant_round_fraction", 0.5
            ),
            "lane-accuracy": lambda value: value["lane_classifier"][
                "reference_scorer"
            ].__setitem__("accuracy", 0.5),
            "provenance-max-bytes": lambda value: value["version_provenance"].__setitem__(
                "max_compact_bytes", 79
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                forged = copy.deepcopy(self.receipt)
                mutate(forged)
                self.assert_rejected(forged)

    def test_tampered_gate_or_mutant_claim_is_rejected(self) -> None:
        gate = copy.deepcopy(self.receipt)
        gate["fast_preflight"]["gates"]["all_seeded_failures_caught"] = False
        self.assert_rejected(gate)

        mutant = copy.deepcopy(self.receipt)
        mutant["benchmark_receipt"]["mutants"][0]["rejected"] = False
        self.assert_rejected(mutant)

        top_level = copy.deepcopy(self.receipt)
        top_level["passed"] = False
        self.assert_rejected(top_level)

    def test_non_finite_or_impossible_timing_summary_is_rejected(self) -> None:
        non_finite = copy.deepcopy(self.receipt)
        non_finite["benchmark_receipt"]["enabled_p50_ms"] = math.nan
        self.assert_rejected(non_finite)

        impossible = copy.deepcopy(self.receipt)
        impossible["benchmark_receipt"]["enabled_p95_ms"] = -1.0
        self.assert_rejected(impossible)

    def test_provenance_requires_the_exact_six_case_matrix(self) -> None:
        duplicated = copy.deepcopy(self.receipt)
        for row in duplicated["version_provenance"]["cases"]:
            row.update(
                {
                    "case": "current",
                    "expected": "current",
                    "observed": "current",
                    "correct": True,
                }
            )
        duplicated["version_provenance"]["gates"].update(
            {
                "all_states_correct": True,
                "missing_never_current": True,
            }
        )
        self.assert_rejected(duplicated)


if __name__ == "__main__":
    unittest.main()
