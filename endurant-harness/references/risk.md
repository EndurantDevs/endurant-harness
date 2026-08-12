# Risk-Based Verification

Repository instructions override this guide. Choose the smallest evidence set that proves the behavior, then broaden only for material blast radius.

## Contents

- [Task-selected local verification](#task-selected-local-verification)
- [End-to-end performance](#end-to-end-performance)
- [Evidence strength](#evidence-strength)
- [Change matrix](#change-matrix)
- [High-risk contract additions](#high-risk-contract-additions)
- [False-confidence checks](#false-confidence-checks)

## Task-selected local verification

Choose gates from the change, not a fixed checklist:

1. Run the narrowest focused behavior check.
2. For performance or efficiency work, measure an identical synthetic benchmark on unchanged baseline and changed source, while checking correctness. Do not substitute a new workload or self-reported timing.
3. Add affected-scope checks when the changed surface can influence neighboring behavior.
4. Run the repository's local CI preflight before paying the remote-CI feedback delay when one is available.

For ordinary correctness work, skip synthetic benchmarks unless the hypothesis, risk, or acceptance criteria make them decisive. A skipped irrelevant benchmark is an efficiency success, not missing proof.

For a claimed behavior regression, add or adjust the narrow regression first and observe it fail before the production edit, then fix and pass it. Do not manufacture a red step for features, internal refactors, or performance work unless their contract genuinely supplies a failing behavior.

Classify failures as code, expectation, environment, permission, flaky, or pre-existing. Fix code or expectation failures; repair or report environment/permission failures; rerun a flaky signal once, then isolate it; document pre-existing failures and prove them unchanged.

## End-to-end performance

Use the existing benchmark contract in [repository-profile.md](repository-profile.md) when present.

### 1. Decompose before editing

Measure each applicable phase separately:

- external I/O or acquisition;
- transformation/materialization/replay;
- integrity proof;
- commit/publication;
- cleanup.

Record wall time, throughput, CPU/wait or database-query share, actual reads/writes/refetches, and logical progress counters. Never assume a logical “rows written” counter means inserted or changed rows.

### 2. Use the performance ladder

Keep the fixture, indexes, concurrency, environment, and correctness oracle constant. Stop at the first rung meeting the target:

- unchanged baseline;
- delete redundant scans, writes, serialization, hashing, or callbacks;
- reuse an existing deferred/checkpoint/receipt/fast path;
- use an existing set-wise, database-native, or platform primitive;
- parallelize only the independently measured remaining bottleneck.

Do not build a subsystem when an earlier rung succeeds.

### 3. Pair speed with exact correctness

Every benchmark needs a production-path regression and applicable invariant checks:

- exact final content and membership;
- missing, extra, and tampered rows;
- crash/retry boundaries;
- lineage and completed-checkpoint reuse;
- stale cleanup;
- linked callbacks;
- publication behavior.

### 4. Treat receipts carefully

A write-time receipt may replace expensive recomputation only when:

- it is atomically bound to the exact durable write and proof version; and
- later drift is prevented or independently detected.

A receipt created before mutable rows can change is not proof of current state. Always include negative tamper, missing-row, and extra-row tests.

### 5. Reprofile after every win

Optimizing one phase moves the bottleneck. Measure the entire pipeline again before stopping. Use observational whole-pipeline profiling between wins; reserve the benchmark contract’s one baseline and one final invocation for acceptance. Report:

- baseline and final optimized-phase time/throughput;
- baseline and final whole-run time/throughput.

A phase speedup does not determine whole-run speedup; measure both. Other changes can produce higher or lower speedup, corresponding to lower or higher elapsed time.

### 6. Prove live releases in order

When deployment/recovery is authorized, require:

```text
exact-head CI
-> exact post-merge CI
-> source-linked deployment
-> deployed digest and baked revision
-> readiness
-> controlled cancellation and terminal drain
-> lineage-preserving retry
-> no unintended refetch/rewrite and preserved checkpoints
-> measured live throughput
-> terminal integrity/publication proof.
```

Do not promote an earlier gate into runtime proof.

For an authorized operational replan, inspect exact external state before another mutation and define stop, rollback, or drain first. Bind mutation to that observed state with a native conditional update or exact state hash. Never repeat an unchanged failed action. Parallel agents may diagnose or compare recovery variants, but one owner mutates shared or live state. Prefer dry-run, canary, or checkpoint/resume; preserve lineage and the terminal readiness, integrity, or publication oracle.

## Evidence strength

Prefer, in order:

1. automated behavior-level acceptance or regression tests;
2. reproducible commands with observable before/after output;
3. integration, end-to-end, migration, race, security, or benchmark evidence;
4. static checks, typecheck, lint, and build;
5. direct code-path reasoning.

Lower-ranked evidence may support a conclusion but is not equivalent to behavior proof.

In runner plans, tag commands as `behavior`, `integration`, `static`, `diagnostic`, `diff`, `cleanup`, or `other`. Set `require_behavior_evidence` for behavior-changing work so a static-only green cannot close the done gate.

## Change matrix

| Change | Minimum decisive evidence | Add when material |
|---|---|---|
| Bug | baseline/reproduction, behavior regression, focused checks | affected package and integration/public path |
| Feature | success and invalid-input behavior, focused checks | API/docs compatibility, E2E, security/performance |
| Refactor | same behavior before and after | package suite, snapshots/public API, benchmark |
| Persisted data/schema | forward path, mixed-state behavior, idempotency | rollback, partial failure, old/new reader-writer compatibility, data validation |
| Dependency | build/typecheck, affected tests, API and lockfile review | advisory review, runtime smoke, full suite |
| Concurrency | deterministic regression where possible, race/thread tooling | stress repeats, cancellation, deadlock and resource cleanup |
| Performance | same-workload baseline/result and correctness | variance, production-like load, memory/resource limits |
| Security | failing abuse case, authorization boundary, secret/log review | threat review, dependency scan, tenant isolation |
| UI | component behavior and build/typecheck | browser/E2E, visual, accessibility, responsive states |
| Deployment/config | parse/validate and dry run/plan | staging smoke, permissions, rollback, environment parity |

## High-risk contract additions

For public APIs, persisted data, auth, payments, shared infrastructure, security, deployment, or broad migrations, explicitly record:

- invariant and highest-cost failure mode;
- compatibility window and supported versions;
- partial-rollout behavior and old/new interoperability;
- failure, rollback, and destructive/irreversible operations;
- observability needed to detect a bad rollout;
- checks unavailable locally and their exact residual risk.

## False-confidence checks

- Did the intended test run rather than select zero cases?
- Did cache or incremental output hide a clean failure?
- Did proof exercise the public entry point rather than only a helper?
- Are mixed-version, failure, rollback, cancellation, and cleanup paths covered where material?
- Does the final diff match the predicted surface?
- Are blocked checks named instead of being presented as passed?
