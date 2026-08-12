from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "lab"
sys.path.insert(0, str(LAB))

SCRIPT = LAB / "run_adaptive_replan.py"
SPEC = importlib.util.spec_from_file_location("run_adaptive_replan", SCRIPT)
adaptive = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(adaptive)

from eval_lib import canonical_sha256, copy_subject, read_json, tree_manifest, write_json_atomic  # noqa: E402
from grade_run import recovery_command_checks  # noqa: E402


class AdaptiveReceiptTests(unittest.TestCase):
    def seal(self, value: dict[str, object]) -> None:
        value.pop("receipt_sha256", None)
        value["receipt_sha256"] = canonical_sha256(value)

    def make_receipt(self, root: Path) -> dict[str, object]:
        case = "software-settings"
        configs = adaptive.CASES[case]["candidates"]
        seeded_fixture = root / "seeded-fixture"
        seeded_fixture.mkdir()
        frozen = adaptive.frozen_contract(case, adaptive.CASES[case], ROOT / "endurant-harness")
        frozen["seeded_fixture_sha256"] = adaptive.tree_sha256(seeded_fixture)
        frozen["candidate_runs"] = [
            {
                "context_sha256": adaptive.bytes_sha256(
                    f"context {config['id']}\n".encode()
                ),
                "fixture_sha256": adaptive.tree_sha256(seeded_fixture),
                "id": config["id"],
                "model": config["model"],
                "prompt_sha256": adaptive.bytes_sha256(
                    f"frozen prompt {config['id']}".encode()
                ),
                "reasoning_effort": config["reasoning_effort"],
                "run_id": f"{root.name}-candidate-{index}",
                "subject": f"adaptive-{config['id']}",
            }
            for index, config in enumerate(configs)
        ]
        frozen["contract_sha256"] = canonical_sha256(
            {key: item for key, item in frozen.items() if key != "contract_sha256"}
        )
        write_json_atomic(root / "frozen.json", frozen)

        candidates: list[dict[str, object]] = []
        for index, config in enumerate(configs):
            contract = frozen["candidate_runs"][index]
            capture = ROOT / "artifacts" / "runs" / contract["run_id"]
            capture.mkdir()
            self.addCleanup(shutil.rmtree, capture, True)
            workspace_id = f"{root.name}-workspace-{index}"
            workspace = ROOT / "artifacts" / "workspaces" / workspace_id
            prompt = f"frozen prompt {config['id']}"
            metadata = {
                "agent_started_monotonic_ns": 10 + index,
                "fixture": adaptive.CASES[case]["fixture"],
                "fixture_source": str(seeded_fixture),
                "fixture_tree_manifest": tree_manifest(seeded_fixture),
                "model": config["model"],
                "prompt": prompt,
                "prompt_context_sha256": contract["context_sha256"],
                "reasoning_effort": config["reasoning_effort"],
                "run_id": contract["run_id"],
                "subject": contract["subject"],
                "subject_tree_manifest": tree_manifest(ROOT / "endurant-harness"),
                "workspace": str(workspace),
                "workspace_id": workspace_id,
            }
            write_json_atomic(capture / "metadata.json", metadata)
            write_json_atomic(capture / "summary.json", {"passed": True})
            write_json_atomic(
                capture / "grade.json",
                {
                    "changed_paths": ["src/settings.py", "tests/test_settings.py"],
                    "passed": True,
                },
            )
            write_json_atomic(
                capture / "agent-metrics.json",
                {
                    "agent_ended_monotonic_ns": 40 + index,
                    "agent_started_monotonic_ns": 10 + index,
                    "duration_seconds": 10.0 + index,
                },
            )
            (capture / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
            for name in set(adaptive.CAPTURE_FILES) - {
                "agent-metrics.json",
                "grade.json",
                "metadata.json",
                "prompt.txt",
                "summary.json",
            }:
                (capture / name).write_text(f"{name}\n", encoding="utf-8")
            patch = root / f"candidate-{index}.patch"
            patch.write_text(
                "diff --git a/src/settings.py b/src/settings.py\n"
                "--- a/src/settings.py\n+++ b/src/settings.py\n"
                "@@ -1 +1 @@\n-old\n+new\n"
                "diff --git a/tests/test_settings.py b/tests/test_settings.py\n"
                "--- a/tests/test_settings.py\n+++ b/tests/test_settings.py\n"
                "@@ -1 +1 @@\n-old\n+new\n",
                encoding="utf-8",
            )
            candidates.append(
                {
                    "capture": str(capture.relative_to(ROOT)),
                    "capture_hashes": {
                        name: adaptive.sha256_file(capture / name)
                        for name in adaptive.CAPTURE_FILES
                    },
                    "changed_lines": 4,
                    "changed_paths": ["src/settings.py", "tests/test_settings.py"],
                    "duration_seconds": 10.0 + index,
                    "ended_monotonic_ns": 40 + index,
                    "id": config["id"],
                    "model": config["model"],
                    "passed": True,
                    "patch_path": patch.name,
                    "patch_sha256": adaptive.bytes_sha256(patch.read_bytes()),
                    "reasoning_effort": config["reasoning_effort"],
                    "returncode": 0,
                    "run_id": contract["run_id"],
                    "started_monotonic_ns": 10 + index,
                    "timed_out": False,
                    "untracked_paths": [],
                    "workspace": str(workspace.relative_to(ROOT)),
                    "wrapper_ended_monotonic_ns": 40 + index,
                    "wrapper_started_monotonic_ns": 10 + index,
                }
            )

        seed_capture = root / "seed"
        seed_capture.mkdir()
        stdout = seed_capture / "seeded-oracle.stdout"
        stderr = seed_capture / "seeded-oracle.stderr"
        stdout.write_text("FAIL\n", encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        seeded = {
            "capture": str(seed_capture.relative_to(ROOT)),
            "oracle": frozen["oracle"],
            "returncode": 1,
            "stderr_path": str(stderr.relative_to(ROOT)),
            "stderr_sha256": adaptive.sha256_file(stderr),
            "stdout_path": str(stdout.relative_to(ROOT)),
            "stdout_sha256": adaptive.sha256_file(stdout),
        }
        write_json_atomic(seed_capture / "seeded-failure.json", seeded)

        owner_capture = root / "owner"
        owner_capture.mkdir()
        checks: dict[str, object] = {"passed": True}
        for name in ("hidden_grade", "local_ci", "oracle"):
            stdout_path = owner_capture / f"owner-{name}.stdout"
            stderr_path = owner_capture / f"owner-{name}.stderr"
            stdout_path.write_text("PASS\n", encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")
            checks[name] = {
                "returncode": 0,
                "stderr_sha256": adaptive.sha256_file(stderr_path),
                "stdout_sha256": adaptive.sha256_file(stdout_path),
            }
        owner_patch = owner_capture / "owner.patch"
        owner_patch.write_bytes((root / "candidate-0.patch").read_bytes())
        owner = {
            "capture": str(owner_capture.relative_to(ROOT)),
            "checks": checks,
            "integration": {
                "kind": "patch",
                "patch_sha256": candidates[0]["patch_sha256"],
            },
            "owner_patch_sha256": adaptive.sha256_file(owner_patch),
            "passed": True,
            "selected": configs[0]["id"],
            "started_after_candidates": True,
            "started_monotonic_ns": 50,
            "workspace": "artifacts/workspaces/owner",
        }
        write_json_atomic(owner_capture / "owner.json", owner)
        receipt: dict[str, object] = {
            "case": case,
            "candidates": candidates,
            "evidence_tier": "task-evaluated",
            "frozen": frozen,
            "frozen_file_sha256": adaptive.sha256_file(root / "frozen.json"),
            "owner": owner,
            "parallel_overlap": True,
            "passed": True,
            "schema_version": 1,
            "seeded_failure": seeded,
            "selected_candidate": configs[0]["id"],
        }
        self.seal(receipt)
        return receipt

    def test_receipt_is_bound_to_raw_evidence(self) -> None:
        (ROOT / "artifacts" / "runs").mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="adaptive-receipt-", dir=ROOT / "artifacts" / "runs"
        ) as raw:
            root = Path(raw)
            value = self.make_receipt(root)
            self.assertEqual(adaptive.validate_receipt(value, root), [])
            capture = ROOT / value["candidates"][0]["capture"]
            (capture / "codex-observed.jsonl").write_text("tampered\n", encoding="utf-8")
            self.assertTrue(adaptive.validate_receipt(value, root))

    def test_receipt_binds_candidate_identity_and_lean_metrics_to_raw_capture(self) -> None:
        (ROOT / "artifacts" / "runs").mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="adaptive-bindings-", dir=ROOT / "artifacts" / "runs"
        ) as raw:
            root = Path(raw)
            value = self.make_receipt(root)
            candidate = value["candidates"][0]
            candidate["changed_paths"] = ["fabricated/a.py", "fabricated/b.py"]
            metadata_path = ROOT / candidate["capture"] / "metadata.json"
            metadata = read_json(metadata_path)
            metadata["run_id"] = "fabricated-run"
            write_json_atomic(metadata_path, metadata)
            candidate["capture_hashes"]["metadata.json"] = adaptive.sha256_file(metadata_path)
            self.seal(value)
            errors = adaptive.validate_receipt(value, root)
            self.assertTrue(any("run" in error for error in errors), errors)
            self.assertTrue(any("changed paths" in error for error in errors), errors)

    def test_receipt_uses_actual_agent_intervals_for_overlap(self) -> None:
        (ROOT / "artifacts" / "runs").mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="adaptive-overlap-", dir=ROOT / "artifacts" / "runs"
        ) as raw:
            root = Path(raw)
            value = self.make_receipt(root)
            candidate = value["candidates"][1]
            metrics_path = ROOT / candidate["capture"] / "agent-metrics.json"
            metrics = read_json(metrics_path)
            metrics["agent_started_monotonic_ns"] = 100
            metrics["agent_ended_monotonic_ns"] = 120
            candidate["started_monotonic_ns"] = 100
            candidate["ended_monotonic_ns"] = 120
            write_json_atomic(metrics_path, metrics)
            candidate["capture_hashes"]["agent-metrics.json"] = adaptive.sha256_file(
                metrics_path
            )
            self.seal(value)
            errors = adaptive.validate_receipt(value, root)
            self.assertIn("candidate execution did not overlap", errors)

    def test_receipt_requires_artifacts_and_lean_complete_candidates(self) -> None:
        self.assertEqual(adaptive.validate_receipt({}), ["artifact root is required"])
        candidates = [
            {"passed": True, "changed_paths": [], "changed_lines": None, "duration_seconds": 1},
            {"passed": True, "changed_paths": ["x"], "changed_lines": 1, "duration_seconds": 2, "workspace": "w", "id": "complete"},
        ]
        self.assertEqual(adaptive.select_candidate(candidates)["id"], "complete")

    def test_failed_campaign_does_not_require_selected_owner_process_evidence(self) -> None:
        (ROOT / "artifacts" / "runs").mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="adaptive-no-selection-", dir=ROOT / "artifacts" / "runs"
        ) as raw:
            root = Path(raw)
            value = self.make_receipt(root)
            for candidate in value["candidates"]:
                candidate["passed"] = False
                summary_path = ROOT / candidate["capture"] / "summary.json"
                write_json_atomic(summary_path, {"passed": False})
                candidate["capture_hashes"]["summary.json"] = adaptive.sha256_file(
                    summary_path
                )
            owner_capture = root / "owner"
            owner = {
                "capture": str(owner_capture.relative_to(ROOT)),
                "integration": "no-op",
                "passed": False,
                "started_after_candidates": True,
                "started_monotonic_ns": 50,
                "workspace": "artifacts/workspaces/owner",
            }
            write_json_atomic(owner_capture / "owner.json", owner)
            value["owner"] = owner
            value["passed"] = False
            value["selected_candidate"] = None
            self.seal(value)
            self.assertEqual(adaptive.validate_receipt(value, root), [])


