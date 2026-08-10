"""Minimal JSON command-line entrypoint for settings merging."""

from __future__ import annotations

import json
import sys

from src.settings import merge_settings


def main() -> int:
    payload = json.load(sys.stdin)
    json.dump(merge_settings(payload["defaults"], payload["overrides"]), sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
