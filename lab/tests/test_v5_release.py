from __future__ import annotations

import copy
import hashlib
import json
import math
import shutil
import stat
import statistics
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


LAB = Path(__file__).resolve().parents[1]
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))

import build_v5_release as release  # noqa: E402


FAKE_SHA256 = "a" * 64
FAKE_RUNTIME = f'''\
import json
import sys

if sys.argv[1:] != ["provenance", "--format", "json"]:
    raise SystemExit(2)
print(json.dumps({{
    "release": "v5",
    "package_sha256": "{FAKE_SHA256}",
    "marker_sha256": "{FAKE_SHA256}",
    "package_integrity": True,
}}))
'''


class V5ReleaseTests(unittest.TestCase):
    def make_package(self, parent: Path) -> Path:
        package = parent / "endurant-harness"
        (package / "scripts").mkdir(parents=True)
        (package / "references").mkdir()
        (package / "SKILL.md").write_text("# Synthetic Endurant Harness\n", encoding="utf-8")
        (package / "scripts" / "endurant.py").write_text(
            FAKE_RUNTIME, encoding="utf-8"
        )
        (package / "references" / "protocol.md").write_text(
            "Synthetic protocol.\n", encoding="utf-8"
        )
        return package

    def build_paths(self, root: Path, name: str = "release") -> tuple[Path, Path]:
        return root / f"{name}.zip", root / f"{name}.json"

    @staticmethod
    def timing_summary(samples: list[float]) -> dict[str, object]:
        ordered = sorted(samples)
        return {
            "samples_seconds": samples,
            "p50_seconds": round(statistics.median(samples), 9),
            "p95_seconds": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 9),
            "min_seconds": round(min(samples), 9),
            "max_seconds": round(max(samples), 9),
        }

    def runtime_surface(self, name: str, delta: float) -> dict[str, object]:
        rows: list[dict[str, object]] = []
        combined_samples: list[float] = []
        v5_samples: list[float] = []
        empty_hash = hashlib.sha256(b"").hexdigest()
        output_hash = hashlib.sha256(b"ok\n").hexdigest()
        normalized_hash = "b" * 64
        for index in range(31):
            combined_seconds = round(0.05 + index * 0.00001, 9)
            v5_seconds = round(combined_seconds + delta, 9)
            combined_samples.append(combined_seconds)
            v5_samples.append(v5_seconds)
            observation = {
                "returncode": 0,
                "stderr_bytes": 0,
                "stderr_sha256": empty_hash,
                "stdout_bytes": 3,
                "stdout_sha256": output_hash,
                "timed_out": False,
            }
            if name == "template":
                validation: dict[str, object] = {
                    "combined_json_sha256": normalized_hash,
                    "combined_parse_error": None,
                    "semantic_equal": True,
                    "stderr_exact": True,
                    "stdout_exact": True,
                    "v5_json_sha256": normalized_hash,
                    "v5_parse_error": None,
                }
            elif name == "probe":
                validation = {
                    "combined_intentional_fields": {},
                    "combined_normalized_sha256": normalized_hash,
                    "combined_parse_error": None,
                    "semantic_equal": True,
                    "v5_intentional_fields": {},
                    "v5_normalized_sha256": normalized_hash,
                    "v5_parse_error": None,
                }
            else:
                validation = {
                    "combined_normalized_sha256": normalized_hash,
                    "combined_parse_error": None,
                    "combined_status": "passed",
                    "semantic_equal": True,
                    "status_equal": True,
                    "v5_normalized_sha256": normalized_hash,
                    "v5_parse_error": None,
                    "v5_status": "passed",
                }
            rows.append(
                {
                    "combined": {**observation, "seconds": combined_seconds},
                    "index": index,
                    "order": ["combined", "v5"]
                    if index % 2 == 0
                    else ["v5", "combined"],
                    "v5": {**observation, "seconds": v5_seconds},
                    "v5_minus_combined_seconds": round(
                        v5_seconds - combined_seconds, 9
                    ),
                    "validation": validation,
                }
            )
        combined = self.timing_summary(combined_samples)
        v5 = self.timing_summary(v5_samples)
        median_delta = float(v5["p50_seconds"]) - float(combined["p50_seconds"])
        p95_delta = float(v5["p95_seconds"]) - float(combined["p95_seconds"])
        comparison = {
            "median_absolute_delta_seconds": round(abs(median_delta), 9),
            "median_change_fraction": round(
                median_delta / float(combined["p50_seconds"]), 9
            ),
            "median_delta_seconds": round(median_delta, 9),
            "p95_absolute_delta_seconds": round(abs(p95_delta), 9),
            "p95_change_fraction": round(
                p95_delta / float(combined["p95_seconds"]), 9
            ),
            "p95_delta_seconds": round(p95_delta, 9),
            "v5_median_regression_limit_seconds": 0.025,
            "v5_median_regression_within_limit": True,
        }
        return {
            "alternating_order": True,
            "combined": combined,
            "comparison": comparison,
            "exit_parity": True,
            "pairs": 31,
            "raw_pairs": rows,
            "semantic_parity": True,
            "v5": v5,
            "warmups_per_runtime": 3,
        }

    def runtime_receipt_value(self) -> dict[str, object]:
        source_hashes = {
            relative: release._sha256_file(release.PROJECT_ROOT / relative)
            for relative in release.RUNTIME_SOURCE_PATHS
        }
        return {
            "benchmark": "endurant-v5-runtime",
            "command_failures": [],
            "configuration": {
                "alternating_order": True,
                "command_timeout_seconds": 90,
                "default_pairs": 31,
                "intentional_probe_differences": list(
                    release.RUNTIME_INTENTIONAL_PROBE_DIFFERENCES
                ),
                "pairs": 31,
                "probe_task": release.RUNTIME_PROBE_TASK,
                "v5_median_regression_limit_seconds": 0.025,
                "warmups_per_runtime_and_surface": 3,
            },
            "environment": {},
            "gates": {name: True for name in release.RUNTIME_GATE_NAMES},
            "passed": True,
            "schema_version": 1,
            "source": {
                "git_head": None,
                "git_status_sha256": None,
                "input_sha256_after": dict(source_hashes),
                "input_sha256_before": dict(source_hashes),
                "probe_task_sha256": hashlib.sha256(
                    release.RUNTIME_PROBE_TASK.encode("utf-8")
                ).hexdigest(),
                "runner_plan_sha256": "c" * 64,
            },
            "surfaces": {
                "probe": self.runtime_surface("probe", 0.005),
                "runner": self.runtime_surface("runner", 0.006),
                "template": self.runtime_surface("template", 0.007),
            },
        }

    def make_runtime_receipt(self, root: Path) -> Path:
        path = root / "v5-runtime.json"
        path.write_bytes(release._canonical_json_bytes(self.runtime_receipt_value()))
        return path

    def test_build_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-release-test-") as raw:
            root = Path(raw)
            package = self.make_package(root / "source")
            runtime_receipt = self.make_runtime_receipt(root)
            first_archive, first_receipt = self.build_paths(root, "first")
            second_archive, second_receipt = self.build_paths(root, "second")

            release.build_release(
                package, first_archive, first_receipt, runtime_receipt
            )
            release.build_release(
                package, second_archive, second_receipt, runtime_receipt
            )

            self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
            self.assertEqual(first_receipt.read_bytes(), second_receipt.read_bytes())
            receipt = release.verify_release(
                package, first_archive, first_receipt, runtime_receipt
            )
            self.assertEqual(receipt["package"]["sha256"], FAKE_SHA256)
            self.assertEqual(
                receipt["runtime_benchmark"]["receipt_sha256"],
                release._sha256_file(runtime_receipt),
            )
            self.assertEqual(
                receipt["runtime_benchmark"]["surfaces"]["probe"],
                {
                    "combined_p50_seconds": 0.05015,
                    "median_delta_seconds": 0.005,
                    "v5_p50_seconds": 0.05515,
                },
            )

    def test_archive_has_exact_sorted_single_root_layout_and_fixed_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-release-test-") as raw:
            root = Path(raw)
            package = self.make_package(root / "source")
            runtime_receipt = self.make_runtime_receipt(root)
            archive_path, receipt_path = self.build_paths(root)
            receipt = release.build_release(
                package, archive_path, receipt_path, runtime_receipt
            )

            expected = [
                "endurant-harness/SKILL.md",
                "endurant-harness/references/protocol.md",
                "endurant-harness/scripts/endurant.py",
            ]
            self.assertEqual(
                [item["path"] for item in receipt["archive"]["members"]], expected
            )
            with zipfile.ZipFile(archive_path) as archive:
                infos = archive.infolist()
                self.assertEqual([info.filename for info in infos], expected)
                for info in infos:
                    self.assertFalse(info.is_dir())
                    self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(info.date_time, release.FIXED_ZIP_TIME)
                    self.assertEqual(info.create_system, 3)
                    self.assertEqual(
                        info.external_attr >> 16, stat.S_IFREG | 0o644
                    )

    def test_forbidden_files_are_rejected(self) -> None:
        forbidden = (
            "README.md",
            "CHANGELOG",
            "notes.bak",
            "scratch.tmp",
            "__pycache__/endurant.pyc",
            "backups/SKILL.md.orig",
        )
        for relative in forbidden:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory(
                prefix="v5-release-test-"
            ) as raw:
                root = Path(raw)
                package = self.make_package(root / "source")
                runtime_receipt = self.make_runtime_receipt(root)
                target = package / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"forbidden")
                archive_path, receipt_path = self.build_paths(root)
                with self.assertRaises(release.ReleaseError):
                    release.build_release(
                        package, archive_path, receipt_path, runtime_receipt
                    )
                self.assertFalse(archive_path.exists())
                self.assertFalse(receipt_path.exists())

    def test_symlink_in_package_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-release-test-") as raw:
            root = Path(raw)
            package = self.make_package(root / "source")
            runtime_receipt = self.make_runtime_receipt(root)
            link = package / "references" / "linked.md"
            try:
                link.symlink_to(package / "SKILL.md")
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            archive_path, receipt_path = self.build_paths(root)
            with self.assertRaises(release.ReleaseError):
                release.build_release(
                    package, archive_path, receipt_path, runtime_receipt
                )

    def test_tampered_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-release-test-") as raw:
            root = Path(raw)
            package = self.make_package(root / "source")
            runtime_receipt = self.make_runtime_receipt(root)
            archive_path, receipt_path = self.build_paths(root)
            release.build_release(package, archive_path, receipt_path, runtime_receipt)
            value = json.loads(receipt_path.read_text(encoding="utf-8"))
            value["archive"]["sha256"] = "0" * 64
            receipt_path.write_text(
                json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(release.ReleaseError):
                release.verify_release(
                    package, archive_path, receipt_path, runtime_receipt
                )

    def test_tampered_archive_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-release-test-") as raw:
            root = Path(raw)
            package = self.make_package(root / "source")
            runtime_receipt = self.make_runtime_receipt(root)
            archive_path, receipt_path = self.build_paths(root)
            release.build_release(package, archive_path, receipt_path, runtime_receipt)
            with archive_path.open("ab") as handle:
                handle.write(b"tampered")
            with self.assertRaises(release.ReleaseError):
                release.verify_release(
                    package, archive_path, receipt_path, runtime_receipt
                )

    def test_appended_archive_is_rejected_even_with_recomputed_receipt_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-release-test-") as raw:
            root = Path(raw)
            package = self.make_package(root / "source")
            runtime_receipt = self.make_runtime_receipt(root)
            archive_path, receipt_path = self.build_paths(root)
            release.build_release(package, archive_path, receipt_path, runtime_receipt)
            with archive_path.open("ab") as handle:
                handle.write(b"appended-but-not-a-member")
            value = json.loads(receipt_path.read_text(encoding="utf-8"))
            value["archive"]["sha256"] = release._sha256_file(archive_path)
            value["archive"]["size_bytes"] = archive_path.stat().st_size
            receipt_path.write_bytes(release._canonical_json_bytes(value))

            with self.assertRaises(release.ReleaseError):
                release.verify_release(
                    package, archive_path, receipt_path, runtime_receipt
                )

    def test_build_refuses_existing_outputs_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-release-test-") as raw:
            root = Path(raw)
            package = self.make_package(root / "source")
            runtime_receipt = self.make_runtime_receipt(root)
            archive_path, receipt_path = self.build_paths(root)
            archive_path.write_bytes(b"keep-me")
            with self.assertRaises(release.ReleaseError):
                release.build_release(
                    package, archive_path, receipt_path, runtime_receipt
                )
            self.assertEqual(archive_path.read_bytes(), b"keep-me")
            self.assertFalse(receipt_path.exists())

    def test_runtime_receipt_mutations_are_rejected_without_rerunning_timings(self) -> None:
        mutations = {
            "schema": lambda value: value.__setitem__("schema_version", 2),
            "gate": lambda value: value["gates"].__setitem__(
                "v5_probe_median_regression_within_25ms", False
            ),
            "pairs": lambda value: value["configuration"].__setitem__("pairs", 30),
            "warmups": lambda value: value["configuration"].__setitem__(
                "warmups_per_runtime_and_surface", 2
            ),
            "limit": lambda value: value["configuration"].__setitem__(
                "v5_median_regression_limit_seconds", 0.026
            ),
            "command-failure": lambda value: value["command_failures"].append(
                {"surface": "probe"}
            ),
            "source-before": lambda value: value["source"][
                "input_sha256_before"
            ].__setitem__(release.RUNTIME_SOURCE_PATHS[0], "0" * 64),
            "source-after": lambda value: value["source"][
                "input_sha256_after"
            ].__setitem__(release.RUNTIME_SOURCE_PATHS[1], "0" * 64),
            "p50-summary": lambda value: value["surfaces"]["probe"]["v5"].__setitem__(
                "p50_seconds", 0.001
            ),
            "raw-pair": lambda value: value["surfaces"]["runner"]["raw_pairs"][
                0
            ]["v5"].__setitem__("seconds", 0.2),
            "median-delta": lambda value: value["surfaces"]["template"][
                "comparison"
            ].__setitem__("median_delta_seconds", 0.0),
        }
        pristine = self.runtime_receipt_value()
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory(
                prefix="v5-release-test-"
            ) as raw:
                root = Path(raw)
                package = self.make_package(root / "source")
                value = copy.deepcopy(pristine)
                mutate(value)
                runtime_receipt = root / "v5-runtime.json"
                runtime_receipt.write_bytes(release._canonical_json_bytes(value))
                archive_path, receipt_path = self.build_paths(root)
                with self.assertRaises(release.ReleaseError):
                    release.build_release(
                        package, archive_path, receipt_path, runtime_receipt
                    )
                self.assertFalse(archive_path.exists())
                self.assertFalse(receipt_path.exists())

    def test_release_receipt_runtime_hash_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-release-test-") as raw:
            root = Path(raw)
            package = self.make_package(root / "source")
            runtime_receipt = self.make_runtime_receipt(root)
            archive_path, receipt_path = self.build_paths(root)
            release.build_release(package, archive_path, receipt_path, runtime_receipt)
            value = json.loads(receipt_path.read_text(encoding="utf-8"))
            value["runtime_benchmark"]["receipt_sha256"] = "0" * 64
            receipt_path.write_bytes(release._canonical_json_bytes(value))
            with self.assertRaises(release.ReleaseError):
                release.verify_release(
                    package, archive_path, receipt_path, runtime_receipt
                )

    def test_verify_source_recomputes_archive_without_reading_or_writing_one(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-release-test-") as raw:
            root = Path(raw)
            package = self.make_package(root / "source")
            runtime_receipt = self.make_runtime_receipt(root)
            archive_path, receipt_path = self.build_paths(root)
            expected = release.build_release(
                package, archive_path, receipt_path, runtime_receipt
            )
            archive_path.unlink()
            self.assertEqual(list(root.glob("*.zip")), [])

            with patch.object(
                release,
                "_inspect_archive",
                side_effect=AssertionError("verify-source read an archive"),
            ):
                observed = release.verify_source(
                    package, receipt_path, runtime_receipt
                )

            self.assertEqual(observed, expected)
            self.assertEqual(list(root.glob("*.zip")), [])

    def test_verify_source_rejects_tampered_archive_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-release-test-") as raw:
            root = Path(raw)
            package = self.make_package(root / "source")
            runtime_receipt = self.make_runtime_receipt(root)
            archive_path, receipt_path = self.build_paths(root)
            release.build_release(package, archive_path, receipt_path, runtime_receipt)
            archive_path.unlink()
            value = json.loads(receipt_path.read_text(encoding="utf-8"))
            value["archive"]["sha256"] = "0" * 64
            receipt_path.write_bytes(release._canonical_json_bytes(value))

            with self.assertRaises(release.ReleaseError):
                release.verify_source(package, receipt_path, runtime_receipt)
            self.assertFalse(archive_path.exists())

    def test_verify_rejects_archive_with_extra_top_level_member(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-release-test-") as raw:
            root = Path(raw)
            package = self.make_package(root / "source")
            runtime_receipt = self.make_runtime_receipt(root)
            archive_path, receipt_path = self.build_paths(root)
            release.build_release(package, archive_path, receipt_path, runtime_receipt)

            forged = root / "forged.zip"
            shutil.copyfile(archive_path, forged)
            with zipfile.ZipFile(forged, "a", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("outside.txt", b"outside")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["archive"]["sha256"] = release._sha256_file(forged)
            receipt["archive"]["size_bytes"] = forged.stat().st_size
            forged_receipt = root / "forged.json"
            forged_receipt.write_bytes(release._canonical_json_bytes(receipt))

            with self.assertRaises(release.ReleaseError):
                release.verify_release(
                    package, forged, forged_receipt, runtime_receipt
                )


if __name__ == "__main__":
    unittest.main()
