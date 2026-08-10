#!/usr/bin/env python3
"""Invoke the unchanged Python repository-scan kernel for the Rust spike."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "subjects"
    / "current"
    / "endurant-harness"
    / "scripts"
    / "endurant.py"
)


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: python_scan_kernel.py ROOT MAX_DEPTH MAX_ITEMS", file=sys.stderr)
        return 2
    spec = importlib.util.spec_from_file_location("current_endurant", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    result = module._scan_repository(Path(sys.argv[1]).resolve(), int(sys.argv[2]), int(sys.argv[3]))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
