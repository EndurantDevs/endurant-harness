"""Shared standard-library helpers for the Endurant Harness evaluation lab."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


LAB_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = LAB_ROOT / "artifacts"
FIXTURES = LAB_ROOT / "fixtures"
SUBJECTS = LAB_ROOT / "subjects"


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ignored_relative(relative: Path) -> bool:
    parts = relative.parts
    return (
        not parts
        or parts[0] == ".git"
        or parts[:2] == (".agents", "skills")
        or "__pycache__" in parts
        or relative.suffix in {".pyc", ".pyo"}
        or relative.name == ".DS_Store"
    )


def file_manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if ignored_relative(relative):
            continue
        result[relative.as_posix()] = sha256_file(path)
    return result


def tree_manifest(root: Path) -> dict[str, dict[str, Any]]:
    """Fingerprint files, directories, modes, and symlink targets under a fixed tree."""
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ignored_relative(relative):
            continue
        metadata = path.lstat()
        entry: dict[str, Any] = {"mode": stat.S_IMODE(metadata.st_mode)}
        if stat.S_ISLNK(metadata.st_mode):
            entry.update({"type": "symlink", "target": os.readlink(path)})
        elif stat.S_ISREG(metadata.st_mode):
            entry.update({"type": "file", "sha256": sha256_file(path)})
        elif stat.S_ISDIR(metadata.st_mode):
            entry["type"] = "directory"
        else:
            entry["type"] = "other"
        result[relative.as_posix()] = entry
    return result


def manifest_delta(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    before_paths = set(before)
    after_paths = set(after)
    return {
        "added": sorted(after_paths - before_paths),
        "removed": sorted(before_paths - after_paths),
        "modified": sorted(
            path for path in before_paths & after_paths if before[path] != after[path]
        ),
    }


def changed_paths(delta: dict[str, list[str]]) -> list[str]:
    return sorted({path for values in delta.values() for path in values})


def run_process(
    argv: list[str],
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def copy_subject(subject: str, workspace: Path) -> Path:
    source = SUBJECTS / subject / "endurant-harness"
    if not (source / "SKILL.md").is_file():
        raise FileNotFoundError(f"unknown subject or missing SKILL.md: {source}")
    target = workspace / ".agents" / "skills" / "endurant-harness"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    return target


def materialize_workspace(
    fixture: str, subject: str, run_id: str, *, workspace_id: str | None = None
) -> tuple[Path, Path]:
    fixture_root = FIXTURES / fixture
    template = fixture_root / "template"
    if not template.is_dir():
        raise FileNotFoundError(f"unknown fixture: {fixture}")
    workspace = ARTIFACTS / "workspaces" / (workspace_id or run_id)
    capture = ARTIFACTS / "runs" / run_id
    if workspace.exists() or capture.exists():
        raise FileExistsError(f"run already exists: {run_id}")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    capture.mkdir(parents=True, exist_ok=False)
    shutil.copytree(template, workspace)
    copy_subject(subject, workspace)

    init = run_process(["git", "init", "--quiet"], workspace)
    if init.returncode != 0:
        raise RuntimeError(f"git init failed: {init.stderr}")
    add = run_process(["git", "add", "."], workspace)
    if add.returncode != 0:
        raise RuntimeError(f"git add failed: {add.stderr}")
    return workspace, capture


def git_state(root: Path) -> dict[str, str | None]:
    index = run_process(["git", "ls-files", "--stage", "-z"], root)
    if index.returncode != 0:
        raise RuntimeError(f"git index inspection failed: {index.stderr}")
    head = run_process(["git", "rev-parse", "--verify", "HEAD"], root)
    return {
        "index_sha256": hashlib.sha256(index.stdout.encode("utf-8")).hexdigest(),
        "head": head.stdout.strip() if head.returncode == 0 else None,
    }


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            events.append(value)
    return events


def latest_passing_event(
    events: Iterable[dict[str, Any]], gate: str, actor: str
) -> dict[str, Any] | None:
    selected = [
        event
        for event in events
        if event.get("gate") == gate and event.get("actor") == actor and event.get("passed") is True
    ]
    return selected[-1] if selected else None


def canonical_prompt(fixture: str) -> str:
    task = (FIXTURES / fixture / "task.txt").read_text(encoding="utf-8").strip()
    return (
        "Use $endurant-harness to complete this task in the current repository.\n\n"
        f"{task}\n\n"
        "Work only in this repository. Follow AGENTS.md and the repository profile. "
        "Do not commit, reset, clean, or access unrelated paths."
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
