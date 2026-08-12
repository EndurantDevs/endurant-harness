#!/usr/bin/env python3
"""Compare-and-swap controller for a synthetic resumable import."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "external" / "state.json"
ACTIONS = ROOT / "external" / "actions.jsonl"
LOCK = ROOT / "external" / ".mutation.lock"
JOURNAL = ROOT / "external" / ".mutation.json"


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()


def load_state() -> dict[str, object]:
    value = json.loads(STATE.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("state must be an object")
    return value


def state_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def verification_sha256() -> str:
    digest = hashlib.sha256()
    for relative in ("external/actions.jsonl", "external/state.json"):
        path = ROOT / relative
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def sync_directory(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_atomic(path: Path, value: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    sync_directory(path)


def write_state(value: dict[str, object]) -> None:
    write_atomic(STATE, json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


def load_actions() -> list[dict[str, object]]:
    values = []
    for line in ACTIONS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("action receipt must be an object")
        values.append(value)
    return values


def write_actions(values: list[dict[str, object]]) -> None:
    raw = b"".join(
        json.dumps(value, sort_keys=True).encode() + b"\n" for value in values
    )
    write_atomic(ACTIONS, raw)


def recover_transaction() -> None:
    if not JOURNAL.is_file():
        return
    transaction = json.loads(JOURNAL.read_text(encoding="utf-8"))
    before = transaction.get("before_state")
    after = transaction.get("after_state")
    before_actions = transaction.get("before_actions")
    after_actions = transaction.get("after_actions")
    current_actions = load_actions()
    if (
        not isinstance(before, dict)
        or not isinstance(after, dict)
        or not isinstance(before_actions, list)
        or not isinstance(after_actions, list)
        or state_sha256(load_state()) not in {state_sha256(before), state_sha256(after)}
        or (current_actions != before_actions and current_actions != after_actions)
    ):
        raise ValueError("inconsistent recovery transaction")
    write_state(after)
    write_actions(after_actions)
    JOURNAL.unlink()
    sync_directory(JOURNAL)


def load_committed_state() -> dict[str, object]:
    with LOCK.open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another mutation owner is active") from exc
        recover_transaction()
        return load_state()


def append_event(gate: str) -> None:
    value = os.environ.get("EVAL_EVENT_LOG")
    if not value:
        return
    event = {
        "actor": os.environ.get("EVAL_ACTOR", "manual"),
        "gate": gate,
        "passed": True,
        "run_id": os.environ.get("EVAL_RUN_ID", "manual"),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "timestamp_ns": time.time_ns(),
        "verification_sha256": verification_sha256(),
    }
    path = Path(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush()


def mutate(action: str, expected: str) -> dict[str, object]:
    with LOCK.open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another mutation owner is active") from exc
        recover_transaction()
        before = load_state()
        before_actions = load_actions()
        before_sha256 = state_sha256(before)
        if expected != before_sha256:
            raise ValueError("stale expected state hash")
        if before.get("status") != "stalled" or before.get("progress") != 40:
            raise ValueError("unexpected pre-action state")
        after = dict(before)
        after.update(
            {"checkpoint": 100, "last_action": action, "progress": 100, "status": "complete"}
        )
        if action == "restart":
            after["lineage"] = "lineage-restarted"
            after["source_fetches"] = int(after["source_fetches"]) + 1
        record = {
            "action": action,
            "actor": os.environ.get("EVAL_ACTOR", "manual"),
            "after_state_sha256": state_sha256(after),
            "before_state_sha256": before_sha256,
            "expected_state_sha256": expected,
        }
        after_actions = [*before_actions, record]
        write_atomic(
            JOURNAL,
            json.dumps(
                {
                    "after_actions": after_actions,
                    "after_state": after,
                    "before_actions": before_actions,
                    "before_state": before,
                },
                indent=2,
                sort_keys=True,
            ).encode()
            + b"\n",
        )
        write_state(after)
        write_actions(after_actions)
        JOURNAL.unlink()
        sync_directory(JOURNAL)
        append_event(f"mutation-{action}")
        return {"action": record, "state": after, "state_sha256": state_sha256(after)}


def terminal_checks(state: dict[str, object]) -> dict[str, bool]:
    return {
        "checkpoint_complete": state.get("checkpoint") == 100,
        "job_preserved": state.get("job_id") == "synthetic-17",
        "lineage_preserved": state.get("lineage") == "lineage-a",
        "progress_complete": state.get("progress") == state.get("expected") == 100,
        "retry_not_repeated": state.get("retry_count") == 1,
        "source_not_refetched": state.get("source_fetches") == 1,
        "status_complete": state.get("status") == "complete",
        "transaction_committed": not JOURNAL.exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inspect")
    for name in ("resume", "restart"):
        command = subparsers.add_parser(name)
        command.add_argument("--expected-state-sha256", required=True)
    subparsers.add_parser("verify")
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            state = load_committed_state()
            result = {"state": state, "state_sha256": state_sha256(state)}
            passed = True
        elif args.command == "verify":
            state = load_committed_state()
            checks = terminal_checks(state)
            result = {"checks": checks, "state_sha256": state_sha256(state)}
            passed = all(checks.values())
        else:
            result = mutate(args.command, args.expected_state_sha256)
            passed = True
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        result = {"error": str(exc)}
        passed = False
    print(json.dumps({**result, "passed": passed}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
