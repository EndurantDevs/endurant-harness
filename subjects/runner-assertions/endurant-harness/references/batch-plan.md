# Staged Runner Plan

Run the bundled script by absolute path from the skill directory:

```bash
python3 -S /absolute/path/to/endurant-harness/scripts/endurant.py run /tmp/proof-plan.json --repo .
```

The standard-library runner stores complete output in a temporary log directory and emits a bounded evidence summary.

## Schema

```json
{
  "name": "prove-cache-fix",
  "cwd": ".",
  "default_timeout": 120,
  "require_behavior_evidence": true,
  "stages": [
    {
      "name": "focused",
      "parallel": true,
      "commands": [
        {
          "id": "regression",
          "argv": ["pytest", "tests/test_cache.py::test_expired_refetch", "-q"],
          "evidence": "behavior",
          "timeout": 90,
          "expected_exit_codes": [0]
        },
        {
          "id": "lint-changed",
          "argv": ["ruff", "check", "src/cache.py", "tests/test_cache.py"],
          "evidence": "static"
        }
      ]
    },
    {
      "name": "affected-scope",
      "parallel": false,
      "commands": [
        {"id": "cache-suite", "argv": ["pytest", "tests/cache", "-q"], "evidence": "integration"}
      ]
    },
    {
      "name": "diff",
      "run_if": "always",
      "parallel": true,
      "commands": [
        {"id": "diff-check", "argv": ["git", "diff", "--check"], "evidence": "diff", "timeout": 30},
        {"id": "diff-stat", "argv": ["git", "diff", "--stat"], "evidence": "diff", "timeout": 30}
      ]
    }
  ]
}
```

## Rules

- Prefer `argv` arrays; shell strings and inline shell forms are rejected unless `--allow-shell` is explicitly supplied for reviewed trusted input.
- The plan root defines the allowed working tree. Per-command `cwd` must remain inside it.
- `parallel: true` requires every command in that stage to be independent and free of shared mutable resources.
- `run_if` is `success` by default. Use `always` for cleanup/final evidence and `failure` only for diagnostics.
- Later success stages are skipped after a required failure; `always` stages still run.
- Tag each command with `behavior`, `integration`, `static`, `diagnostic`, `diff`, `cleanup`, or `other`.
- Set `require_behavior_evidence: true` for behavior-changing work. The run fails if no `behavior` command passes.
- Use `expected_exit_codes` only when nonzero is intentionally part of the evidence.
- Use `must_match` or `must_not_match` for bounded-tail regex assertions such as rejecting `Ran 0 tests` after an accepted exit.
- Full output is written to one log per command. The summary contains evidence kind, status, exit code, duration, command, and a short failure tail.
- A runner failure is evidence, not permission to patch. Classify it first.

Generate an editable example with:

```bash
python3 -S /absolute/path/to/endurant-harness/scripts/endurant.py template > /tmp/proof-plan.json
```
