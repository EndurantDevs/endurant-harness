#!/usr/bin/env python3
"""External functional grader for the record-selection fixture."""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path


def load_function(workspace: Path):
    path = workspace / "src" / "record_selection.py"
    spec = importlib.util.spec_from_file_location("graded_record_selection", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.select_records


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    select_records = load_function(workspace)
    records = [
        {"id": "dup", "value": "first"},
        {"id": "x", "value": "x"},
        {"id": "dup", "value": "last"},
    ]
    before = [dict(record) for record in records]
    requested = ["dup", "missing", "x", "dup"]
    result = select_records(records, requested)
    checks = {
        "signature": list(inspect.signature(select_records).parameters) == ["records", "requested_ids"],
        "first_match": result[0] is records[0] and result[2] is records[0],
        "repeated_requests": len(result) == 3,
        "missing_ignored": all(item["id"] != "missing" for item in result),
        "order": result == [records[0], records[1], records[0]],
        "input_not_mutated": records == before,
    }
    print(json.dumps({"passed": all(checks.values()), "checks": checks}, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
