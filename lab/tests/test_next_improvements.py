from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from lab.proposals import (
    benchmark_receipt,
    fast_preflight,
    lane_classifier,
    probe_relevance,
    version_provenance,
)


class ProbeRelevanceTests(unittest.TestCase):
    @staticmethod
    def runtime(
        output: str = "", *, code: int = 0, capture_truncated: bool = False
    ) -> SimpleNamespace:
        calls: list[tuple[list[str], Path, int]] = []

        def run_capture(
            argv: list[str], root: Path, timeout: int
        ) -> tuple[int, str, bool]:
            calls.append((argv, root, timeout))
            return code, output, capture_truncated

        return SimpleNamespace(
            MAX_FILE_BYTES=2_000_000,
            IGNORED_DIRS={".git", "target", "node_modules"},
            _run_capture=run_capture,
            capture_calls=calls,
        )

    def test_exact_symbols_preserve_snake_and_camel_case_in_order(self) -> None:
        task = (
            "Fix select_records, then selectRecords, repeat select_records, "
            "and leave select-records prose alone"
        )
        self.assertEqual(
            probe_relevance.exact_symbols(task),
            ["select_records", "selectRecords"],
        )

    def test_no_exact_symbol_returns_fallback_byte_for_byte(self) -> None:
        expected = (["./docs/guide.md"], ["broad warning"])
        calls: list[tuple[Path, str, int]] = []

        def fallback(root: Path, task: str, max_items: int):
            calls.append((root, task, max_items))
            return expected

        root = Path("/synthetic/repository")
        actual = probe_relevance.candidate_paths(
            self.runtime(),
            root,
            "Fix the parser behavior",
            7,
            fallback=fallback,
        )

        self.assertIs(actual, expected)
        self.assertEqual(calls, [(root, "Fix the parser behavior", 7)])

    def test_missing_rg_and_failed_exact_search_use_fallback(self) -> None:
        expected = (["./src/fallback.py"], ["fallback"])
        fallback = mock.Mock(return_value=expected)
        root = Path("/synthetic/repository")

        with mock.patch.object(probe_relevance.shutil, "which", return_value=None):
            self.assertIs(
                probe_relevance.candidate_paths(
                    self.runtime(),
                    root,
                    "Fix select_records",
                    5,
                    fallback=fallback,
                ),
                expected,
            )
        with mock.patch.object(probe_relevance.shutil, "which", return_value="/bin/rg"):
            self.assertIs(
                probe_relevance.candidate_paths(
                    self.runtime(code=2),
                    root,
                    "Fix select_records",
                    5,
                    fallback=fallback,
                ),
                expected,
            )

        self.assertEqual(fallback.call_count, 2)

    def test_ranking_is_deterministic_deduplicated_and_bounded(self) -> None:
        paths = [
            "./docs/select_records.md",
            "./src/z_record_selection.py",
            "./subjects/candidate/endurant-harness/SKILL.md",
            "./tests/test_record_selection.py",
            "./src/a_select_records.py",
            "./src/a_select_records.py",
        ]
        expected = [
            "./src/a_select_records.py",
            "./src/z_record_selection.py",
            "./tests/test_record_selection.py",
        ]
        fallback = mock.Mock(side_effect=AssertionError("fallback must not run"))
        observed: list[list[str]] = []

        with mock.patch.object(probe_relevance.shutil, "which", return_value="/bin/rg"):
            for candidate_order in (paths, list(reversed(paths)), paths[2:] + paths[:2]):
                runtime = self.runtime("\n".join(candidate_order), capture_truncated=True)
                ranked, warnings = probe_relevance.candidate_paths(
                    runtime,
                    Path("/synthetic/repository"),
                    "Fix select_records",
                    3,
                    fallback=fallback,
                )
                observed.append(ranked)
                self.assertEqual(
                    warnings,
                    ["candidate search output truncated", "candidate path list truncated"],
                )
                argv, root, timeout = runtime.capture_calls[0]
                self.assertEqual(root, Path("/synthetic/repository"))
                self.assertEqual(timeout, 2)
                self.assertIn("-F", argv)
                self.assertEqual(argv[argv.index("-e") + 1], "select_records")

        self.assertEqual(observed, [expected, expected, expected])

    def test_unrelated_harness_hits_fall_back_but_harness_tasks_keep_them(self) -> None:
        noise = "./subjects/candidate/endurant-harness/SKILL.md"
        expected = (["./src/fallback.py"], [])
        fallback = mock.Mock(return_value=expected)

        with mock.patch.object(probe_relevance.shutil, "which", return_value="/bin/rg"):
            unrelated = probe_relevance.candidate_paths(
                self.runtime(noise),
                Path("/synthetic/repository"),
                "Fix select_records",
                5,
                fallback=fallback,
            )
            harness_paths, _ = probe_relevance.candidate_paths(
                self.runtime("\n".join([noise, "./src/select_records.py"])),
                Path("/synthetic/repository"),
                "Improve the endurant harness around select_records",
                5,
                fallback=fallback,
            )

        self.assertIs(unrelated, expected)
        self.assertIn(noise, harness_paths)
        self.assertIn("./src/select_records.py", harness_paths)
        self.assertEqual(fallback.call_count, 1)

    def test_single_exact_hit_adds_bounded_non_harness_broad_context(self) -> None:
        fallback = mock.Mock(
            return_value=(
                [
                    "./src/select_records.py",
                    "./subjects/candidate/repo-harness/README.md",
                    "./tests/test_record_selection.py",
                    "./src/record_helpers.py",
                    "./docs/extra.md",
                ],
                ["broad warning"],
            )
        )
        with mock.patch.object(probe_relevance.shutil, "which", return_value="/bin/rg"):
            paths, warnings = probe_relevance.candidate_paths(
                self.runtime("./src/select_records.py"),
                Path("/synthetic/repository"),
                "Fix select_records",
                10,
                fallback=fallback,
            )

        self.assertEqual(
            paths,
            [
                "./src/select_records.py",
                "./tests/test_record_selection.py",
                "./src/record_helpers.py",
            ],
        )
        self.assertEqual(warnings, ["broad warning"])


