"""Repository-owned fast-preflight contract prototype."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _command(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("command must be an object")
    argv = value.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
    ):
        raise ValueError("command argv must be a non-empty string array")
    if value.get("cwd", ".") != "." or "shell" in value:
        raise ValueError("preflight commands must use argv at repository root")
    return value


def resolve(
    profile: dict[str, Any] | None,
    *,
    required_checks: list[str],
    bundle_id: str,
    original_commands: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve one trusted bundle; absent profiles preserve the original object."""
    if profile is None:
        return original_commands
    if profile.get("schema_version") != 1:
        raise ValueError("unsupported fast-preflight schema")
    checks = profile.get("checks")
    bundles = profile.get("bundles")
    if not isinstance(checks, dict) or not isinstance(bundles, dict):
        raise ValueError("profile requires checks and bundles")
    bundle = _command(bundles.get(bundle_id))
    covers = bundle.get("covers")
    if not isinstance(covers, list) or len(covers) != len(set(covers)):
        raise ValueError("bundle covers must be a unique array")
    if not all(isinstance(item, str) and item in checks for item in covers):
        raise ValueError("bundle covers unknown check")
    selected = [{"id": f"bundle:{bundle_id}", **bundle}]
    for check_id in required_checks:
        if check_id in covers:
            continue
        if check_id not in checks:
            raise ValueError(f"unknown required check: {check_id}")
        selected.append({"id": check_id, **_command(checks[check_id])})
    return selected


def verify_receipt(
    profile: dict[str, Any],
    bundle_id: str,
    receipt: dict[str, Any],
    final_fingerprint: str,
) -> bool:
    bundle = profile.get("bundles", {}).get(bundle_id, {})
    required = bundle.get("receipt", {}).get("required_check_ids")
    checks = receipt.get("checks")
    valid_checks = bool(
        isinstance(checks, list)
        and all(isinstance(item, dict) for item in checks)
    )
    return bool(
        isinstance(required, list)
        and valid_checks
        and receipt.get("schema_version") == 1
        and receipt.get("profile_sha256") == canonical_sha256(profile)
        and receipt.get("verification_sha256") == final_fingerprint
        # The type guard above makes these comprehensions fail closed.
        and [item.get("id") for item in checks] == required
        and len({item.get("id") for item in checks}) == len(required)
        and all(item.get("passed") is True for item in checks)
    )


def synthetic_checks(root: Path) -> dict[str, bool]:
    """Small dependency-free CI contract used only by the evaluation fixture."""
    focused = (root / "src" / "focused.txt").read_text(encoding="utf-8").strip() == "pass"
    python_files = sorted(root.rglob("*.py"))
    lint = all(
        not any(line.endswith((" ", "\t")) for line in path.read_text(encoding="utf-8").splitlines())
        for path in python_files
    )
    build = True
    for path in python_files:
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            build = False
    typed = ast.parse((root / "src" / "typed.py").read_text(encoding="utf-8"))
    assignments = [node for node in ast.walk(typed) if isinstance(node, ast.AnnAssign)]
    typecheck = bool(
        assignments
        and isinstance(assignments[0].annotation, ast.Name)
        and assignments[0].annotation.id == "int"
        and isinstance(assignments[0].value, ast.Constant)
        and isinstance(assignments[0].value.value, int)
    )
    generated = root / "generated" / "output.txt"
    expected = (root / "generated" / "expected.sha256").read_text(encoding="utf-8").strip()
    generated_ok = hashlib.sha256(generated.read_bytes()).hexdigest() == expected
    shared = (root / "shared" / "status.txt").read_text(encoding="utf-8").strip() == "pass"
    return {
        "focused": focused,
        "lint": lint,
        "typecheck": typecheck,
        "build": build,
        "generated-drift": generated_ok,
        "shared-package": shared,
    }
