# Maintaining Endurant Harness

Read only while editing, packaging, or evaluating this skill.

## Release contract

A revision is releasable only when:

- the folder and frontmatter name are exactly `endurant-harness`;
- `SKILL.md` stays below the hard ceiling in `evals/efficiency-baseline.json`; the audit reports its softer target and keeps it at least 2.5x smaller than the v3 core;
- trigger and non-trigger boundaries remain explicit;
- the scan/change/prove gates and every capability in `evals/capability-contract.json` remains evidenced at its declared `core`, `conditional`, or `runtime` activation tier;
- detailed guidance stays conditional and one reference level deep;
- one canonical probe/runner (`scripts/endurant.py`) is used consistently by instructions, tests, and benchmarks;
- the unique `endurant-provenance` marker names the release and matches the bounded canonical package hash after marker normalization;
- shell execution remains opt-in, working directories remain contained, and command timeouts/process cleanup are tested;
- behavior, trigger, failure-path, capability-parity, and efficiency checks pass;
- the ZIP contains exactly one top-level `endurant-harness/` directory and no duplicate, README, changelog, cache, bytecode, backup, or temporary files.

## Dogfood loop

1. Snapshot the previous package and run its audit.
2. Define a falsifiable quality or efficiency hypothesis.
3. Probe the comparison workspace with `scripts/endurant.py probe`.
4. Make one coherent revision.
5. Run the strict audit and controlled benchmark:

```bash
python3 -S scripts/audit_skill.py . --strict --format text
python3 -S scripts/benchmark_efficiency.py . --baseline-skill /path/to/v3 --format text
```

6. Exercise symbol relevance/fallback, profile discovery, provenance states, optional preflight/benchmark contracts, behavior-evidence gating, passing, failing, timeout, skipped-stage, always-stage, parallel-stage, malformed-plan, shell-guard, and cwd-escape cases.
7. Run representative behavior and trigger cases against the revision and frozen baseline.
8. Keep the revision only when it improves verified outcome per token, model round, wall time, or human correction without lowering success or verification honesty.

Before policy adoption, pre-register gates, establish an A/A noise floor, run at least five interleaved pairs, and include one untouched holdout.

## Interpretation of controlled targets

The bundled benchmark is an instruction-footprint and parallel-runner wiring smoke. It does not establish universal end-to-end coding throughput. Validate real impact with repeated A/B repository tasks measuring success, regressions, turns, tokens, elapsed time, and human corrections.
