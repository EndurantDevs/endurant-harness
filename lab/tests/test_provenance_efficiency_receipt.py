from __future__ import annotations

import copy
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


LAB = Path(__file__).resolve().parents[1]
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))

import provenance_efficiency_receipt as receipt  # noqa: E402


ARTIFACT = (
    LAB.parent / "artifacts" / "benchmarks" / "provenance-efficiency-ab.json"
)


class ProvenanceEfficiencyReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = receipt.read_json(ARTIFACT)

    @staticmethod
    def rehash(payload):
        payload["receipt_sha256"] = receipt._sha256_bytes(  # noqa: SLF001
            receipt._canonical_bytes(payload["body"])  # noqa: SLF001
        )
        return payload

    def assert_rejected(self, payload) -> None:
        checks = receipt.validate_receipt(payload)
        self.assertFalse(all(checks.values()), checks)

    def test_current_receipt_passes_every_gate(self) -> None:
        checks = receipt.validate_receipt(self.payload)
        self.assertTrue(all(checks.values()), checks)

    def test_stale_source_or_runner_identity_is_rejected(self) -> None:
        stale = copy.deepcopy(self.payload)
        stale["body"]["source"]["input_sha256"][
            "lab/prompts/provenance-efficiency.txt"
        ] = "0" * 64
        self.assert_rejected(self.rehash(stale))

        runner = copy.deepcopy(self.payload)
        runner["body"]["source"]["executed_runner_sha256"] = "1" * 64
        self.assert_rejected(self.rehash(runner))

    def test_fixture_symlink_is_rejected_from_source_binding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="provenance-fixture-symlink-") as raw:
            fixture = Path(raw)
            template = fixture / "template"
            template.mkdir()
            target = template / "target.txt"
            target.write_text("synthetic\n", encoding="utf-8")
            (template / "linked.txt").symlink_to(target)
            with patch.object(receipt, "FIXTURE_ROOT", fixture):
                with self.assertRaises(receipt.ReceiptError):
                    receipt.source_input_paths()

    def test_run_metric_and_derived_summary_mutations_are_rejected(self) -> None:
        run = copy.deepcopy(self.payload)
        run["body"]["runs"][0]["wall_seconds"] = 1.0
        self.assert_rejected(self.rehash(run))

        aggregate = copy.deepcopy(self.payload)
        aggregate["body"]["metrics"]["wall_seconds"]["new_median"] = 1.0
        self.assert_rejected(self.rehash(aggregate))

        pair = copy.deepcopy(self.payload)
        pair["body"]["pairwise"][0]["wall_change_fraction"] = 0.0
        self.assert_rejected(self.rehash(pair))

        coherent = copy.deepcopy(self.payload)
        coherent["body"]["runs"][0]["wall_seconds"] = 50.0
        recomputed = receipt.aggregate_runs(coherent["body"]["runs"])
        for key, value in recomputed.items():
            coherent["body"][key] = value
        self.assert_rejected(self.rehash(coherent))

    def test_quality_and_provenance_mutations_are_rejected(self) -> None:
        provenance = copy.deepcopy(self.payload)
        provenance["body"]["runs"][1]["provenance_current"] = False
        provenance["body"]["runs"][1]["accepted"] = False
        self.assert_rejected(self.rehash(provenance))

        quality = copy.deepcopy(self.payload)
        quality["body"]["quality"]["old"]["accepted"] = 2
        self.assert_rejected(self.rehash(quality))

    def test_scope_external_and_synthetic_false_greens_are_rejected(self) -> None:
        scope = copy.deepcopy(self.payload)
        scope["body"]["runs"][0]["changed_paths"].append("scripts/verify.py")
        scope["body"]["runs"][0]["scope_exact"] = False
        scope["body"]["runs"][0]["functional_passed"] = False
        scope["body"]["runs"][0]["accepted"] = False
        self.assert_rejected(self.rehash(scope))

        external = copy.deepcopy(self.payload)
        external["body"]["runs"][0]["external"]["hidden"]["returncode"] = 1
        external["body"]["runs"][0]["functional_passed"] = False
        external["body"]["runs"][0]["accepted"] = False
        self.assert_rejected(self.rehash(external))

        synthetic = copy.deepcopy(self.payload)
        synthetic["body"]["runs"][0]["synthetic_command_count"] = 1
        synthetic["body"]["runs"][0]["functional_passed"] = False
        synthetic["body"]["runs"][0]["accepted"] = False
        self.assert_rejected(self.rehash(synthetic))

    def test_non_finite_and_negative_metrics_fail_closed(self) -> None:
        non_finite = copy.deepcopy(self.payload)
        non_finite["body"]["runs"][0]["wall_seconds"] = math.nan
        checks = receipt.validate_receipt(non_finite)
        self.assertFalse(all(checks.values()), checks)

        negative = copy.deepcopy(self.payload)
        negative["body"]["runs"][0]["uncached_input_tokens"] = -1
        self.assert_rejected(self.rehash(negative))

    def test_raw_receipt_path_and_hash_tampering_is_rejected(self) -> None:
        path = copy.deepcopy(self.payload)
        path["body"]["raw_receipts"][0]["files"][0]["path"] = "../escape"
        self.assert_rejected(self.rehash(path))

        digest = copy.deepcopy(self.payload)
        digest["body"]["raw_receipts"][0]["files"][0]["sha256"] = "f" * 64
        self.assert_rejected(self.rehash(digest))

    def test_general_speed_claim_remains_closed(self) -> None:
        claim = copy.deepcopy(self.payload)
        claim["body"]["decision"]["speed_claim_ready"] = True
        self.assert_rejected(self.rehash(claim))

        extra = copy.deepcopy(self.payload)
        extra["body"]["decision"]["universal_speedup"] = True
        self.assert_rejected(self.rehash(extra))

    def test_cross_field_and_usage_false_greens_are_rejected(self) -> None:
        for field, value in (
            ("first_production_edit_seconds", 999.0),
            ("pre_production_command_count", 999),
            ("provenance_attempts", 999),
        ):
            mutant = copy.deepcopy(self.payload)
            mutant["body"]["runs"][0][field] = value
            self.assert_rejected(self.rehash(mutant))

        usage = copy.deepcopy(self.payload)
        usage["body"]["runs"][0]["usage"]["reported_savings"] = 1
        self.assert_rejected(self.rehash(usage))


if __name__ == "__main__":
    unittest.main()
