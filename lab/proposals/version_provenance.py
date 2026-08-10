"""Compact package/session provenance prototype."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def package_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def receipt(
    *,
    current_release: str,
    current_package_hash: str,
    loaded_release: str | None,
    loaded_package_hash: str | None,
) -> dict[str, Any]:
    if not loaded_release or not loaded_package_hash:
        state = "unknown"
    elif loaded_release != current_release or loaded_package_hash != current_package_hash:
        state = "stale"
    else:
        state = "current"
    short_hash = current_package_hash[:12]
    compact = json.dumps(
        {"v": current_release, "h": short_hash, "s": state},
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "release": current_release,
        "package_sha256": current_package_hash,
        "loaded_release": loaded_release,
        "loaded_package_sha256": loaded_package_hash,
        "state": state,
        "compact": compact,
        "compact_bytes": len(compact.encode("utf-8")),
    }
