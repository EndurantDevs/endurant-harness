from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "subjects" / "combined-candidate" / "endurant-harness" / "scripts" / "endurant.py"


class CombinedRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="combined-runner-")
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=self.repo, check=True)
        (self.repo / "source.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "source.txt"], cwd=self.repo, check=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fingerprint(self) -> str:
        completed = subprocess.run(
            [sys.executable, "-S", str(SCRIPT), "fingerprint", "--repo", str(self.repo)],
            cwd=self.repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return completed.stdout.strip()

    def execute(
        self,
        plan: dict[str, object],
        expected_exit: int,
        extra_args: list[str] | None = None,
        environment: dict[str, str] | None = None,
    ) -> dict[str, object]:
        plan_path = self.base / f"plan-{time.time_ns()}.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        argv = [
            sys.executable,
            "-S",
            str(SCRIPT),
            "run",
            str(plan_path),
            "--repo",
            str(self.repo),
            "--format",
            "json",
            "--log-dir",
            str(self.base / f"logs-{time.time_ns()}"),
        ]
        argv.extend(extra_args or [])
        completed = subprocess.run(
            argv,
            cwd=self.repo,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, expected_exit, completed.stderr + completed.stdout)
        return json.loads(completed.stdout)

    def test_all_optional_fields_work_together(self) -> None:
        result = self.execute(
            {
                "name": "combined-pass",
                "proof_deadline_seconds": 5,
                "expected_diff_fingerprint": self.fingerprint(),
                "require_behavior_evidence": True,
                "stages": [
                    {
                        "name": "proof",
                        "commands": [
                            {
                                "id": "tests",
                                "argv": [sys.executable, "-S", "-c", "print('Ran 3 tests')"],
                                "evidence": "behavior",
                                "must_match": "Ran [1-9][0-9]* tests",
                                "must_not_match": "Ran 0 tests",
                            }
                        ],
                    }
                ],
            },
            expected_exit=0,
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["initial_diff_fingerprint"], result["final_diff_fingerprint"])

    def test_zero_test_output_is_rejected(self) -> None:
        result = self.execute(
            {
                "name": "zero-test",
                "stages": [
                    {
                        "name": "proof",
                        "commands": [
                            {
                                "id": "tests",
                                "argv": [sys.executable, "-S", "-c", "print('Ran 0 tests')"],
                                "evidence": "behavior",
                                "must_not_match": "Ran 0 tests",
                            }
                        ],
                    }
                ],
            },
            expected_exit=1,
        )
        self.assertEqual(result["stages"][0]["commands"][0]["status"], "failed")

    def test_deadline_kills_child_and_runs_cleanup(self) -> None:
        pid_path = self.base / "child.pid"
        cleanup_path = self.base / "cleanup.marker"
        worker = (
            "import pathlib,subprocess,time; "
            "p=subprocess.Popen(['/bin/sleep','30']); "
            f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid)); "
            "time.sleep(30)"
        )
        result = self.execute(
            {
                "name": "deadline",
                "proof_deadline_seconds": 1,
                "stages": [
                    {
                        "name": "hang",
                        "commands": [
                            {
                                "id": "hang",
                                "argv": [sys.executable, "-S", "-c", worker],
                                "timeout": 30,
                                "evidence": "diagnostic",
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
                                    f"from pathlib import Path; Path({str(cleanup_path)!r}).write_text('done')",
                                ],
                                "evidence": "cleanup",
                            }
                        ],
                    },
                ],
            },
            expected_exit=1,
        )
        self.assertIn("plan proof deadline exhausted", result["proof_errors"])
        self.assertEqual(cleanup_path.read_text(encoding="utf-8"), "done")
        child_pid = int(pid_path.read_text(encoding="utf-8"))
        time.sleep(0.2)
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            child_alive = False
        else:
            child_alive = True
        if child_alive:
            os.kill(child_pid, signal.SIGKILL)
        self.assertFalse(child_alive)

    def test_fingerprint_os_error_still_runs_cleanup(self) -> None:
        work_path = self.base / "fingerprint-work.marker"
        cleanup_path = self.base / "fingerprint-cleanup.marker"
        result = self.execute(
            {
                "name": "fingerprint-io-error",
                "expected_diff_fingerprint": "0" * 64,
                "stages": [
                    {
                        "name": "work",
                        "commands": [
                            {
                                "id": "work",
                                "argv": [
                                    sys.executable,
                                    "-S",
                                    "-c",
                                    f"from pathlib import Path; Path({str(work_path)!r}).touch()",
                                ],
                                "evidence": "diagnostic",
                            }
                        ],
                    },
                    {
                        "name": "cleanup",
                        "run_if": "always",
                        "commands": [
                            {
                                "id": "fingerprint-cleanup",
                                "argv": [
                                    sys.executable,
                                    "-S",
                                    "-c",
                                    f"from pathlib import Path; Path({str(cleanup_path)!r}).write_text('done')",
                                ],
                                "evidence": "cleanup",
                            }
                        ],
                    },
                ],
            },
            expected_exit=1,
            environment={**os.environ, "PATH": str(self.base / "empty-path")},
        )
        self.assertIn("No such file or directory", result["proof_error"])
        self.assertFalse(work_path.exists())
        self.assertEqual(cleanup_path.read_text(encoding="utf-8"), "done")

    def test_parallel_queue_uses_absolute_plan_deadline(self) -> None:
        command = {
            "argv": [sys.executable, "-S", "-c", "import time; time.sleep(0.75)"],
            "timeout": 5,
            "evidence": "diagnostic",
        }
        started = time.monotonic()
        result = self.execute(
            {
                "name": "parallel-absolute-deadline",
                "proof_deadline_seconds": 1,
                "stages": [
                    {
                        "name": "queued",
                        "parallel": True,
                        "commands": [
                            {"id": "first", **command},
                            {"id": "second", **command},
                        ],
                    }
                ],
            },
            expected_exit=1,
            extra_args=["--max-workers", "1"],
        )
        duration = time.monotonic() - started
        commands = result["stages"][0]["commands"]
        self.assertEqual(commands[0]["status"], "passed")
        self.assertEqual(commands[1]["status"], "timeout")
        self.assertIn("plan proof deadline exhausted", result["proof_errors"])
        self.assertLess(duration, 2.5)

    def test_pathological_output_regex_is_deadline_bounded(self) -> None:
        cleanup_path = self.base / "regex-cleanup.marker"
        started = time.monotonic()
        result = self.execute(
            {
                "name": "regex-deadline",
                "proof_deadline_seconds": 1,
                "stages": [
                    {
                        "name": "assert",
                        "commands": [
                            {
                                "id": "regex",
                                "argv": [
                                    sys.executable,
                                    "-S",
                                    "-c",
                                    "import sys; sys.stdout.write('a' * 50000 + '!')",
                                ],
                                "timeout": 10,
                                "evidence": "diagnostic",
                                "must_not_match": "(a+)+$",
                            }
                        ],
                    },
                    {
                        "name": "cleanup",
                        "run_if": "always",
                        "commands": [
                            {
                                "id": "regex-cleanup",
                                "argv": [
                                    sys.executable,
                                    "-S",
                                    "-c",
                                    f"from pathlib import Path; Path({str(cleanup_path)!r}).write_text('done')",
                                ],
                                "evidence": "cleanup",
                            }
                        ],
                    },
                ],
            },
            expected_exit=1,
        )
        duration = time.monotonic() - started
        command = result["stages"][0]["commands"][0]
        self.assertEqual(command["status"], "timeout")
        self.assertGreater(command["duration_seconds"], 0.5)
        self.assertLess(duration, 3.0)
        self.assertEqual(cleanup_path.read_text(encoding="utf-8"), "done")

    def test_nul_output_regex_does_not_abort_runner(self) -> None:
        result = self.execute(
            {
                "name": "nul-regex",
                "stages": [
                    {
                        "name": "assert",
                        "commands": [
                            {
                                "id": "nul-regex",
                                "argv": [sys.executable, "-S", "-c", "print('clean')"],
                                "evidence": "diagnostic",
                                "must_not_match": "\u0000",
                            }
                        ],
                    }
                ],
            },
            expected_exit=0,
        )
        self.assertEqual(result["status"], "passed")

    def assert_signal_kills_child_and_runs_cleanup(
        self, signal_number: signal.Signals, label: str
    ) -> None:
        child_pid_path = self.base / f"{label}-child.pid"
        failure_path = self.base / f"{label}-failure.marker"
        cleanup_path = self.base / f"{label}-cleanup.marker"
        worker = (
            "import pathlib,subprocess,time; "
            "p=subprocess.Popen(['/bin/sleep','30']); "
            f"pathlib.Path({str(child_pid_path)!r}).write_text(str(p.pid)); "
            "time.sleep(30)"
        )
        plan_path = self.base / f"{label}-plan.json"
        plan_path.write_text(
            json.dumps(
                {
                    "name": f"{label}-cleanup",
                    "stages": [
                        {
                            "name": "work",
                            "commands": [
                                {
                                    "id": "work",
                                    "argv": [sys.executable, "-S", "-c", worker],
                                    "timeout": 60,
                                    "evidence": "diagnostic",
                                }
                            ],
                        },
                        {
                            "name": "failure-diagnostics",
                            "run_if": "failure",
                            "commands": [
                                {
                                    "id": f"{label}-failure",
                                    "argv": [
                                        sys.executable,
                                        "-S",
                                        "-c",
                                        f"from pathlib import Path; Path({str(failure_path)!r}).write_text('bad')",
                                    ],
                                    "evidence": "diagnostic",
                                }
                            ],
                        },
                        {
                            "name": "cleanup",
                            "run_if": "always",
                            "commands": [
                                {
                                    "id": f"{label}-cleanup",
                                    "argv": [
                                        sys.executable,
                                        "-S",
                                        "-c",
                                        f"from pathlib import Path; Path({str(cleanup_path)!r}).write_text('done')",
                                    ],
                                    "evidence": "cleanup",
                                }
                            ],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        proc = subprocess.Popen(
            [
                sys.executable,
                "-S",
                str(SCRIPT),
                "run",
                str(plan_path),
                "--repo",
                str(self.repo),
                "--format",
                "json",
                "--log-dir",
                str(self.base / f"{label}-logs"),
            ],
            cwd=self.repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        cutoff = time.monotonic() + 5
        while not child_pid_path.exists() and time.monotonic() < cutoff:
            time.sleep(0.02)
        self.assertTrue(child_pid_path.exists(), "worker did not start")
        os.kill(proc.pid, signal_number)
        stdout, stderr = proc.communicate(timeout=8)
        self.assertEqual(proc.returncode, 2, stderr + stdout)
        result = json.loads(stdout)
        self.assertTrue(result["interrupted"])
        self.assertFalse(failure_path.exists())
        self.assertEqual(cleanup_path.read_text(encoding="utf-8"), "done")
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        time.sleep(0.2)
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            child_alive = False
        else:
            child_alive = True
        if child_alive:
            os.kill(child_pid, signal.SIGKILL)
        self.assertFalse(child_alive)

    def test_sigint_kills_child_and_runs_only_cleanup(self) -> None:
        self.assert_signal_kills_child_and_runs_cleanup(signal.SIGINT, "sigint")

    def test_sigterm_kills_child_and_runs_only_cleanup(self) -> None:
        self.assert_signal_kills_child_and_runs_cleanup(signal.SIGTERM, "sigterm")

    def test_stale_fingerprint_skips_success_but_runs_cleanup(self) -> None:
        expected = self.fingerprint()
        (self.repo / "source.txt").write_text("changed-after-fingerprint\n", encoding="utf-8")
        work_marker = self.base / "work.marker"
        cleanup_marker = self.base / "cleanup.marker"
        result = self.execute(
            {
                "name": "stale",
                "expected_diff_fingerprint": expected,
                "stages": [
                    {
                        "name": "success-only",
                        "commands": [
                            {
                                "id": "work",
                                "argv": [
                                    sys.executable,
                                    "-S",
                                    "-c",
                                    f"from pathlib import Path; Path({str(work_marker)!r}).write_text('bad')",
                                ],
                                "evidence": "diagnostic",
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
                                    f"from pathlib import Path; Path({str(cleanup_marker)!r}).write_text('done')",
                                ],
                                "evidence": "cleanup",
                            }
                        ],
                    },
                ],
            },
            expected_exit=1,
        )
        self.assertIn("diff fingerprint changed before proof", result["proof_errors"])
        self.assertEqual(result["stages"][0]["status"], "skipped")
        self.assertFalse(work_marker.exists())
        self.assertEqual(cleanup_marker.read_text(encoding="utf-8"), "done")


if __name__ == "__main__":
    unittest.main()
