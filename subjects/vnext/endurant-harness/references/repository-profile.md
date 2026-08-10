# Repository Engineering Profile

Put durable repository-specific guidance in `AGENTS.md`. When a separate human-readable handoff is useful, save this template at `.agents/endurant-harness-profile.md`; `scripts/endurant.py probe` loads it automatically.

## Project shape

- Languages/frameworks:
- Package/workspace boundaries:
- Important entry points:
- Generated files and generators:
- Required external services:

## Canonical commands

- Install/bootstrap: `...`
- Focused test: `...`
- Package/module test: `...`
- Full test: `...`
- Format: `...`
- Lint: `...`
- Typecheck: `...`
- Build: `...`
- Integration/E2E: `...`
- Migration/rollback validation: `...`
- Synthetic benchmark/stress: `...`
- Local CI preflight: `...`

## Change rules

- Files/directories not to edit directly:
- Public compatibility guarantees:
- Database/migration rules:
- Error-handling/logging conventions:
- Test placement/naming:
- Required docs/changelog updates:
- Security or privacy constraints:

## Environment notes

- Required environment variables:
- Services/containers and bounded readiness checks:
- Known pre-existing failures:
- Platform-specific limitations:
- Commands that are slow, destructive, or require approval:

## Completion policy

- Minimum checks for ordinary changes:
- Additional checks for risky areas:
- Required final handoff format:

## Optional fast-preflight contract

Use `.agents/endurant-harness-preflight.json` only when one repository-owned local-CI command already covers focused or static checks that would otherwise run twice. Version 1 uses repository-root argument arrays only:

```json
{
  "schema_version": 1,
  "checks": {
    "focused": {
      "argv": ["python3", "tools/verify.py", "focused"],
      "evidence": "behavior",
      "timeout": 120,
      "env": {}
    }
  },
  "bundles": {
    "local-ci": {
      "command": {
        "argv": ["python3", "tools/verify.py", "local-ci"],
        "evidence": "integration",
        "timeout": 600,
        "env": {}
      },
      "covers": ["focused"]
    }
  }
}
```

Pin the profile hash reported by `probe`, then run:

```bash
python3 -S <skill>/scripts/endurant.py preflight \
  --repo . --bundle local-ci --require focused \
  --profile-sha256 <sha256>
```

The bundle runs once. The harness injects `ENDURANT_RECEIPT_PATH`, `ENDURANT_PROFILE_SHA256`, `ENDURANT_VERIFICATION_SHA256`, and `ENDURANT_BUNDLE_ID`. The verification SHA is the fingerprint observed before the bundle; the harness checks that it is unchanged afterward. The repository command must write this shape to `ENDURANT_RECEIPT_PATH`, with the exact ordered covered IDs and literal `passed: true`:

```json
{
  "schema_version": 1,
  "profile_sha256": "<ENDURANT_PROFILE_SHA256>",
  "verification_sha256": "<ENDURANT_VERIFICATION_SHA256>",
  "bundle_id": "<ENDURANT_BUNDLE_ID>",
  "checks": [
    {"id": "focused", "passed": true}
  ]
}
```

Only required checks not covered by the bundle run separately. A missing, dirty, untracked, symlinked, stale, malformed, or diff-mutating contract fails closed. Without this profile, use the ordinary focused/local-CI workflow unchanged.

## Optional benchmark contract

Use `.agents/endurant-harness-benchmarks.json` only for performance or efficiency acceptance. Each phase executes the declared benchmark exactly once. The harness injects `ENDURANT_BENCHMARK_EVENT_PATH`, `ENDURANT_PROFILE_SHA256`, `ENDURANT_BENCHMARK_ID`, and `ENDURANT_BENCHMARK_PHASE`.

```json
{
  "schema_version": 1,
  "benchmarks": {
    "record-selection": {
      "command": {
        "argv": ["python3", "tools/verify.py", "synthetic"],
        "timeout": 600,
        "env": {"PYTHONDONTWRITEBYTECODE": "1"}
      },
      "source_files": ["src/record_selection.py"],
      "workload_files": ["tools/verify.py"],
      "correctness_keys": ["output_digest", "result_count"],
      "metric_schema": {
        "p95_seconds": {"type": "number", "unit": "seconds", "direction": "lower"}
      },
      "primary_metric": "p95_seconds",
      "minimum_improvement_fraction": 0.4
    }
  }
}
```

The command writes one event to `ENDURANT_BENCHMARK_EVENT_PATH`. Its correctness and metric keys must exactly match the profile:

```json
{
  "schema_version": 1,
  "correctness": {"output_digest": "sha256:...", "result_count": 4002},
  "metrics": {"p95_seconds": 0.125}
}
```

```bash
python3 -S <skill>/scripts/endurant.py benchmark baseline record-selection \
  --repo . --profile-sha256 <sha256> --receipt /tmp/baseline.json
python3 -S <skill>/scripts/endurant.py benchmark final record-selection \
  --repo . --profile-sha256 <sha256> --baseline /tmp/baseline.json \
  --receipt /tmp/final.json
```

Receipts bind the tracked profile, command, explicit environment, workload files, independently observed source files, correctness values, metric schema, primary metric, and threshold. Final comparison requires identical workload and correctness, finite metrics, the declared improvement, and an unchanged proof diff. A receipt hash detects accidental mutation; it does not authenticate repository code. Keep ambient-environment and platform limits explicit.