class AdaptiveFixtureTests(unittest.TestCase):
    def copy_fixture(self, root: Path, name: str) -> Path:
        target = root / name
        shutil.copytree(ROOT / "fixtures" / name / "template", target)
        return target

    def recovery_evidence(
        self,
    ) -> tuple[str, dict[str, object], dict[str, object], str]:
        initial = read_json(
            ROOT
            / "fixtures"
            / "authorized-recovery"
            / "template"
            / "external"
            / "state.json"
        )
        expected = canonical_sha256(initial)
        state = {
            **initial,
            "checkpoint": 100,
            "last_action": "resume",
            "progress": 100,
            "status": "complete",
        }
        action = {
            "action": "resume",
            "actor": "agent",
            "after_state_sha256": canonical_sha256(state),
            "before_state_sha256": expected,
            "expected_state_sha256": expected,
        }
        receipt = json.dumps(
            {
                "action": action,
                "passed": True,
                "state": state,
                "state_sha256": canonical_sha256(state),
            }
        )
        return expected, state, action, receipt

    def test_seeded_settings_attempt_fails_the_original_oracle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="adaptive-settings-") as raw:
            workspace = self.copy_fixture(Path(raw), "settings-override-correctness")
            subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
            subprocess.run(["git", "add", "."], cwd=workspace, check=True)
            patch = adaptive.FAILED_SETTINGS_PATCH.read_text(encoding="utf-8")
            applied = subprocess.run(
                ["git", "apply", "-"],
                cwd=workspace,
                input=patch,
                text=True,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            oracle = subprocess.run(
                [sys.executable, "scripts/verify.py", "focused"],
                cwd=workspace,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(oracle.returncode, 0)
            self.assertIn("FAIL", oracle.stderr + oracle.stdout)

    def test_recovery_requires_cas_and_rejects_restart_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="adaptive-recovery-") as raw:
            root = Path(raw)
            resumed = self.copy_fixture(root, "authorized-recovery")
            inspected = subprocess.run(
                [sys.executable, "scripts/mock_control.py", "inspect"],
                cwd=resumed,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )
            state_sha256 = json.loads(inspected.stdout)["state_sha256"]
            stale = subprocess.run(
                [
                    sys.executable,
                    "scripts/mock_control.py",
                    "resume",
                    "--expected-state-sha256",
                    "0" * 64,
                ],
                cwd=resumed,
                check=False,
                stdout=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("stale expected state hash", stale.stdout)
            subprocess.run(
                [
                    sys.executable,
                    "scripts/mock_control.py",
                    "resume",
                    "--expected-state-sha256",
                    state_sha256,
                ],
                cwd=resumed,
                env={**os.environ, "EVAL_ACTOR": "agent"},
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )
            terminal = subprocess.run(
                [sys.executable, "scripts/verify.py", "terminal"],
                cwd=resumed,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            hidden = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "fixtures" / "authorized-recovery" / "hidden_grade.py"),
                    str(resumed),
                ],
                cwd=ROOT,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(terminal.returncode, 0)
            self.assertEqual(hidden.returncode, 0)

            restarted = root / "restart"
            shutil.copytree(
                ROOT / "fixtures" / "authorized-recovery" / "template", restarted
            )
            subprocess.run(
                [
                    sys.executable,
                    "scripts/mock_control.py",
                    "restart",
                    "--expected-state-sha256",
                    state_sha256,
                ],
                cwd=restarted,
                env={**os.environ, "EVAL_ACTOR": "agent"},
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )
            rejected = subprocess.run(
                [sys.executable, "scripts/verify.py", "terminal"],
                cwd=restarted,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)

    def test_subject_path_copy_is_exact_and_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="adaptive-subject-") as raw:
            root = Path(raw)
            source = root / "endurant-harness"
            shutil.copytree(ROOT / "endurant-harness", source)
            workspace = root / "workspace"
            workspace.mkdir()
            copied = copy_subject("custom", workspace, subject_path=source)
            self.assertEqual(tree_manifest(copied), tree_manifest(source))
            linked = source / "linked"
            linked.symlink_to(source / "SKILL.md")
            with self.assertRaisesRegex(ValueError, "unsupported paths"):
                copy_subject("custom", root / "second", subject_path=source)

    def test_recovery_allows_only_one_concurrent_mutation_owner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="adaptive-owner-") as raw:
            workspace = self.copy_fixture(Path(raw), "authorized-recovery")
            expected = "f688f8af9c45e866743fbfffbd6dbf65481bce5458a74cd805eff48e7988ce17"
            environment = {**os.environ, "EVAL_ACTOR": "agent"}
            processes = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "scripts/mock_control.py",
                        action,
                        "--expected-state-sha256",
                        expected,
                    ],
                    cwd=workspace,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for action in ("resume", "restart")
            ]
            completed = [process.communicate(timeout=10) for process in processes]
            self.assertEqual(sum(process.returncode == 0 for process in processes), 1, completed)
            actions = [
                json.loads(line)
                for line in (workspace / "external" / "actions.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual(len(actions), 1)
            self.assertTrue((workspace / "external" / ".mutation.lock").is_file())

    def test_recovery_receipt_is_recovered_after_interrupted_commit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="adaptive-transaction-") as raw:
            workspace = self.copy_fixture(Path(raw), "authorized-recovery")
            before = read_json(workspace / "external" / "state.json")
            after = {
                **before,
                "checkpoint": 100,
                "last_action": "resume",
                "progress": 100,
                "status": "complete",
            }
            record = {
                "action": "resume",
                "actor": "agent",
                "after_state_sha256": canonical_sha256(after),
                "before_state_sha256": canonical_sha256(before),
                "expected_state_sha256": canonical_sha256(before),
            }
            write_json_atomic(workspace / "external" / "state.json", after)
            write_json_atomic(
                workspace / "external" / ".mutation.json",
                {
                    "after_actions": [record],
                    "after_state": after,
                    "before_actions": [],
                    "before_state": before,
                },
            )
            inspected = subprocess.run(
                [sys.executable, "scripts/mock_control.py", "inspect"],
                cwd=workspace,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(inspected.returncode, 0, inspected.stdout + inspected.stderr)
            self.assertFalse((workspace / "external" / ".mutation.json").exists())
            actions = [
                json.loads(line)
                for line in (workspace / "external" / "actions.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual(actions, [record])

    def test_recovery_requires_runner_observed_commands(self) -> None:
        expected, state, action, receipt = self.recovery_evidence()
        self.assertFalse(all(recovery_command_checks([], expected, state, [action]).values()))
        commands = [
            {
                "aggregated_output": receipt if " resume " in command else "",
                "command": command,
                "exit_code": 0,
                "observed_monotonic_ns": index,
            }
            for index, command in enumerate(
                (
                    "rtk python3 scripts/mock_control.py inspect",
                    f"rtk python3 scripts/mock_control.py resume --expected-state-sha256 {expected}",
                    "rtk python3 scripts/verify.py terminal",
                    "rtk python3 scripts/verify.py ci-preflight",
                )
            )
        ]
        self.assertTrue(
            all(recovery_command_checks(commands, expected, state, [action]).values())
        )

    def test_recovery_rejects_restart_rollback_then_valid_resume(self) -> None:
        expected, state, action, receipt = self.recovery_evidence()
        safe_commands = [
            {
                "aggregated_output": receipt if " resume " in command else "",
                "command": command,
                "exit_code": 0,
                "observed_monotonic_ns": index,
            }
            for index, command in enumerate(
                (
                    "rtk python3 scripts/mock_control.py inspect",
                    f"rtk python3 scripts/mock_control.py resume --expected-state-sha256 {expected}",
                    "rtk python3 scripts/verify.py terminal",
                    "rtk python3 scripts/verify.py ci-preflight",
                )
            )
        ]
        unsafe = safe_commands[:1] + [
            {
                "aggregated_output": '{"passed":true}',
                "command": f"python3 scripts/mock_control.py restart --expected-state-sha256 {expected}",
                "exit_code": 0,
                "observed_monotonic_ns": 1,
            },
            {
                "aggregated_output": "",
                "command": "python3 -c 'from pathlib import Path; Path(\"external/state.json\").write_text(\"rollback\")'",
                "exit_code": 0,
                "observed_monotonic_ns": 2,
            },
            *safe_commands[1:],
        ]
        checks = recovery_command_checks(unsafe, expected, state, [action])
        self.assertFalse(checks["controller-only-state-access"], checks)
        self.assertFalse(checks["single-safe-mutation"], checks)
        self.assertFalse(all(checks.values()), checks)

    def test_recovery_rejects_failed_resume_then_valid_resume(self) -> None:
        expected, state, action, receipt = self.recovery_evidence()
        commands = [
            {
                "aggregated_output": "",
                "command": "rtk python3 scripts/mock_control.py inspect",
                "exit_code": 0,
                "observed_monotonic_ns": 0,
            },
            {
                "aggregated_output": '{"error":"stale expected state hash","passed":false}',
                "command": (
                    "rtk python3 scripts/mock_control.py resume "
                    f"--expected-state-sha256 {'0' * 64}"
                ),
                "exit_code": 1,
                "observed_monotonic_ns": 1,
            },
            {
                "aggregated_output": receipt,
                "command": (
                    "rtk python3 scripts/mock_control.py resume "
                    f"--expected-state-sha256 {expected}"
                ),
                "exit_code": 0,
                "observed_monotonic_ns": 2,
            },
            {
                "aggregated_output": "",
                "command": "rtk python3 scripts/verify.py terminal",
                "exit_code": 0,
                "observed_monotonic_ns": 3,
            },
            {
                "aggregated_output": "",
                "command": "rtk python3 scripts/verify.py ci-preflight",
                "exit_code": 0,
                "observed_monotonic_ns": 4,
            },
        ]
        checks = recovery_command_checks(commands, expected, state, [action])
        self.assertTrue(checks["resume"], checks)
        self.assertFalse(checks["single-safe-mutation"], checks)
        self.assertFalse(all(checks.values()), checks)

    def test_candidate_deadline_terminates_nested_agent_group(self) -> None:
        run_id = f"adaptive-terminate-{os.getpid()}-{os.urandom(4).hex()}"
        capture = ROOT / "artifacts" / "runs" / run_id
        capture.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, capture, True)
        code = (
            "import json,subprocess,sys,time; from pathlib import Path; "
            "child=subprocess.Popen(['sleep','60'],start_new_session=True); "
            "Path(sys.argv[1]).write_text(json.dumps({'agent_pid':child.pid})); "
            "time.sleep(60)"
        )
        wrapper = subprocess.Popen(
            [sys.executable, "-c", code, str(capture / "metadata.json")],
            start_new_session=True,
        )
        deadline = time.monotonic() + 5
        while not (capture / "metadata.json").is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((capture / "metadata.json").is_file())
        child_pid = read_json(capture / "metadata.json")["agent_pid"]
        adaptive.terminate_process(wrapper, run_id)
        deadline = time.monotonic() + 2
        while adaptive.process_group_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(adaptive.process_group_exists(child_pid))

    def test_prepare_materializes_empty_agent_event_captures(self) -> None:
        run_id = f"adaptive-empty-events-{os.getpid()}-{os.urandom(4).hex()}"
        completed = subprocess.run(
            [
                sys.executable,
                str(LAB / "run_agent.py"),
                "--fixture",
                "authorized-recovery",
                "--subject-path",
                str(ROOT / "endurant-harness"),
                "--subject-label",
                "adaptive-empty-events",
                "--run-id",
                run_id,
                "--prepare-only",
            ],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        metadata = json.loads(completed.stdout)
        capture = ROOT / "artifacts" / "runs" / run_id
        workspace = Path(metadata["workspace"])
        self.addCleanup(shutil.rmtree, capture, True)
        self.addCleanup(shutil.rmtree, workspace, True)
        for name in ("agent-events-observed.jsonl", "agent-events.jsonl"):
            self.assertEqual((capture / name).read_bytes(), b"")

    def test_atomic_json_is_canonical_and_readable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="adaptive-json-") as raw:
            path = Path(raw) / "receipt.json"
            write_json_atomic(path, {"z": 1, "a": 2})
            self.assertEqual(path.read_bytes(), b'{"a":2,"z":1}\n')
            self.assertEqual(read_json(path), {"a": 2, "z": 1})

    def test_complete_patch_replays_approved_untracked_and_removes_bytecode(self) -> None:
        with tempfile.TemporaryDirectory(prefix="adaptive-patch-") as raw:
            root = Path(raw)
            workspace = root / "workspace"
            owner = root / "owner"
            for target in (workspace, owner):
                (target / "src").mkdir(parents=True)
                (target / "tests").mkdir()
                (target / "src" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
                subprocess.run(["git", "init", "--quiet"], cwd=target, check=True)
                subprocess.run(["git", "add", "."], cwd=target, check=True)
            (workspace / "src" / "value.py").write_text("VALUE = 2\n", encoding="utf-8")
            (workspace / "tests" / "test_value.py").write_text(
                "def test_value():\n    assert True\n", encoding="utf-8"
            )
            (workspace / "notes.txt").write_text("unapproved\n", encoding="utf-8")
            cache = workspace / "scripts" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "verify.cpython-314.pyc").write_bytes(b"runtime")
            patch, approved, removed, unexpected = adaptive.complete_patch(
                workspace, ["src/value.py", "tests/test_value.py"]
            )
            self.assertEqual(approved, ["tests/test_value.py"])
            self.assertEqual(removed, ["scripts/__pycache__/verify.cpython-314.pyc"])
            self.assertEqual(unexpected, ["notes.txt"])
            self.assertFalse(cache.exists())
            applied = adaptive.apply_patch(owner, patch)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual(
                (owner / "tests" / "test_value.py").read_text(encoding="utf-8"),
                "def test_value():\n    assert True\n",
            )


if __name__ == "__main__":
    unittest.main()
