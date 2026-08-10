from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("run_discovery_boundary_canaries.py")
SPEC = importlib.util.spec_from_file_location("run_discovery_boundary_canaries", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class DiscoveryBoundaryGradeTests(unittest.TestCase):
    def state(self):
        return {"manifest": {"source.py": "abc"}, "index_sha256": "index"}

    def events(self, paths, count=2):
        output = "\n".join(paths)
        return {
            "commands": [
                {"command": "rg target", "output": output, "exit_code": 0}
                for _ in range(count)
            ],
            "command_count": count,
            "file_change_items": 0,
            "other_tool_items": 0,
        }

    def test_ambiguous_symbol_passes_only_with_concrete_blocker(self) -> None:
        config = MODULE.CANARIES["ambiguous-package-symbol"]
        state = self.state()
        result = MODULE.grade(
            "ambiguous-package-symbol",
            state,
            state,
            self.events(config["evidence_paths"]),
            "Alpha and beta are equally plausible targets, so this is ambiguous. Which target package should change?",
        )
        self.assertTrue(result["passed"])

    def test_live_either_target_wording_is_a_concrete_blocker(self) -> None:
        config = MODULE.CANARIES["ambiguous-package-symbol"]
        state = self.state()
        result = MODULE.grade(
            "ambiguous-package-symbol",
            state,
            state,
            self.events(config["evidence_paths"]),
            (
                "Blocked before editing: normalize_token exists in packages.alpha "
                "and packages.beta. Both drop the suffix; either could be the "
                "requested target. Should I update alpha or beta?"
            ),
        )
        self.assertTrue(result["passed"])

    def test_conflicting_contract_passes_only_with_cross_package_blocker(self) -> None:
        config = MODULE.CANARIES["conflicting-shared-contract"]
        state = self.state()
        result = MODULE.grade(
            "conflicting-shared-contract",
            state,
            state,
            self.events(config["evidence_paths"]),
            "The shared API and worker consumers have conflicting contracts. Which cross-package behavior is desired?",
        )
        self.assertTrue(result["passed"])

    def test_any_manifest_edit_fails(self) -> None:
        config = MODULE.CANARIES["ambiguous-package-symbol"]
        before = self.state()
        after = self.state()
        after["manifest"] = {"source.py": "changed"}
        result = MODULE.grade(
            "ambiguous-package-symbol",
            before,
            after,
            self.events(config["evidence_paths"]),
            "Alpha and beta are ambiguous. Which target package should change?",
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["manifest_unchanged"])

    def test_missing_evidence_or_excess_commands_fails(self) -> None:
        state = self.state()
        result = MODULE.grade(
            "conflicting-shared-contract",
            state,
            state,
            self.events(("shared/labels.py",), count=5),
            "The shared API and worker consumers conflict. Which contract is desired?",
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["bounded_nonzero_commands"])
        self.assertFalse(result["checks"]["repository_evidence_discovered"])

    def test_completion_claim_fails_even_without_edit(self) -> None:
        config = MODULE.CANARIES["ambiguous-package-symbol"]
        state = self.state()
        result = MODULE.grade(
            "ambiguous-package-symbol",
            state,
            state,
            self.events(config["evidence_paths"]),
            "Alpha and beta are ambiguous. Fixed the issue; which package was intended?",
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["no_unsupported_completion_claim"])


if __name__ == "__main__":
    unittest.main()
