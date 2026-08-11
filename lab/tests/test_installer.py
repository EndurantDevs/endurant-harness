from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "install.sh"


class InstallerTests(unittest.TestCase):
    def test_both_hosts_update_and_duplicate_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            env = os.environ.copy()
            env.update({"HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"})
            env.pop("CODEX_HOME", None)
            command = [
                "sh",
                str(INSTALLER),
                "--source",
                str(ROOT),
                "--agent",
                "both",
            ]

            hostile = home / "hostile-cwd"
            (hostile / "endurant-harness" / "scripts").mkdir(parents=True)
            (hostile / "sh").write_text("not the installer\n")
            (hostile / "endurant-harness" / "SKILL.md").write_text(
                "---\nname: endurant-harness\n---\n"
            )
            (hostile / "endurant-harness" / "scripts" / "audit_skill.py").write_text(
                "raise SystemExit(0)\n"
            )
            piped_env = env | {
                "ENDURANT_REPOSITORY": "file:///definitely-missing-endurant-repo"
            }
            piped = subprocess.run(
                ["sh", "-s", "--", "--agent", "codex"],
                input=INSTALLER.read_text(),
                cwd=hostile,
                capture_output=True,
                text=True,
                env=piped_env,
            )
            self.assertNotEqual(piped.returncode, 0)
            self.assertFalse(
                (home / ".agents" / "skills" / "endurant-harness").exists()
            )

            conflict = home / ".claude" / ".endurant-harness.previous"
            conflict.mkdir(parents=True)
            (conflict / "SKILL.md").write_text("---\nname: unrelated\n---\n")
            blocked = subprocess.run(command, capture_output=True, text=True, env=env)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("unrelated backup", blocked.stderr)
            self.assertFalse(
                (home / ".agents" / "skills" / "endurant-harness").exists()
            )
            shutil.rmtree(conflict)

            first = subprocess.run(command, capture_output=True, text=True, env=env)
            self.assertEqual(first.returncode, 0, first.stderr)
            codex = home / ".agents" / "skills" / "endurant-harness"
            claude = home / ".claude" / "skills" / "endurant-harness"
            self.assertTrue((codex / "SKILL.md").is_file())
            self.assertEqual(
                (codex / "SKILL.md").read_bytes(),
                (claude / "SKILL.md").read_bytes(),
            )

            update = subprocess.run(
                command[:-1] + ["codex"], capture_output=True, text=True, env=env
            )
            self.assertEqual(update.returncode, 0, update.stderr)
            self.assertTrue(
                (home / ".agents" / ".endurant-harness.previous" / "SKILL.md").is_file()
            )
            backup = home / ".agents" / ".endurant-harness.previous"
            backup_skill = (backup / "SKILL.md").read_bytes()
            fake_bin = home / "fake-bin"
            fake_bin.mkdir()
            fake_cp = fake_bin / "cp"
            fake_cp.write_text("#!/bin/sh\nexit 1\n")
            fake_cp.chmod(0o755)
            failing_env = env | {"PATH": f"{fake_bin}{os.pathsep}{env['PATH']}"}
            failed_copy = subprocess.run(
                command[:-1] + ["codex"],
                capture_output=True,
                text=True,
                env=failing_env,
            )
            self.assertNotEqual(failed_copy.returncode, 0)
            self.assertEqual((backup / "SKILL.md").read_bytes(), backup_skill)
            self.assertTrue((codex / "SKILL.md").is_file())

            legacy = home / ".codex" / "skills" / "endurant-harness"
            legacy.parent.mkdir(parents=True)
            shutil.copytree(codex, legacy)
            duplicate = subprocess.run(
                command[:-1] + ["codex"], capture_output=True, text=True, env=env
            )
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("both Codex skill locations exist", duplicate.stderr)
            self.assertTrue((codex / "SKILL.md").is_file())
            self.assertTrue((legacy / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
