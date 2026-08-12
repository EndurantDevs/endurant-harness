from __future__ import annotations

import importlib.util
import json
import os
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "subjects" / "vnext" / "endurant-harness" / "scripts" / "endurant.py"
SPEC = importlib.util.spec_from_file_location("endurant_vnext_runtime", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)


def wait_for_path(path: Path, process: subprocess.Popen[str], timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"process exited before canary was ready: {stdout} {stderr}")
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for canary path: {path}")


def process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def wait_for_process_death(pid: int, timeout: float = 8) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_is_running(pid):
            return True
        time.sleep(0.05)
    return not process_is_running(pid)


class RepositoryFixture:
    def __init__(self, parent: Path) -> None:
        self.root = parent / "repo"
        self.root.mkdir()
        self.run("init", "-q")
        self.run("config", "user.email", "synthetic@example.invalid")
        self.run("config", "user.name", "Synthetic Test")

    def run(self, *args: str) -> None:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)

    def commit(self) -> None:
        self.run("add", ".")
        self.run("commit", "-qm", "synthetic fixture")


class ProbeRelevanceTests(unittest.TestCase):
    def test_exact_symbols_are_nul_safe_ranked_and_suppress_harness_noise(self) -> None:
        captured: list[list[str]] = []

        def exact(argv, root, timeout=2):
            captured.append(list(argv))
            return (
                0,
                [
                    "./docs/select_records.md",
                    "./tests/test_record_selection.py",
                    "./src/select_records\nimplementation.py",
                    "./subjects/endurant-harness/SKILL.md",
                ],
                False,
            )

        with (
            mock.patch.object(runtime.shutil, "which", return_value="/bin/rg"),
            mock.patch.object(runtime, "_run_capture_nul", side_effect=exact),
        ):
            paths, warnings = runtime._candidate_paths(
                Path("/synthetic/repo"), "Fix select_records", 5
            )

        self.assertEqual(warnings, [])
        self.assertEqual(
            paths[:2],
            [
                "./src/select_records\nimplementation.py",
                "./tests/test_record_selection.py",
            ],
        )
        self.assertNotIn("./subjects/endurant-harness/SKILL.md", paths)
        self.assertIn("-0", captured[0])
        self.assertIn("-F", captured[0])

    def test_plain_language_preserves_the_broad_fallback_object(self) -> None:
        expected = (["./src/parser.py"], ["broad warning"])
        with mock.patch.object(runtime, "_broad_candidate_paths", return_value=expected):
            actual = runtime._candidate_paths(
                Path("/synthetic/repo"), "Fix parser behavior", 5
            )
        self.assertIs(actual, expected)

    def test_failed_exact_search_preserves_snake_case_in_broad_fallback(self) -> None:
        captured: list[list[str]] = []

        def broad(argv, root, timeout=2):
            captured.append(list(argv))
            return (0, "./src/record_selection.py\n", False)

        with (
            mock.patch.object(runtime.shutil, "which", return_value="/bin/rg"),
            mock.patch.object(runtime, "_run_capture_nul", return_value=(124, [], False)),
            mock.patch.object(runtime, "_run_capture", side_effect=broad),
        ):
            paths, warnings = runtime._candidate_paths(
                Path("/synthetic/repo"), "Fix select_records behavior", 5
            )

        self.assertEqual(paths, ["./src/record_selection.py"])
        self.assertEqual(warnings, [])
        self.assertIn("select_records", captured[0][-2])


