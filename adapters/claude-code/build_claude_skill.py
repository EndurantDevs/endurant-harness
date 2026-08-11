#!/usr/bin/env python3
"""Derive the Claude Code variant of the Endurant Harness skill package.

The canonical package under ``endurant-harness/`` targets Codex CLI. Claude
Code discovers user skills from ``~/.claude/skills/<name>/`` using the same
SKILL.md frontmatter contract, and the harness scripts are agent-neutral, so
the variant is a deterministic text derivation, not a fork:

1. Copy the canonical package byte-for-byte (modes preserved).
2. Widen the three instruction-file mentions of ``AGENTS.md`` to also name
   ``CLAUDE.md`` (Claude Code's project-instruction file). Every transform is
   an exact-match replacement that fails loudly if the canonical wording
   drifts, so a new upstream release cannot silently produce a stale variant.
3. Restamp the provenance marker with release ``<source-release>-claude`` and
   the recomputed canonical package hash, using the package's own hashing
   code so the two implementations cannot drift.
4. Verify with the package's own strict audit and provenance check.

No engine change is made for instruction discovery: Claude Code loads
CLAUDE.md into context by itself, so the probe listing AGENTS.md files only
is sufficient and keeps ``scripts/endurant.py`` identical across variants.

Usage:
  python3 adapters/claude-code/build_claude_skill.py --output DIR
  python3 adapters/claude-code/build_claude_skill.py --install

``--install`` stages the variant and atomically replaces
``~/.claude/skills/endurant-harness``, keeping a rollback copy of any
previous installation beside the skills directory.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

SKILL_NAME = "endurant-harness"
WORD_LIMIT = 450

# Exact-match transforms; a failed match aborts the build so upstream wording
# changes surface here instead of shipping a half-adapted variant.
TRANSFORMS: tuple[tuple[str, str, str], ...] = (
    (
        "SKILL.md",
        "Follow `AGENTS.md`;",
        "Follow `AGENTS.md`/`CLAUDE.md`;",
    ),
    (
        "references/protocol.md",
        "Read applicable `AGENTS.md` or override files",
        "Read applicable `AGENTS.md`/`CLAUDE.md` or override files",
    ),
    (
        "references/protocol.md",
        "Put durable repository-specific commands and invariants in `AGENTS.md` or",
        "Put durable repository-specific commands and invariants in `AGENTS.md`/`CLAUDE.md` or",
    ),
    (
        "references/repository-profile.md",
        "Put durable repository-specific guidance in `AGENTS.md`.",
        "Put durable repository-specific guidance in `AGENTS.md` or `CLAUDE.md`.",
    ),
)


def fail(message: str) -> "SystemExit":
    return SystemExit(f"build_claude_skill: {message}")


def load_engine(package_root: Path):
    engine_path = package_root / "scripts" / "endurant.py"
    spec = importlib.util.spec_from_file_location("endurant_engine", engine_path)
    if spec is None or spec.loader is None:
        raise fail(f"cannot import engine module: {engine_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def apply_transforms(package_root: Path) -> None:
    for relative, old, new in TRANSFORMS:
        path = package_root / relative
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise fail(
                f"canonical wording not found in {relative}: {old!r}; "
                "upstream changed - update TRANSFORMS deliberately"
            )
        if text.count(old) != 1:
            raise fail(f"transform anchor is not unique in {relative}: {old!r}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")


def restamp_provenance(package_root: Path, engine) -> tuple[str, str]:
    skill_path = package_root / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    matches = list(engine.PROVENANCE_RE.finditer(text))
    if len(matches) != 1:
        raise fail("SKILL.md must contain exactly one provenance marker")
    source_release = matches[0].group(1)
    if source_release.endswith("-claude"):
        raise fail(f"source package already carries a claude release: {source_release}")
    release = f"{source_release}-claude"
    if len(release) > 32:
        raise fail(f"derived release id exceeds 32 chars: {release}")
    placeholder_marker = (
        f"<!--endurant-provenance:{release}:{engine.PROVENANCE_PLACEHOLDER}-->"
    )
    skill_path.write_text(
        engine.PROVENANCE_RE.sub(placeholder_marker, text, count=1), encoding="utf-8"
    )
    _, _, computed = engine._canonical_package_sha256(package_root)
    stamped_marker = f"<!--endurant-provenance:{release}:{computed}-->"
    stamped = skill_path.read_text(encoding="utf-8").replace(
        placeholder_marker, stamped_marker, 1
    )
    skill_path.write_text(stamped, encoding="utf-8")
    return release, computed


def check_word_budget(package_root: Path) -> int:
    words = len((package_root / "SKILL.md").read_text(encoding="utf-8").split())
    if words > WORD_LIMIT:
        raise fail(f"SKILL.md has {words} words; maximum is {WORD_LIMIT}")
    return words


def run_checked(argv: list[str], cwd: Path, expect: str, label: str) -> str:
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=300,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0 or expect not in output:
        raise fail(f"{label} failed (exit {completed.returncode}):\n{output.strip()}")
    return output


def verify(package_root: Path, release: str, package_sha256: str) -> None:
    run_checked(
        [
            sys.executable,
            "-S",
            str(package_root / "scripts" / "audit_skill.py"),
            str(package_root),
            "--strict",
            "--format",
            "text",
        ],
        package_root.parent,
        "PASS:",
        "strict audit",
    )
    run_checked(
        [
            sys.executable,
            "-S",
            str(package_root / "scripts" / "endurant.py"),
            "provenance",
            "--loaded-provenance",
            f"{release}:{package_sha256}",
        ],
        package_root.parent,
        "PROVENANCE CURRENT",
        "provenance check",
    )


def build(source: Path, destination: Path, engine) -> dict[str, object]:
    if destination.exists():
        raise fail(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    for cache in destination.rglob("__pycache__"):
        shutil.rmtree(cache)
    apply_transforms(destination)
    release, package_sha256 = restamp_provenance(destination, engine)
    words = check_word_budget(destination)
    verify(destination, release, package_sha256)
    return {
        "release": release,
        "package_sha256": package_sha256,
        "skill_md_words": words,
        "path": str(destination),
    }


def install(staged: Path, receipt: dict[str, object]) -> dict[str, object]:
    skills_dir = Path.home() / ".claude" / "skills"
    target = skills_dir / SKILL_NAME
    skills_dir.mkdir(parents=True, exist_ok=True)
    rollback: str | None = None
    if target.exists():
        marker = str(receipt["package_sha256"])[:8]
        backup = Path.home() / ".claude" / f".{SKILL_NAME}-claude-replaced-{marker}"
        if backup.exists():
            shutil.rmtree(backup)
        target.rename(backup)
        rollback = str(backup)
    staged.rename(target)
    return {"installed": str(target), "rollback": rollback}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=None,
        help="canonical package directory (default: endurant-harness/ beside adapters/)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", help="write the variant package to DIR/endurant-harness")
    group.add_argument(
        "--install",
        action="store_true",
        help="atomically install to the Claude Code user skills directory",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    source = Path(args.source).resolve() if args.source else repo_root / SKILL_NAME
    if not (source / "SKILL.md").is_file():
        raise fail(f"source package not found: {source}")
    engine = load_engine(source)

    if args.install:
        stage_parent = Path(tempfile.mkdtemp(prefix="endurant-claude-stage-"))
        try:
            receipt = build(source, stage_parent / SKILL_NAME, engine)
            receipt.update(install(stage_parent / SKILL_NAME, receipt))
        finally:
            shutil.rmtree(stage_parent, ignore_errors=True)
    else:
        receipt = build(source, Path(args.output).resolve() / SKILL_NAME, engine)

    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
