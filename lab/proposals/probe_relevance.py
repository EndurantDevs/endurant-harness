"""Exact-symbol-first candidate ranking without changing the packaged probe."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from types import ModuleType


TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,}")
HARNESS_TERMS = {"endurant", "harness", "repo-harness", "skill"}


def exact_symbols(task: str) -> list[str]:
    result: list[str] = []
    for token in TOKEN.findall(task):
        if "_" not in token and re.search(r"[a-z][A-Z]", token) is None:
            continue
        if token not in result:
            result.append(token)
    return result


def _self_noise(path: str) -> bool:
    parts = Path(path.removeprefix("./")).parts
    return any(
        part == "endurant-harness"
        or part == "repo-harness"
        or part.startswith("endurant-harness-")
        or part.startswith("repo-harness-")
        for part in parts
    )


def _score(path: str, symbols: list[str]) -> tuple[int, str]:
    relative = path.removeprefix("./")
    parsed = Path(relative)
    parts = set(parsed.parts)
    name = parsed.name.lower()
    score = 100
    if {"src", "lib"} & parts:
        score += 45
    if {"test", "tests"} & parts or name.startswith("test_") or ".test." in name:
        score += 40
    if parsed.suffix == ".md" or "docs" in parts:
        score -= 30
    normalized_path = re.sub(r"[^a-z0-9]", "", relative.lower())
    if any(re.sub(r"[^a-z0-9]", "", symbol.lower()) in normalized_path for symbol in symbols):
        score += 20
    return score, relative


def candidate_paths(
    runtime: ModuleType,
    root: Path,
    task: str,
    max_items: int,
    *,
    fallback: Callable[[Path, str, int], tuple[list[str], list[str]]] | None = None,
) -> tuple[list[str], list[str]]:
    """Return ranked exact hits, falling back byte-for-byte to current broad search."""
    broad_search = fallback or runtime._candidate_paths
    symbols = exact_symbols(task)
    if not symbols:
        return broad_search(root, task, max_items)
    rg = shutil.which("rg")
    if not rg:
        return broad_search(root, task, max_items)
    argv = [
        rg,
        "-l",
        "--hidden",
        "--no-messages",
        "--max-filesize",
        str(runtime.MAX_FILE_BYTES),
        "-F",
    ]
    for ignored in sorted(runtime.IGNORED_DIRS):
        argv.extend(["--glob", f"!**/{ignored}/**"])
    for symbol in symbols:
        argv.extend(["-e", symbol])
    argv.append(".")
    code, output, capture_truncated = runtime._run_capture(argv, root, timeout=2)
    if code not in {0, 1}:
        return broad_search(root, task, max_items)
    task_words = {word.lower() for word in TOKEN.findall(task)}
    keep_self = bool(task_words & HARNESS_TERMS)
    paths = [
        line
        for line in output.splitlines()
        if line and (keep_self or not _self_noise(line))
    ]
    if not paths:
        return broad_search(root, task, max_items)
    ranked = sorted(set(paths), key=lambda path: (-_score(path, symbols)[0], _score(path, symbols)[1]))
    warnings = ["candidate search output truncated"] if capture_truncated else []
    if len(ranked) > max_items:
        ranked = ranked[:max_items]
        warnings.append("candidate path list truncated")
    if len(ranked) == 1:
        broad, broad_warnings = broad_search(root, task, max_items)
        for path in broad:
            if path not in ranked and (keep_self or not _self_noise(path)):
                ranked.append(path)
            if len(ranked) >= min(max_items, 3):
                break
        warnings.extend(item for item in broad_warnings if item not in warnings)
    return ranked, warnings
