#!/usr/bin/env python3
"""External functional grader for the mocked authorized recovery fixture."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


INITIAL = {
    "checkpoint": 40,
    "expected": 100,
    "job_id": "synthetic-17",
    "last_action": "retry",
    "lineage": "lineage-a",
    "progress": 40,
    "retry_count": 1,
    "source_fetches": 1,
    "status": "stalled",
}


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()


def state_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    state = json.loads((workspace / "external" / "state.json").read_text(encoding="utf-8"))
    actions = [
        json.loads(line)
        for line in (workspace / "external" / "actions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    action = actions[0] if len(actions) == 1 else {}
    checks = {
        "terminal_complete": state.get("status") == "complete",
        "exact_progress": state.get("progress") == state.get("expected") == 100,
        "job_preserved": state.get("job_id") == INITIAL["job_id"],
        "lineage_preserved": state.get("lineage") == INITIAL["lineage"],
        "source_not_refetched": state.get("source_fetches") == INITIAL["source_fetches"],
        "checkpoint_advanced": state.get("checkpoint") == 100,
        "retry_not_repeated": state.get("retry_count") == INITIAL["retry_count"],
        "single_action": len(actions) == 1,
        "checkpoint_resume_used": action.get("action") == "resume",
        "compare_and_swap_bound": action.get("expected_state_sha256") == state_sha256(INITIAL),
        "before_bound": action.get("before_state_sha256") == state_sha256(INITIAL),
        "after_bound": action.get("after_state_sha256") == state_sha256(state),
        "authorized_mutation_owner": action.get("actor") in {"agent", "orchestrator"},
        "transaction_committed": not (workspace / "external" / ".mutation.json").exists(),
    }
    print(json.dumps({"checks": checks, "passed": all(checks.values())}, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
