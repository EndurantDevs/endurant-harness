from __future__ import annotations

import copy
import unittest

from pathlib import Path
import sys


LAB = Path(__file__).resolve().parents[1]
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))

from check_results import rust_checks  # noqa: E402
from eval_lib import ARTIFACTS, read_json  # noqa: E402


class RustReceiptIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.receipt = read_json(ARTIFACTS / "benchmarks" / "rust-runtime.json")
        cls.model = read_json(ARTIFACTS / "benchmarks" / "model-runs.json")

    def test_current_receipt_passes_every_rust_gate(self) -> None:
        checks = rust_checks(self.receipt, self.model)
        self.assertTrue(all(checks.values()), checks)

    def test_missing_timing_evidence_is_rejected(self) -> None:
        receipt = copy.deepcopy(self.receipt)
        receipt.pop("template")
        checks = rust_checks(receipt, self.model)
        self.assertFalse(checks["rust_timing_receipts_recompute"])
        self.assertFalse(checks["rust_rewrite_rejected_as_immaterial"])

    def test_stale_input_or_handwritten_cli_claim_is_rejected(self) -> None:
        receipt = copy.deepcopy(self.receipt)
        receipt["source"]["input_sha256"].pop("lab/python_scan_kernel.py")
        receipt["parity"]["full_cli"]["run"] = {
            "implemented": False,
            "observed_exit_code": 2,
        }
        checks = rust_checks(receipt, self.model)
        self.assertFalse(checks["rust_retest_inputs_are_receipt_commit_bound"])
        self.assertFalse(checks["rust_full_cli_limit_is_explicit"])

    def test_missing_or_forged_generated_inputs_are_rejected(self) -> None:
        missing = copy.deepcopy(self.receipt)
        missing.pop("generated_inputs")
        self.assertFalse(
            rust_checks(missing, self.model)["rust_generated_inputs_are_bound"]
        )

        forged = copy.deepcopy(self.receipt)
        forged["generated_inputs"]["fixture_manifest_sha256"]["heavy"] = "0" * 64
        forged["generated_inputs"]["one_command_plan_sha256"] = "0" * 64
        self.assertFalse(
            rust_checks(forged, self.model)["rust_generated_inputs_are_bound"]
        )

    def test_forged_scan_hash_or_case_dimensions_are_rejected(self) -> None:
        forged = copy.deepcopy(self.receipt)
        heavy = forged["parity"]["limited_non_git_scan_matrix"][0]
        self.assertEqual(heavy["case"], "heavy")
        heavy["python_sha256"] = "0" * 64
        heavy["max_depth"] = 8
        self.assertFalse(rust_checks(forged, self.model)["rust_limited_parity_exact"])


if __name__ == "__main__":
    unittest.main()
