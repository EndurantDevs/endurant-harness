from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("run_lane_ab.py")
SPEC = importlib.util.spec_from_file_location("run_lane_ab", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class LaneABTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = MODULE.load_cases(MODULE.DEFAULT_CASES)

    def predictions(self):
        return [
            {"id": case["id"], "lane": case["expected_lane"]}
            for case in self.cases
        ]

    def test_exact_predictions_score_perfectly(self) -> None:
        score = MODULE.score_predictions(self.cases, self.predictions())
        self.assertTrue(score["valid"])
        self.assertEqual(score["accuracy"], 1.0)
        self.assertEqual(score["direct_recall"], 1.0)
        self.assertEqual(score["hazardous_recall"], 1.0)

    def test_duplicate_invalidates_entire_batch(self) -> None:
        predictions = self.predictions()
        predictions[-1] = copy.deepcopy(predictions[0])
        score = MODULE.score_predictions(self.cases, predictions)
        self.assertFalse(score["valid"])
        self.assertEqual(score["accuracy"], 0.0)
        self.assertIn(predictions[0]["id"], score["duplicate_ids"])

    def test_missing_prediction_invalidates_entire_batch(self) -> None:
        score = MODULE.score_predictions(self.cases, self.predictions()[:-1])
        self.assertFalse(score["valid"])
        self.assertEqual(score["accuracy"], 0.0)
        self.assertEqual(len(score["missing_ids"]), 1)

    def test_one_wrong_direct_prediction_changes_only_direct_recall(self) -> None:
        predictions = self.predictions()
        predictions[0]["lane"] = "escalated"
        score = MODULE.score_predictions(self.cases, predictions)
        self.assertTrue(score["valid"])
        self.assertEqual(score["accuracy"], 0.975)
        self.assertEqual(score["direct_recall"], 0.95)
        self.assertEqual(score["hazardous_recall"], 1.0)

    def test_unhashable_lane_is_rejected_without_crashing(self) -> None:
        predictions = self.predictions()
        predictions[0]["lane"] = ["direct"]
        score = MODULE.score_predictions(self.cases, predictions)
        self.assertFalse(score["valid"])
        self.assertEqual(score["accuracy"], 0.0)
        self.assertEqual(score["invalid_predictions"], 1)

    def test_parser_rejects_unhashable_lane_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lane-parser-") as value:
            path = Path(value) / "final.json"
            path.write_text(
                json.dumps(
                    {
                        "classifications": [
                            {"id": "d01", "lane": ["direct"]}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            predictions, error = MODULE.parse_predictions(path)
        self.assertEqual(predictions, [])
        self.assertEqual(error, "classification has an invalid id or lane")

    def test_candidate_replacement_is_exact_and_adds_allowlist(self) -> None:
        current = MODULE.DEFAULT_SKILL.read_text(encoding="utf-8")
        candidate = MODULE.candidate_policy(current)
        self.assertNotEqual(current, candidate)
        self.assertIn("only when every condition is established", candidate)
        self.assertNotIn(MODULE.CURRENT_DIRECT, candidate)


if __name__ == "__main__":
    unittest.main()
