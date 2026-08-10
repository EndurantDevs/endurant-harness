from __future__ import annotations

import json
import os
import runpy
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "subjects"
    / "combined-candidate"
    / "endurant-harness"
    / "scripts"
    / "endurant.py"
)


class ProbeInventoryTests(unittest.TestCase):
    def test_git_inventory_excludes_ignored_project_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="probe-git-ignore-") as value:
            repo = Path(value)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            (repo / ".gitignore").write_text("generated/\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text("[project]\nname='root'\n", encoding="utf-8")
            tracked_ignored = repo / "tracked-generated"
            tracked_ignored.mkdir()
            (tracked_ignored / "Cargo.toml").write_text(
                "[package]\nname='tracked'\n", encoding="utf-8"
            )
            deleted = repo / "deleted" / "Cargo.toml"
            deleted.parent.mkdir()
            deleted.write_text("[package]\nname='deleted'\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "tracked-generated/Cargo.toml", "deleted/Cargo.toml"],
                cwd=repo,
                check=True,
            )
            deleted.unlink()
            (repo / ".gitignore").write_text(
                "generated/\ntracked-generated/\n", encoding="utf-8"
            )
            generated = repo / "generated" / "nested"
            generated.mkdir(parents=True)
            (generated / "Cargo.toml").write_text("[package]\nname='ignored'\n", encoding="utf-8")
            (generated / "pyproject.toml").write_text(
                "[project]\nname='ignored'\n", encoding="utf-8"
            )
            visible = repo / "packages" / "visible"
            visible.mkdir(parents=True)
            (visible / "Cargo.toml").write_text(
                "[package]\nname='visible'\n", encoding="utf-8"
            )
            subprocess.run(
                ["git", "add", ".gitignore", "pyproject.toml"], cwd=repo, check=True
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(SCRIPT),
                    "probe",
                    "--repo",
                    str(repo),
                    "--format",
                    "json",
                ],
                cwd=repo,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            payload = json.loads(completed.stdout)

            self.assertIn("pyproject.toml", payload["project_files"])
            self.assertIn("packages/visible/Cargo.toml", payload["project_files"])
            self.assertIn("tracked-generated/Cargo.toml", payload["project_files"])
            self.assertNotIn("deleted/Cargo.toml", payload["project_files"])
            self.assertFalse(
                any(path.startswith("generated/") for path in payload["project_files"])
            )
            self.assertNotIn("generated/", payload["top_level"])
            self.assertIn("packages/", payload["top_level"])

    def test_git_inventory_deadline_interrupts_silent_process(self) -> None:
        namespace = runpy.run_path(str(SCRIPT))
        scan = namespace["_scan_git_repository"]
        real_popen = subprocess.Popen
        children = []

        def delayed_git(_argv, **kwargs):
            child = real_popen(
                [sys.executable, "-c", "import time; time.sleep(3)"], **kwargs
            )
            children.append(child)
            return child

        with tempfile.TemporaryDirectory(prefix="probe-git-timeout-") as value:
            started = time.monotonic()
            with mock.patch.object(subprocess, "Popen", side_effect=delayed_git):
                result = scan(Path(value), max_depth=4, max_items=40)
            elapsed = time.monotonic() - started

        self.assertIsNotNone(result)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["budget_exhausted"])
        self.assertIsNotNone(children[0].returncode)
        self.assertLess(elapsed, 2.5)

    @unittest.skipIf(os.name == "nt", "POSIX signal and process liveness check")
    def test_probe_interrupt_reaps_silent_git_process(self) -> None:
        with tempfile.TemporaryDirectory(prefix="probe-git-interrupt-") as value:
            root = Path(value)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            child_pid_path = root / "git-child.pid"
            fake_git = fake_bin / "git"
            fake_git.write_text(
                """#!/usr/bin/env python3
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
if args[:2] == ["rev-parse", "--show-toplevel"]:
    print(os.getcwd())
elif args and args[0] == "ls-files":
    Path(os.environ["FAKE_GIT_CHILD_PID"]).write_text(str(os.getpid()))
    time.sleep(30)
""",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            env = {
                **os.environ,
                "FAKE_GIT_CHILD_PID": str(child_pid_path),
                "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            probe = subprocess.Popen(
                [
                    sys.executable,
                    "-S",
                    str(SCRIPT),
                    "probe",
                    "--repo",
                    str(root),
                    "--format",
                    "json",
                ],
                cwd=root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_pid = None
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not child_pid_path.exists():
                    time.sleep(0.02)
                self.assertTrue(child_pid_path.exists(), "fake Git inventory did not start")
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                probe.send_signal(signal.SIGINT)
                _stdout, stderr = probe.communicate(timeout=4)
                self.assertEqual(probe.returncode, 2, stderr)

                reaped = False
                reap_deadline = time.monotonic() + 2
                while time.monotonic() < reap_deadline:
                    try:
                        os.kill(child_pid, 0)
                    except ProcessLookupError:
                        reaped = True
                        break
                    time.sleep(0.02)
                self.assertTrue(reaped, "interrupted probe left the Git child alive")
            finally:
                if probe.poll() is None:
                    probe.kill()
                    probe.wait(timeout=2)
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()
