# Endurant Harness Evaluation Lab

This directory evaluates proposed Endurant Harness improvements without modifying
the installed skill or any production repository.

The lab compares the current harness with isolated experimental variants on
neutral, executable synthetic coding tasks. Each variant must earn adoption by
improving a declared speed or quality metric without reducing acceptance,
scope fidelity, verification honesty, or local-CI preflight coverage.

## Safety and publication boundary

- The installed skill at `$CODEX_HOME/skills/endurant-harness` is read-only.
- Fixtures contain synthetic technical identifiers only.
- Generated workspaces, Codex homes, raw JSONL, and full command logs stay under
  `artifacts/` and are ignored by default.
- Local commits preserve the experiment history. No remote, push, pull request,
  or remote CI action is configured or authorized.
- A local CI preflight is reported separately from actual remote CI.

## Subjects

- `subjects/current/endurant-harness/`: frozen current baseline.
- `subjects/<variant>/endurant-harness/`: isolated candidate subjects, one
  improvement per variant.
- `subjects/combined-candidate/endurant-harness/`: only the parts that passed
  their isolated gates.

## Fixtures

- `record-selection-performance`: requires the task to select correctness
  checks, an identical before/after synthetic benchmark, and local CI preflight.
- `settings-override-correctness`: requires focused and local-CI regression
  proof while skipping an available but irrelevant synthetic benchmark.

## Promotion policy

An improvement is worth considering only when repeated runs show:

1. no acceptance, scope, safety, or verification-honesty regression;
2. its intended decision is selected without extra user interaction;
3. a material benefit in its declared metric;
4. no unrelated workflow artifact or full-suite expansion for ordinary work.

The initial materiality target is a 15% reduction in median elapsed time or
uncached tokens. A high-confidence fast-lane claim should reach 20%.

The Rust experiment is split from the probe algorithm experiment. A
behavior-equivalent Rust runtime must beat the current Python runtime before a
Rust implementation is combined with any probe-diet changes.

The combined candidate met the local promotion gate. Two fresh ordinary-task
pairs passed with a 49.1% median wall-time reduction and 19.7% fewer uncached
tokens. The performance task correctly selected before/after synthetic proof;
its single pair is quality evidence, not a universal wall-time claim. A separate
post-hardening smoke passed the final evaluator; it is not mixed into the paired
timing sample. Dogfooding that candidate then found and fixed Git-ignored
fixture noise, stale deleted manifests, and silent-Git timeout/interrupt leaks in
scoped project discovery while keeping routine probes within the guard. A fresh
performance canary also selected before/after synthetic proof and local CI,
passed the runner-owned hidden grader, and reduced its fixture p95 by 99.66%.

## Local reproduction

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s lab/tests -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -S subjects/combined-candidate/endurant-harness/scripts/audit_skill.py subjects/combined-candidate/endurant-harness --strict --format text
PYTHONDONTWRITEBYTECODE=1 python3 -S lab/test_runner_variants.py
PYTHONDONTWRITEBYTECODE=1 python3 -S lab/benchmark_probe.py --candidate-subject combined-candidate --output-name combined-probe.json
PYTHONDONTWRITEBYTECODE=1 python3 -S lab/summarize_model_runs.py
PYTHONDONTWRITEBYTECODE=1 python3 -S lab/check_results.py
PYTHONDONTWRITEBYTECODE=1 python3 -S subjects/combined-candidate/endurant-harness/scripts/endurant.py run lab/local-ci-plan.json --repo .
```

The measured recommendation and component-by-component decision matrix are in
[`reports/DECISION.md`](reports/DECISION.md).