class StrictContractTests(unittest.TestCase):
    def test_log_dir_help_describes_a_private_per_run_child(self) -> None:
        for arguments in (["run"], ["preflight"], ["benchmark", "baseline"]):
            completed = subprocess.run(
                [sys.executable, "-S", str(SCRIPT), *arguments, "--help"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertEqual(
                completed.returncode, 0, completed.stderr or completed.stdout
            )
            self.assertIn(
                "parent for a new private per-run log directory", completed.stdout
            )
            self.assertIn("Git-ignored", completed.stdout)

    def test_duplicate_json_reserved_env_unknown_keys_and_symlinks_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate key"):
            runtime._strict_json_bytes(b'{"a":1,"a":2}', "profile")
        with self.assertRaisesRegex(ValueError, "reserved"):
            runtime._contract_command(
                {"argv": ["python3", "verify.py"], "env": {"ENDURANT_FAKE": "1"}},
                "command",
            )
        with self.assertRaisesRegex(ValueError, "unexpected"):
            runtime._validate_preflight_profile(
                {
                    "schema_version": 1,
                    "checks": {"focused": {"argv": ["python3"]}},
                    "bundles": {
                        "local": {
                            "command": {"argv": ["python3"]},
                            "covers": ["focused"],
                            "claim": "untrusted",
                        }
                    },
                }
            )
        with tempfile.TemporaryDirectory(prefix="vnext-symlink-") as raw:
            root = Path(raw)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            (root / "profile.json").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlinks"):
                runtime._repo_file_bytes(root, "profile.json", "profile")

    def test_command_logs_never_follow_or_overwrite_existing_entries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vnext-log-guard-") as raw:
            root = Path(raw).resolve()
            log_parent = root / "logs"
            log_parent.mkdir()
            victim = root / "victim.txt"
            victim.write_text("preserve-me\n", encoding="utf-8")
            command = {
                "id": "proof",
                "argv": [sys.executable, "-S", "-c", "print('new output')"],
                "timeout": 5.0,
                "expected_exit_codes": [0],
                "env": {},
                "evidence": "other",
            }

            (log_parent / "proof.log").symlink_to(victim)
            symlinked = runtime._execute_command(
                "security", command, root, log_parent, 5
            )
            self.assertEqual(symlinked.status, "error")
            self.assertEqual(symlinked.tail, [])
            self.assertEqual(victim.read_text(encoding="utf-8"), "preserve-me\n")

            (log_parent / "proof.log").unlink()
            (log_parent / "proof.log").write_text("existing\n", encoding="utf-8")
            existing = runtime._execute_command(
                "security", command, root, log_parent, 5
            )
            self.assertEqual(existing.status, "error")
            self.assertEqual(
                (log_parent / "proof.log").read_text(encoding="utf-8"),
                "existing\n",
            )

            private = runtime._private_log_dir(str(log_parent), "private-")
            self.assertNotEqual(private, log_parent)
            isolated = runtime._execute_command("security", command, root, private, 5)
            self.assertEqual(isolated.status, "passed")
            self.assertEqual(victim.read_text(encoding="utf-8"), "preserve-me\n")

    def test_fingerprint_hashes_external_untracked_symlink_without_following(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vnext-fingerprint-link-") as raw:
            base = Path(raw)
            repository = RepositoryFixture(base)
            first_target = base / "first.txt"
            second_target = base / "second.txt"
            first_target.write_text("first\n", encoding="utf-8")
            second_target.write_text("second\n", encoding="utf-8")
            link = repository.root / "data-link"
            link.symlink_to(first_target)

            initial = runtime._diff_fingerprint(repository.root)
            first_target.write_text("changed target content\n", encoding="utf-8")
            self.assertEqual(runtime._diff_fingerprint(repository.root), initial)

            link.unlink()
            link.symlink_to(second_target)
            self.assertNotEqual(runtime._diff_fingerprint(repository.root), initial)


class RunnerCwdFailureTests(unittest.TestCase):
    def run_plan(self, plan: dict[str, object], root: Path) -> subprocess.CompletedProcess[str]:
        plan_path = root.parent / f"plan-{time.time_ns()}.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                "-S",
                str(SCRIPT),
                "run",
                str(plan_path),
                "--repo",
                str(root),
                "--format",
                "json",
                "--log-dir",
                str(root.parent / f"logs-{time.time_ns()}"),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_invalid_cwd_is_structured_and_always_cleanup_runs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vnext-cwd-error-") as raw:
            repository = RepositoryFixture(Path(raw))
            plan = {
                "stages": [
                    {
                        "name": "bad",
                        "commands": [
                            {
                                "id": "bad-cwd",
                                "argv": [sys.executable, "-S", "-c", "pass"],
                                "cwd": "../outside",
                                "evidence": "behavior",
                            }
                        ],
                    },
                    {
                        "name": "cleanup",
                        "run_if": "always",
                        "commands": [
                            {
                                "id": "cleanup",
                                "argv": [
                                    sys.executable,
                                    "-S",
                                    "-c",
                                    "from pathlib import Path; Path('cleanup-ran').write_text('yes')",
                                ],
                                "evidence": "cleanup",
                            }
                        ],
                    },
                ]
            }
            completed = self.run_plan(plan, repository.root)
            self.assertEqual(completed.returncode, 1, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["stages"][0]["commands"][0]["status"], "error")
            self.assertTrue((repository.root / "cleanup-ran").is_file())

    def test_deadline_result_keeps_invalid_cwd_as_structured_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vnext-cwd-deadline-") as raw:
            root = Path(raw) / "repo"
            result = runtime._deadline_result(  # noqa: SLF001
                "deadline",
                {
                    "id": "bad-cwd",
                    "argv": [sys.executable, "-S", "-c", "pass"],
                    "cwd": "../outside",
                    "evidence": "other",
                    "expected_exit_codes": [0],
                },
                root,
                Path(raw) / "logs",
            )
            self.assertEqual(result.status, "timeout")
            self.assertEqual(result.cwd, str(root / "../outside"))


class AuditEvalCatalogTests(unittest.TestCase):
    def run_audit(self, package: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                str(package / "scripts" / "audit_skill.py"),
                str(package),
                "--format",
                "json",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return completed, json.loads(completed.stdout)

    def test_audit_labels_catalogs_as_specs_and_reports_soft_word_target(self) -> None:
        package = SCRIPT.parents[1]
        completed, result = self.run_audit(package)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        metrics = result["metrics"]
        self.assertEqual(metrics["eval_spec_cases"], 23)
        self.assertEqual(metrics["trigger_spec_cases"], 12)
        self.assertEqual(metrics["skill_word_target"], 450)
        self.assertTrue(metrics["skill_word_target_met"])

    def test_audit_rejects_incomplete_eval_and_trigger_specs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vnext-eval-schema-") as raw:
            package = Path(raw) / "endurant-harness"
            shutil.copytree(SCRIPT.parents[1], package)

            eval_path = package / "evals" / "evals.json"
            evals = json.loads(eval_path.read_text(encoding="utf-8"))
            evals["evals"][0]["expected_output"] = ""
            evals["evals"][1]["assertions"][0] = 7
            eval_path.write_text(json.dumps(evals), encoding="utf-8")

            trigger_path = package / "evals" / "trigger-cases.json"
            triggers = json.loads(trigger_path.read_text(encoding="utf-8"))
            triggers["cases"][0]["prompt"] = ""
            trigger_path.write_text(json.dumps(triggers), encoding="utf-8")

            completed, result = self.run_audit(package)
            self.assertEqual(completed.returncode, 1, completed.stderr or completed.stdout)
            codes = {item["code"] for item in result["errors"]}
            self.assertIn("evals.expected_output", codes)
            self.assertIn("evals.assertions", codes)
            self.assertIn("evals.trigger", codes)


class FastPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="vnext-preflight-")
        self.base = Path(self.temporary.name)
        self.repo = RepositoryFixture(self.base)
        (self.repo.root / ".agents").mkdir()
        (self.repo.root / "scripts").mkdir()
        (self.repo.root / "data.txt").write_text("stable\n", encoding="utf-8")
        (self.repo.root / "scripts" / "preflight.py").write_text(
            """from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from pathlib import Path

count = Path(os.environ["COUNT_PATH"])
count.write_text(str(int(count.read_text() or "0") + 1), encoding="utf-8")
if os.environ.get("SLOW_CHILD_PID"):
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        ]
    )
    Path(os.environ["SLOW_CHILD_PID"]).write_text(str(child.pid), encoding="utf-8")
    time.sleep(60)
if os.environ.get("MUTATE_REPO"):
    Path("data.txt").write_text("mutated\\n", encoding="utf-8")
passed = 1 if os.environ.get("BAD_RECEIPT") else True
receipt = {
    "schema_version": 1,
    "profile_sha256": os.environ["ENDURANT_PROFILE_SHA256"],
    "verification_sha256": os.environ["ENDURANT_VERIFICATION_SHA256"],
    "bundle_id": os.environ["ENDURANT_BUNDLE_ID"],
    "checks": [{"id": "focused", "passed": passed}],
}
Path(os.environ["ENDURANT_RECEIPT_PATH"]).write_text(
    json.dumps(receipt), encoding="utf-8"
)
""",
            encoding="utf-8",
        )
        self.profile = {
            "schema_version": 1,
            "checks": {
                "focused": {
                    "argv": [sys.executable, "scripts/preflight.py"],
                    "evidence": "behavior",
                    "env": {"PYTHONDONTWRITEBYTECODE": "1"},
                }
            },
            "bundles": {
                "local-ci": {
                    "command": {
                        "argv": [sys.executable, "scripts/preflight.py"],
                        "evidence": "integration",
                        "env": {"PYTHONDONTWRITEBYTECODE": "1"},
                    },
                    "covers": ["focused"],
                }
            },
        }
        self.profile_path = (
            self.repo.root / ".agents" / "endurant-harness-preflight.json"
        )
        self.profile_path.write_text(
            json.dumps(self.profile, indent=2) + "\n", encoding="utf-8"
        )
        self.repo.commit()
        self.profile_sha256 = runtime._canonical_sha256(self.profile)
        self.count = self.base / "preflight-count.txt"
        self.count.write_text("0", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def args(self, profile_sha256: str | None = None) -> Namespace:
        return Namespace(
            repo=str(self.repo.root),
            bundle="local-ci",
            require=["focused"],
            profile_sha256=profile_sha256 or self.profile_sha256,
            log_dir=None,
            max_tail_lines=5,
        )

    def test_bundle_runs_once_and_verified_receipt_covers_behavior(self) -> None:
        with mock.patch.dict(os.environ, {"COUNT_PATH": str(self.count)}, clear=False):
            result = runtime.run_preflight(self.args())
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["receipt_verified"])
        self.assertEqual(self.count.read_text(encoding="utf-8"), "1")
        self.assertEqual(result["evidence_summary"]["behavior"]["passed"], 1)
        self.assertEqual(len(result["commands"]), 1)

    def test_preflight_cli_dispatches_the_pinned_bundle_once(self) -> None:
        environment = {**os.environ, "COUNT_PATH": str(self.count), "PYTHONDONTWRITEBYTECODE": "1"}
        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                str(SCRIPT),
                "preflight",
                "--repo",
                str(self.repo.root),
                "--bundle",
                "local-ci",
                "--require",
                "focused",
                "--profile-sha256",
                self.profile_sha256,
                "--format",
                "json",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertEqual(json.loads(completed.stdout)["status"], "passed")
        self.assertEqual(self.count.read_text(encoding="utf-8"), "1")

    @unittest.skipIf(os.name == "nt", "POSIX process-group canary")
    def test_preflight_sigterm_kills_descendant_and_removes_partial_receipt(self) -> None:
        child_path = self.base / "preflight-child.pid"
        log_parent = self.base / "preflight-sigterm-logs"
        environment = {
            **os.environ,
            "COUNT_PATH": str(self.count),
            "SLOW_CHILD_PID": str(child_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-S",
                str(SCRIPT),
                "preflight",
                "--repo",
                str(self.repo.root),
                "--bundle",
                "local-ci",
                "--require",
                "focused",
                "--profile-sha256",
                self.profile_sha256,
                "--log-dir",
                str(log_parent),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        child_pid: int | None = None
        try:
            wait_for_path(child_path, process)
            child_pid = int(child_path.read_text(encoding="utf-8"))
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=12)
            self.assertEqual(process.returncode, 2, stderr or stdout)
            dead = wait_for_process_death(child_pid)
            self.assertTrue(dead, f"preflight descendant survived: {child_pid}")
            self.assertEqual(
                list(log_parent.rglob("preflight-receipt-*.json")), []
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if child_pid is not None and process_is_running(child_pid):
                os.kill(child_pid, signal.SIGKILL)

    def test_stale_pin_bad_boolean_and_mutated_diff_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            runtime.run_preflight(self.args("f" * 64))
        self.assertEqual(self.count.read_text(encoding="utf-8"), "0")

        with mock.patch.dict(
            os.environ,
            {"COUNT_PATH": str(self.count), "BAD_RECEIPT": "1"},
            clear=False,
        ):
            invalid = runtime.run_preflight(self.args())
        self.assertEqual(invalid["status"], "failed")
        self.assertFalse(invalid["receipt_verified"])

        self.count.write_text("0", encoding="utf-8")
        with mock.patch.dict(
            os.environ,
            {"COUNT_PATH": str(self.count), "MUTATE_REPO": "1"},
            clear=False,
        ):
            mutated = runtime.run_preflight(self.args())
        self.assertEqual(mutated["status"], "failed")
        self.assertTrue(
            any("diff changed" in item for item in mutated["proof_errors"]),
            mutated,
        )
        self.assertEqual(self.count.read_text(encoding="utf-8"), "1")


class BenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="vnext-benchmark-")
        self.base = Path(self.temporary.name)
        self.repo = RepositoryFixture(self.base)
        (self.repo.root / ".agents").mkdir()
        (self.repo.root / "scripts").mkdir()
        (self.repo.root / "src").mkdir()
        (self.repo.root / "src" / "mode.txt").write_text("slow\n", encoding="utf-8")
        (self.repo.root / "scripts" / "benchmark.py").write_text(
            """from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from pathlib import Path

count = Path(os.environ["COUNT_PATH"])
count.write_text(str(int(count.read_text() or "0") + 1), encoding="utf-8")
if os.environ.get("SLOW_CHILD_PID"):
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        ]
    )
    Path(os.environ["SLOW_CHILD_PID"]).write_text(str(child.pid), encoding="utf-8")
    time.sleep(60)
if os.environ.get("SWAP_RECEIPT_PARENT"):
    receipt_parent = Path(os.environ["SWAP_RECEIPT_PARENT"])
    moved_parent = Path(os.environ["SWAPPED_RECEIPT_PARENT"])
    receipt_parent.rename(moved_parent)
    receipt_parent.symlink_to(Path.cwd(), target_is_directory=True)
if os.environ.get("HARDLINK_RECEIPT_SOURCE"):
    hardlink_target = Path(os.environ["HARDLINK_RECEIPT_TARGET"])
    hardlink_target.parent.mkdir(parents=True, exist_ok=True)
    os.link(Path(os.environ["HARDLINK_RECEIPT_SOURCE"]), hardlink_target)
mode = Path("src/mode.txt").read_text(encoding="utf-8").strip()
event = {
    "schema_version": 1,
    "correctness": {"output_digest": "stable", "result_count": 4},
    "metrics": {"p95_seconds": 2.0 if mode == "slow" else 1.0},
}
Path(os.environ["ENDURANT_BENCHMARK_EVENT_PATH"]).write_text(
    json.dumps(event), encoding="utf-8"
)
""",
            encoding="utf-8",
        )
        self.profile = {
            "schema_version": 1,
            "benchmarks": {
                "record-selection": {
                    "command": {
                        "argv": [sys.executable, "scripts/benchmark.py"],
                        "env": {"PYTHONDONTWRITEBYTECODE": "1"},
                    },
                    "source_files": ["src/mode.txt"],
                    "workload_files": ["scripts/benchmark.py"],
                    "correctness_keys": ["output_digest", "result_count"],
                    "metric_schema": {
                        "p95_seconds": {
                            "type": "number",
                            "unit": "seconds",
                            "direction": "lower",
                        }
                    },
                    "primary_metric": "p95_seconds",
                    "minimum_improvement_fraction": 0.4,
                }
            },
        }
        profile_path = (
            self.repo.root / ".agents" / "endurant-harness-benchmarks.json"
        )
        profile_path.write_text(
            json.dumps(self.profile, indent=2) + "\n", encoding="utf-8"
        )
        self.repo.commit()
        self.profile_sha256 = runtime._canonical_sha256(self.profile)
        self.count = self.base / "benchmark-count.txt"
        self.count.write_text("0", encoding="utf-8")
        self.baseline = self.base / "baseline.json"
        self.final = self.base / "final.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def args(self, phase: str, receipt: Path, baseline: Path | None = None) -> Namespace:
        return Namespace(
            repo=str(self.repo.root),
            benchmark_phase=phase,
            benchmark_id="record-selection",
            profile_sha256=self.profile_sha256,
            receipt=str(receipt),
            baseline=str(baseline) if baseline else None,
            log_dir=None,
            max_tail_lines=5,
        )

    def test_baseline_and_final_each_run_once_with_bound_comparison(self) -> None:
        with mock.patch.dict(os.environ, {"COUNT_PATH": str(self.count)}, clear=False):
            baseline = runtime.run_benchmark_phase(
                self.args("baseline", self.baseline)
            )
            self.assertEqual(baseline["status"], "passed")
            receipt_stat = self.baseline.lstat()
            self.assertTrue(stat.S_ISREG(receipt_stat.st_mode))
            self.assertEqual(stat.S_IMODE(receipt_stat.st_mode), 0o600)
            self.assertEqual(self.count.read_text(encoding="utf-8"), "1")
            (self.repo.root / "src" / "mode.txt").write_text(
                "fast\n", encoding="utf-8"
            )
            final = runtime.run_benchmark_phase(
                self.args("final", self.final, self.baseline)
            )

        self.assertEqual(final["status"], "passed")
        self.assertEqual(self.count.read_text(encoding="utf-8"), "2")
        self.assertTrue(final["comparison"]["passed"])
        self.assertEqual(final["comparison"]["improvement_fraction"], 0.5)
        self.assertTrue(final["comparison"]["source_changed"])
        self.assertTrue(final["comparison"]["workload_identical"])

    def test_benchmark_cli_runs_exactly_one_baseline_and_final(self) -> None:
        environment = {**os.environ, "COUNT_PATH": str(self.count), "PYTHONDONTWRITEBYTECODE": "1"}
        common = [
            "--repo",
            str(self.repo.root),
            "--profile-sha256",
            self.profile_sha256,
            "--format",
            "json",
        ]
        baseline = subprocess.run(
            [
                sys.executable,
                "-S",
                str(SCRIPT),
                "benchmark",
                "baseline",
                "record-selection",
                *common,
                "--receipt",
                str(self.baseline),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        self.assertEqual(baseline.returncode, 0, baseline.stderr or baseline.stdout)
        (self.repo.root / "src" / "mode.txt").write_text("fast\n", encoding="utf-8")
        final = subprocess.run(
            [
                sys.executable,
                "-S",
                str(SCRIPT),
                "benchmark",
                "final",
                "record-selection",
                *common,
                "--baseline",
                str(self.baseline),
                "--receipt",
                str(self.final),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        self.assertEqual(final.returncode, 0, final.stderr or final.stdout)
        payload = json.loads(final.stdout)
        self.assertEqual(payload["status"], "passed")
        self.assertTrue(payload["comparison"]["passed"])
        self.assertEqual(self.count.read_text(encoding="utf-8"), "2")

    @unittest.skipIf(os.name == "nt", "POSIX process-group canary")
    def test_benchmark_sigterm_kills_descendant_and_removes_partial_receipt(self) -> None:
        child_path = self.base / "benchmark-child.pid"
        log_parent = self.base / "benchmark-sigterm-logs"
        environment = {
            **os.environ,
            "COUNT_PATH": str(self.count),
            "SLOW_CHILD_PID": str(child_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-S",
                str(SCRIPT),
                "benchmark",
                "baseline",
                "record-selection",
                "--repo",
                str(self.repo.root),
                "--profile-sha256",
                self.profile_sha256,
                "--receipt",
                str(self.baseline),
                "--log-dir",
                str(log_parent),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        child_pid: int | None = None
        try:
            wait_for_path(child_path, process)
            child_pid = int(child_path.read_text(encoding="utf-8"))
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=12)
            self.assertEqual(process.returncode, 2, stderr or stdout)
            dead = wait_for_process_death(child_pid)
            self.assertTrue(dead, f"benchmark descendant survived: {child_pid}")
            self.assertFalse(self.baseline.exists())
            self.assertEqual(
                list(log_parent.rglob("benchmark-*-event-*.json")), []
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if child_pid is not None and process_is_running(child_pid):
                os.kill(child_pid, signal.SIGKILL)

    @unittest.skipIf(os.name == "nt", "POSIX receipt-parent race canary")
    def test_parent_swap_cannot_redirect_receipt_into_repository(self) -> None:
        receipt_parent = self.base / "external-receipts"
        moved_parent = self.base / "external-receipts-moved"
        receipt_parent.mkdir()
        receipt = receipt_parent / "receipt.json"
        repository_receipt = self.repo.root / receipt.name
        initial_fingerprint = runtime._diff_fingerprint(self.repo.root)
        environment = {
            **os.environ,
            "COUNT_PATH": str(self.count),
            "SWAP_RECEIPT_PARENT": str(receipt_parent),
            "SWAPPED_RECEIPT_PARENT": str(moved_parent),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                str(SCRIPT),
                "benchmark",
                "baseline",
                "record-selection",
                "--repo",
                str(self.repo.root),
                "--profile-sha256",
                self.profile_sha256,
                "--receipt",
                str(receipt),
                "--format",
                "json",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )

        self.assertEqual(completed.returncode, 1, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertTrue(
            any("parent changed" in error for error in payload["proof_errors"]),
            payload,
        )
        self.assertTrue(receipt_parent.is_symlink())
        self.assertFalse(repository_receipt.exists())
        self.assertFalse((moved_parent / receipt.name).exists())
        self.assertEqual(
            runtime._diff_fingerprint(self.repo.root), initial_fingerprint
        )
        self.assertEqual(self.count.read_text(encoding="utf-8"), "1")

    def test_post_publication_diff_drift_removes_reserved_receipt(self) -> None:
        with (
            mock.patch.dict(os.environ, {"COUNT_PATH": str(self.count)}, clear=False),
            mock.patch.object(
                runtime,
                "_diff_fingerprint",
                side_effect=["initial", "initial", "changed-after-publication"],
            ),
        ):
            result = runtime.run_benchmark_phase(
                self.args("baseline", self.baseline)
            )

        self.assertEqual(result["status"], "failed")
        self.assertTrue(
            any("receipt publication" in error for error in result["proof_errors"]),
            result,
        )
        self.assertFalse(self.baseline.exists())
        self.assertEqual(self.count.read_text(encoding="utf-8"), "1")

    def test_missing_descriptor_relative_operations_fail_before_execution(self) -> None:
        with mock.patch.object(runtime.os, "supports_dir_fd", set()):
            with self.assertRaisesRegex(ValueError, "parent binding is unavailable"):
                runtime.run_benchmark_phase(
                    self.args("baseline", self.baseline)
                )

        self.assertFalse(self.baseline.exists())
        self.assertEqual(self.count.read_text(encoding="utf-8"), "0")

    @unittest.skipIf(os.name == "nt", "POSIX hard-link canary")
    def test_reserved_receipt_rejects_hard_links_before_write(self) -> None:
        exclude = self.repo.root / ".git" / "info" / "exclude"
        exclude.write_text(
            exclude.read_text(encoding="utf-8") + "\nignored-receipts/\n",
            encoding="utf-8",
        )
        linked = self.repo.root / "ignored-receipts" / "receipt.json"
        initial_fingerprint = runtime._diff_fingerprint(self.repo.root)
        environment = {
            **os.environ,
            "COUNT_PATH": str(self.count),
            "HARDLINK_RECEIPT_SOURCE": str(self.baseline),
            "HARDLINK_RECEIPT_TARGET": str(linked),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                str(SCRIPT),
                "benchmark",
                "baseline",
                "record-selection",
                "--repo",
                str(self.repo.root),
                "--profile-sha256",
                self.profile_sha256,
                "--receipt",
                str(self.baseline),
                "--format",
                "json",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )

        self.assertEqual(completed.returncode, 1, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertTrue(
            any("reservation changed" in error for error in payload["proof_errors"]),
            payload,
        )
        self.assertFalse(self.baseline.exists())
        self.assertEqual(linked.read_bytes(), b"")
        self.assertEqual(
            runtime._diff_fingerprint(self.repo.root), initial_fingerprint
        )
        self.assertEqual(self.count.read_text(encoding="utf-8"), "1")

    def test_changed_workload_is_rejected_after_exactly_one_final_run(self) -> None:
        with mock.patch.dict(os.environ, {"COUNT_PATH": str(self.count)}, clear=False):
            self.assertEqual(
                runtime.run_benchmark_phase(self.args("baseline", self.baseline))[
                    "status"
                ],
                "passed",
            )
            script = self.repo.root / "scripts" / "benchmark.py"
            script.write_text(
                script.read_text(encoding="utf-8") + "\n# workload drift\n",
                encoding="utf-8",
            )
            final = runtime.run_benchmark_phase(
                self.args("final", self.final, self.baseline)
            )

        self.assertEqual(final["status"], "failed")
        self.assertEqual(self.count.read_text(encoding="utf-8"), "2")
        self.assertFalse(self.final.exists())
        self.assertTrue(
            any("workload changed" in item for item in final["proof_errors"]),
            final,
        )


class ProvenanceTests(unittest.TestCase):
    def test_provenance_cli_reports_current_for_the_loaded_marker(self) -> None:
        receipt = runtime._provenance_receipt(None)
        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                str(SCRIPT),
                "provenance",
                "--loaded-provenance",
                f"{receipt['release']}:{receipt['marker_sha256']}",
                "--format",
                "json",
                "--require-current",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertEqual(json.loads(completed.stdout)["state"], "current")

    def test_current_stale_unknown_hash_binding_and_symlink_rejection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vnext-provenance-") as raw:
            root = Path(raw)
            (root / "scripts").mkdir()
            skill = root / "SKILL.md"
            skill.write_text(
                "---\nname: endurant-harness\n---\n"
                f"<!--endurant-provenance:v5:{'0' * 64}-->\n",
                encoding="utf-8",
            )
            implementation = root / "scripts" / "endurant.py"
            implementation.write_text("VALUE = 1\n", encoding="utf-8")
            _, _, digest = runtime._canonical_package_sha256(root)
            skill.write_text(
                skill.read_text(encoding="utf-8").replace("0" * 64, digest),
                encoding="utf-8",
            )

            current = runtime._provenance_receipt(
                f"v5:{digest}", package_root=root
            )
            prefixed = runtime._provenance_receipt(
                f"endurant-provenance:v5:{digest}", package_root=root
            )
            wrapped = runtime._provenance_receipt(
                f"<!--endurant-provenance:v5:{digest}-->", package_root=root
            )
            stale = runtime._provenance_receipt(
                f"v5:{'f' * 64}", package_root=root
            )
            different_release = runtime._provenance_receipt(
                f"v4:{digest}", package_root=root
            )
            missing = runtime._provenance_receipt(None, package_root=root)
            self.assertEqual(current["state"], "current")
            self.assertEqual(prefixed["state"], "current")
            self.assertEqual(wrapped["state"], "current")
            self.assertTrue(current["package_integrity"])
            self.assertEqual(stale["state"], "stale")
            self.assertEqual(different_release["state"], "stale")
            self.assertEqual(different_release["loaded_release"], "v4")
            self.assertEqual(missing["state"], "unknown")

            implementation.write_text("VALUE = 2\n", encoding="utf-8")
            tampered = runtime._provenance_receipt(
                f"v5:{digest}", package_root=root
            )
            self.assertEqual(tampered["state"], "unknown")
            self.assertFalse(tampered["package_integrity"])

            (root / "linked.py").symlink_to(implementation)
            with self.assertRaisesRegex(ValueError, "symlinks"):
                runtime._canonical_package_sha256(root)

    def test_probe_parser_accepts_loaded_provenance_without_affecting_other_commands(self) -> None:
        parser = runtime.build_parser()
        loaded = f"v5:{'a' * 64}"
        probe = parser.parse_args(["probe", "--loaded-provenance", loaded])
        preflight = parser.parse_args(
            [
                "preflight",
                "--bundle",
                "local-ci",
                "--require",
                "focused",
                "--profile-sha256",
                "b" * 64,
            ]
        )
        benchmark = parser.parse_args(
            [
                "benchmark",
                "final",
                "record-selection",
                "--profile-sha256",
                "c" * 64,
                "--baseline",
                "/tmp/baseline.json",
                "--receipt",
                "/tmp/final.json",
            ]
        )
        template = parser.parse_args(["template"])
        self.assertEqual(probe.loaded_provenance, loaded)
        self.assertEqual(preflight.require, ["focused"])
        self.assertEqual(benchmark.benchmark_phase, "final")
        self.assertEqual(template.command, "template")


if __name__ == "__main__":
    unittest.main()