class FastPreflightTests(unittest.TestCase):
    @staticmethod
    def profile() -> dict[str, object]:
        return {
            "schema_version": 1,
            "checks": {
                "focused": {"argv": ["python3", "verify.py", "focused"]},
                "lint": {"argv": ["python3", "verify.py", "lint"]},
                "synthetic": {"argv": ["python3", "verify.py", "synthetic"]},
            },
            "bundles": {
                "local-ci": {
                    "argv": ["python3", "verify.py", "local-ci"],
                    "covers": ["focused", "lint"],
                    "receipt": {"required_check_ids": ["focused", "lint"]},
                }
            },
        }

    def valid_receipt(
        self, profile: dict[str, object], fingerprint: str = "final-fingerprint"
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "profile_sha256": fast_preflight.canonical_sha256(profile),
            "verification_sha256": fingerprint,
            "checks": [
                {"id": "focused", "passed": True},
                {"id": "lint", "passed": True},
            ],
        }

    def test_resolve_runs_covered_bundle_once_and_only_uncovered_checks(self) -> None:
        original = [{"id": "legacy"}]
        selected = fast_preflight.resolve(
            self.profile(),
            required_checks=["focused", "synthetic"],
            bundle_id="local-ci",
            original_commands=original,
        )

        self.assertEqual([command["id"] for command in selected], ["bundle:local-ci", "synthetic"])
        self.assertNotIn("focused", [command["id"] for command in selected])

    def test_absent_profile_preserves_original_command_object(self) -> None:
        original = [{"id": "focused"}, {"id": "local-ci"}]
        selected = fast_preflight.resolve(
            None,
            required_checks=["focused"],
            bundle_id="local-ci",
            original_commands=original,
        )
        self.assertIs(selected, original)

    def test_malformed_profiles_commands_and_coverage_are_rejected(self) -> None:
        cases: dict[str, dict[str, object]] = {}

        bad_schema = self.profile()
        bad_schema["schema_version"] = 2
        cases["schema"] = bad_schema

        bad_checks = self.profile()
        bad_checks["checks"] = []
        cases["checks-not-object"] = bad_checks

        bad_bundles = self.profile()
        bad_bundles["bundles"] = []
        cases["bundles-not-object"] = bad_bundles

        missing_bundle = self.profile()
        missing_bundle["bundles"] = {}
        cases["missing-bundle"] = missing_bundle

        for name, value in {
            "bundle-not-object": "invalid",
            "missing-argv": {"covers": ["focused"]},
            "empty-argv": {"argv": [], "covers": ["focused"]},
            "non-string-argv": {"argv": ["python3", 3], "covers": ["focused"]},
            "nested-cwd": {"argv": ["python3"], "cwd": "src", "covers": ["focused"]},
            "shell": {"argv": ["python3"], "shell": False, "covers": ["focused"]},
            "covers-not-array": {"argv": ["python3"], "covers": "focused"},
            "duplicate-cover": {"argv": ["python3"], "covers": ["focused", "focused"]},
            "unknown-cover": {"argv": ["python3"], "covers": ["unknown"]},
            "non-string-cover": {"argv": ["python3"], "covers": [1]},
        }.items():
            profile = self.profile()
            profile["bundles"]["local-ci"] = value  # type: ignore[index]
            cases[name] = profile

        for name, profile in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                fast_preflight.resolve(
                    profile,
                    required_checks=["focused"],
                    bundle_id="local-ci",
                    original_commands=[],
                )

        with self.assertRaisesRegex(ValueError, "unknown required check"):
            fast_preflight.resolve(
                self.profile(),
                required_checks=["unknown"],
                bundle_id="local-ci",
                original_commands=[],
            )

    def test_valid_receipt_and_every_stale_or_untrusted_variant(self) -> None:
        profile = self.profile()
        receipt = self.valid_receipt(profile)
        self.assertTrue(
            fast_preflight.verify_receipt(
                profile, "local-ci", receipt, "final-fingerprint"
            )
        )

        variants: dict[str, dict[str, object]] = {}
        for name, mutate in {
            "schema": lambda value: value.update(schema_version=2),
            "profile-hash": lambda value: value.update(profile_sha256="0" * 64),
            "stale-fingerprint": lambda value: value.update(verification_sha256="stale"),
            "missing-checks": lambda value: value.pop("checks"),
            "checks-not-array": lambda value: value.update(checks={}),
            "unknown-check": lambda value: value["checks"][1].update(id="unknown"),
            "duplicate-check": lambda value: value["checks"][1].update(id="focused"),
            "reordered-checks": lambda value: value.update(checks=list(reversed(value["checks"]))),
            "failed-check": lambda value: value["checks"][0].update(passed=False),
            "truthy-not-true": lambda value: value["checks"][0].update(passed=1),
            "missing-id": lambda value: value["checks"][0].pop("id"),
        }.items():
            candidate = copy.deepcopy(receipt)
            mutate(candidate)
            variants[name] = candidate

        for name, candidate in variants.items():
            with self.subTest(name=name):
                self.assertFalse(
                    fast_preflight.verify_receipt(
                        profile, "local-ci", candidate, "final-fingerprint"
                    )
                )

    def test_malformed_receipt_check_entries_fail_closed(self) -> None:
        profile = self.profile()
        for malformed in (None, "focused", 3, [], {"id": "focused"}):
            receipt = self.valid_receipt(profile)
            receipt["checks"] = [malformed, {"id": "lint", "passed": True}]
            with self.subTest(check=malformed):
                self.assertFalse(
                    fast_preflight.verify_receipt(
                        profile, "local-ci", receipt, "final-fingerprint"
                    )
                )


class BenchmarkReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="receipt-tests-")
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()
        (self.root / "scripts").mkdir()
        (self.root / "src" / "selection.py").write_text(
            "BASELINE = True\n", encoding="utf-8"
        )
        (self.root / "scripts" / "verify.py").write_text(
            "WORKLOAD = 'stable'\n", encoding="utf-8"
        )
        self.profile = {
            "benchmark_id": "selection-benchmark",
            "argv": ["python3", "scripts/verify.py", "synthetic"],
            "cwd": ".",
            "env": {"PYTHONDONTWRITEBYTECODE": "1"},
            "source_files": ["src/selection.py"],
            "workload_files": ["scripts/verify.py"],
            "correctness_keys": ["output_digest", "result_count"],
            "metric_schema": {
                "p95_seconds": {"unit": "seconds", "direction": "lower"},
                "samples_seconds": {"unit": "seconds", "direction": "lower"},
            },
            "primary_metric": "p95_seconds",
            "minimum_improvement_fraction": 0.4,
        }
        self.baseline_event = {
            "output_digest": "stable-output",
            "result_count": 4,
            "metrics": {"p95_seconds": 0.2, "samples_seconds": [0.18, 0.2]},
        }
        self.final_event = {
            "output_digest": "stable-output",
            "result_count": 4,
            "metrics": {"p95_seconds": 0.1, "samples_seconds": [0.09, 0.1]},
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def receipts(self):
        baseline = benchmark_receipt.build_receipt(
            self.profile, self.baseline_event, self.root, "baseline"
        )
        observed_baseline = baseline["body"]["source"]
        (self.root / "src" / "selection.py").write_text(
            "BASELINE = False\n", encoding="utf-8"
        )
        final = benchmark_receipt.build_receipt(
            self.profile, self.final_event, self.root, "final"
        )
        return baseline, final, observed_baseline, final["body"]["source"]

    @staticmethod
    def resign(receipt: dict[str, object]) -> None:
        receipt["receipt_sha256"] = benchmark_receipt.canonical_sha256(receipt["body"])

    def compare(self, baseline, final, observed_baseline, observed_final):
        return benchmark_receipt.compare(
            baseline,
            final,
            observed_baseline_source=observed_baseline,
            observed_final_source=observed_final,
        )

    def test_valid_receipts_bind_sources_and_pass_threshold(self) -> None:
        baseline, final, observed_baseline, observed_final = self.receipts()
        result = self.compare(baseline, final, observed_baseline, observed_final)
        self.assertTrue(result["passed"])
        self.assertEqual(result["primary_metric"], "p95_seconds")
        self.assertEqual(result["improvement_fraction"], 0.5)
        self.assertEqual(result["threshold_fraction"], 0.4)

    def test_build_rejects_missing_extra_or_incomplete_event_keys(self) -> None:
        missing_metric = copy.deepcopy(self.baseline_event)
        missing_metric["metrics"].pop("samples_seconds")
        extra_metric = copy.deepcopy(self.baseline_event)
        extra_metric["metrics"]["median_seconds"] = 0.19
        missing_correctness = copy.deepcopy(self.baseline_event)
        missing_correctness.pop("output_digest")

        with self.assertRaises(ValueError):
            benchmark_receipt.build_receipt(
                self.profile, missing_metric, self.root, "baseline"
            )
        with self.assertRaises(ValueError):
            benchmark_receipt.build_receipt(
                self.profile, extra_metric, self.root, "baseline"
            )
        with self.assertRaises(ValueError):
            benchmark_receipt.build_receipt(
                self.profile, missing_correctness, self.root, "baseline"
            )

    def test_recomputed_hash_cannot_hide_changed_workload_or_contract(self) -> None:
        baseline, final, observed_baseline, observed_final = self.receipts()

        def set_schema(receipt):
            receipt["body"]["schema_version"] = 2

        def set_benchmark(receipt):
            receipt["body"]["benchmark_id"] = "different-benchmark"

        def set_argv(receipt):
            receipt["body"]["workload"]["argv"].append("--changed")

        def set_env(receipt):
            receipt["body"]["workload"]["env"]["MODE"] = "changed"

        def set_workload_file(receipt):
            receipt["body"]["workload"]["files"]["scripts/verify.py"] = "0" * 64

        def set_correctness(receipt):
            receipt["body"]["correctness"]["output_digest"] = "changed"

        def set_metric_schema(receipt):
            receipt["body"]["metric_schema"]["p95_seconds"]["direction"] = "higher"

        def set_primary_metric(receipt):
            receipt["body"]["primary_metric"] = "samples_seconds"

        def set_threshold(receipt):
            receipt["body"]["minimum_improvement_fraction"] = 0.01

        mutants = {
            "schema-version": set_schema,
            "benchmark-id": set_benchmark,
            "argv": set_argv,
            "environment": set_env,
            "workload-file": set_workload_file,
            "correctness": set_correctness,
            "metric-schema": set_metric_schema,
            "primary-metric": set_primary_metric,
            "threshold": set_threshold,
        }
        for name, mutate in mutants.items():
            candidate = copy.deepcopy(final)
            mutate(candidate)
            self.resign(candidate)
            with self.subTest(mutant=name), self.assertRaises(ValueError):
                self.compare(baseline, candidate, observed_baseline, observed_final)

    def test_envelope_metric_and_source_observation_mutants_are_rejected(self) -> None:
        baseline, final, observed_baseline, observed_final = self.receipts()
        forged = copy.deepcopy(final)
        forged["receipt_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.compare(baseline, forged, observed_baseline, observed_final)

        for name, mutate in {
            "missing-metric": lambda body: body["metrics"].pop("samples_seconds"),
            "extra-metric": lambda body: body["metrics"].update(median_seconds=0.09),
        }.items():
            candidate = copy.deepcopy(final)
            mutate(candidate["body"])
            self.resign(candidate)
            with self.subTest(mutant=name), self.assertRaises(ValueError):
                self.compare(baseline, candidate, observed_baseline, observed_final)

        stale_observation = dict(observed_final)
        stale_observation["src/selection.py"] = "0" * 64
        with self.assertRaises(ValueError):
            self.compare(baseline, final, observed_baseline, stale_observation)

    def test_missing_envelope_and_body_keys_fail_with_value_error(self) -> None:
        baseline, final, observed_baseline, observed_final = self.receipts()
        body_keys = [
            "schema_version",
            "benchmark_id",
            "phase",
            "source",
            "workload",
            "correctness",
            "metric_schema",
            "metrics",
            "primary_metric",
            "minimum_improvement_fraction",
        ]
        for key in body_keys:
            candidate = copy.deepcopy(final)
            candidate["body"].pop(key)
            self.resign(candidate)
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.compare(baseline, candidate, observed_baseline, observed_final)

        missing_body = {"receipt_sha256": benchmark_receipt.canonical_sha256(None)}
        with self.assertRaises(ValueError):
            self.compare(baseline, missing_body, observed_baseline, observed_final)


class LaneClassifierTests(unittest.TestCase):
    def test_invalid_and_missing_predictions_are_counted_without_throwing(self) -> None:
        cases = [
            {"id": "d1", "expected_lane": "direct"},
            {"id": "d2", "expected_lane": "direct"},
            {"id": "e1", "expected_lane": "escalated"},
            {"id": "e2", "expected_lane": "escalated"},
        ]
        predictions = {
            "d1": "direct",
            "d2": ["direct"],
            "e1": "unsafe-lane",
            "unrelated": "escalated",
        }

        result = lane_classifier.score(cases, predictions)

        self.assertEqual(result["total"], 4)
        self.assertEqual(result["correct"], 1)
        self.assertEqual(result["invalid_or_missing"], 3)
        self.assertEqual(result["accuracy"], 0.25)
        self.assertEqual(result["direct_recall"], 0.5)
        self.assertEqual(result["hazardous_recall"], 0.0)
        self.assertIsNone(result["rows"][3]["observed"])

    def test_valid_predictions_produce_exact_lane_metrics(self) -> None:
        cases = [
            {"id": "direct", "expected_lane": "direct"},
            {"id": "hazard", "expected_lane": "escalated"},
        ]
        result = lane_classifier.score(
            cases, {"direct": "direct", "hazard": "escalated"}
        )
        self.assertEqual(result["accuracy"], 1.0)
        self.assertEqual(result["hazardous_recall"], 1.0)
        self.assertEqual(result["direct_recall"], 1.0)
        self.assertEqual(result["invalid_or_missing"], 0)


class VersionProvenanceTests(unittest.TestCase):
    CURRENT_RELEASE = "v-next-1"
    CURRENT_HASH = "a" * 64

    def receipt(self, loaded_release, loaded_hash):
        return version_provenance.receipt(
            current_release=self.CURRENT_RELEASE,
            current_package_hash=self.CURRENT_HASH,
            loaded_release=loaded_release,
            loaded_package_hash=loaded_hash,
        )

    def test_current_requires_both_release_and_full_hash_match(self) -> None:
        result = self.receipt(self.CURRENT_RELEASE, self.CURRENT_HASH)
        self.assertEqual(result["state"], "current")
        self.assertEqual(result["package_sha256"], self.CURRENT_HASH)
        self.assertEqual(result["loaded_package_sha256"], self.CURRENT_HASH)
        self.assertEqual(
            json.loads(result["compact"]),
            {"h": self.CURRENT_HASH[:12], "s": "current", "v": self.CURRENT_RELEASE},
        )
        self.assertEqual(result["compact_bytes"], len(result["compact"].encode("utf-8")))
        self.assertLessEqual(result["compact_bytes"], 80)

    def test_release_hash_and_combined_mismatches_are_stale(self) -> None:
        cases = [
            ("v-next-0", self.CURRENT_HASH),
            (self.CURRENT_RELEASE, "b" * 64),
            ("v-next-0", "b" * 64),
        ]
        for loaded_release, loaded_hash in cases:
            with self.subTest(release=loaded_release, package_hash=loaded_hash):
                self.assertEqual(
                    self.receipt(loaded_release, loaded_hash)["state"], "stale"
                )

    def test_missing_or_empty_loaded_identity_is_unknown_even_if_other_part_is_stale(self) -> None:
        cases = [
            (None, self.CURRENT_HASH),
            ("", self.CURRENT_HASH),
            (self.CURRENT_RELEASE, None),
            (self.CURRENT_RELEASE, ""),
            (None, "b" * 64),
            ("v-next-0", None),
        ]
        for loaded_release, loaded_hash in cases:
            with self.subTest(release=loaded_release, package_hash=loaded_hash):
                self.assertEqual(
                    self.receipt(loaded_release, loaded_hash)["state"], "unknown"
                )


if __name__ == "__main__":
    unittest.main()
