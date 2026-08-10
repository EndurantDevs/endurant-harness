# Endurant Harness improvement decision

Date: 2026-08-10  
Upstream reviewed: `Ancienttwo/repo-harness` at `65eb3891783559c6c51d8c0bc6c537fa00c323c8` (`0.14.1`)  
Local installed Endurant Harness: read-only; not modified  
Repository status: local Git only; no remote or push

## Decision

Do not adopt repo-harness's full file-backed workflow for ordinary coding agents. Adopt its small, automatic safety/runtime mechanisms selectively.

| Part | Speed evidence | Quality evidence | Decision |
|---|---|---|---|
| Git-aware probe diet | Real aggregate root: `35.282s -> 0.099s`, 99.72% faster; output `3,954,698 -> 1,348` bytes. Routine scoped Git was `8ms` slower (5.7%); standalone non-Git was `9ms` slower (9.9%). The 31-repeat adversarial ignored/deleted fixture was `21ms` slower (14.0%) but remained below the 50ms absolute cap and emitted 14.9% less output. | Dogfooding found ignored benchmark fixtures leaking into project discovery. The retained fix excludes Git-ignored and absent index files while preserving tracked-ignored and visible-untracked project files. Regressions enforce the 1.5s silent-Git deadline and reap its child on interrupt. | **Adopt.** Routine probes remain below both guards; the adversarial quality case trades 21ms for correct inventory. |
| Clear-task direct lane | Two fresh paired repeats all passed. Median wall time fell `98.703s -> 50.221s` (49.12%); uncached input `24,385.5 -> 19,580.5` (19.70%); reasoning output `1,009.5 -> 534.5` (47.05%); first edit `39.034s -> 26.186s` (32.91%). | Both arms passed hidden behavior, mutation-adequate unit+CLI coverage, final-fingerprint proof, local CI, scope, Git-state, subject-tree, and event-order gates. | **Adopt behind the clear/reversible classifier.** Never classify a performance claim as direct. |
| Task-selected synthetic testing + local CI preflight | Ordinary runs skipped the deliberately irrelevant 2s benchmark. In the performance pair, combined used 45.47% fewer uncached tokens but was 23.29% slower in wall time. | Both performance arms ran unchanged-before/changed-after synthetic workloads, preserved duplicate/identity semantics, passed correctness/local CI, and improved p95 by more than 99.5%. | **Adopt policy for quality and CI efficiency.** The single performance pair does not prove a wall-time gain. |
| Bounded-tail output assertions | Disabled-path overhead was `+0.03ms` in the latest paired microbenchmark. | Exit-zero `Ran 0 tests` was rejected; `Ran 3 tests` was accepted. Matching is limited to the final 64KiB, isolated from the runner, and deadline-bounded against pathological regexes. | **Adopt as optional per-command fields.** |
| Plan proof deadline | Disabled-path timing was within noise (`-0.45ms`). | A hanging process tree stopped in `1.127s` under a 1s budget. Queued parallel work obeyed the same absolute deadline; SIGINT/SIGTERM killed children, skipped failure diagnostics, and still ran `always` cleanup. | **Adopt as optional plan budget.** |
| Diff fingerprint | Disabled/no-field overhead was `+3.59ms`; enabled overhead was `+130.39ms` on a deliberately tiny no-op plan. | It binds HEAD, index, tracked/untracked diff and content, rejects stale/proof-mutated state, converts Git I/O errors to proof failures, and still runs `always` cleanup. | **High-risk only**; do not require for ordinary work. |
| Python-to-Rust runtime rewrite | Exact template: saved 78ms median. Same scan kernel: saved 126ms. Deliberately optimistic one-scan + one-run upper bound: **0.212s**, only 0.44% of the 48s clear task and 0.18% of the 117s performance task. | Exact template and scan JSON parity passed, but full runner parity was not implemented or claimed. Initial release build observed about 3.25s; binary is 453KB. | **Reject rewrite.** Keep Python; fix algorithms and context volume. |
| Full repo-harness workflow | Its frozen pre-EPC Codex matrix reports the same 9/9 acceptance for all profiles, while known tokens rise `535,060 -> 1,775,916 -> 2,502,266` and average duration rises `32.299s -> 63.518s -> 80.530s`. | Strong file-backed continuity, diff freshness, bounded verifier, and evidence provenance, but substantial ceremony for the user's normal coding-agent goal. Upstream explicitly labels the matrix descriptive pre-EPC evidence, not a current improvement claim. | **Reject as Endurant's default workflow. Borrow mechanisms, not ceremony.** |

## Recommended Endurant vNext shape

1. **Direct lane:** instructions + dirty tree + direct path + predicted files/checks; one edit; focused behavior; task-selected local CI; diff. No probe, hypothesis document, analogy search, checkpoint, JSON plan, or subagent for clear reversible work.
2. **Escalated lane:** current scan/change/prove behavior for uncertainty, cross-package coupling, performance, migrations, security, deployment, or contradictory evidence.
3. **Verification selector:** focused -> synthetic only when decisive -> affected scope when blast radius requires it -> local CI preflight.
4. **Runner kernel:** optional `must_match`/`must_not_match`, optional proof deadline with guaranteed cleanup, and optional high-risk diff fingerprint.
5. **Probe:** detect an aggregate non-Git workspace, return bounded child Git repositories, and require the agent to rerun on one exact repo. Preserve full bounded discovery for standalone non-Git projects.

The local promotion gate is met. The combined candidate is ready for a controlled installed-skill canary after explicit approval; the installed skill remains untouched in this repository task.

## Evidence and limits

- Deterministic graders bind events to the exact run, require pre-edit and post-edit ordering, and bind final proof to the final source-and-test fingerprint.
- Agent runs require unchanged skill files including modes and symlink targets, unchanged staged Git index/HEAD, allowed diff scope, mutation-adequate regression tests, hidden functional acceptance, and a real local CI preflight.
- A fresh post-hardening combined smoke passed the final runner-observed ordering and targeted unit/CLI mutation grader in `60.165s` with `20,994` uncached input tokens. It is excluded from the paired speed comparison.
- Dogfooding the candidate in this development session exposed and fixed Git-ignored project-file noise. The new regression and benchmark gate preserve tracked-ignored and visible-untracked files, omit absent index entries, and stop a silent Git inventory at its deadline.
- A fresh final-candidate performance smoke selected an unchanged-source synthetic baseline before editing (`0.231281s` p95), repeated the identical workload after the change (`0.000791s`, 99.66% lower), and passed hidden semantics plus local CI in `81.943s` with `19,747` uncached input tokens. Runner-owned evidence confirms the exact two-file scope, unchanged skill tree/Git index, and untampered ordering. Its sanitized record is in `model-runs.json`; it is not mixed into the paired sample.
- Raw model runs are preserved under `artifacts/runs/` but intentionally Git-ignored. Sanitized summaries and deterministic benchmark JSON are pushable.
- The ordinary result is two paired repeats per arm on one synthetic task. It clears the local threshold but does not establish universal repository performance.
- The paired runs predate the final adversarial evaluator hardening. Their retained workspaces pass the final separate unit/CLI mutant grader; the fresh smoke above proves the complete current evaluator path.
- The performance result is one pair: verification selection succeeded, but candidate wall time was slower despite lower uncached-token use.
- The 449-word combined package passes all 31 capability cases, 15 eval cases, and 14 bundled runtime smokes; 24 added integrity/runner/probe tests also pass.
- The Rust spike is an upper-bound study, not a production-quality cross-platform port.

## Reproducible artifacts

- `artifacts/benchmarks/probe-diet.json`
- `artifacts/benchmarks/combined-probe.json`
- `artifacts/benchmarks/model-runs.json`
- `artifacts/benchmarks/runner-variants.json`
- `artifacts/benchmarks/rust-runtime.json`
- `lab/benchmark_probe.py`
- `lab/test_runner_variants.py`
- `lab/benchmark_rust_runtime.py`
- `lab/check_results.py`
- `lab/local-ci-plan.json`
- `lab/run_agent.py`
- `lab/summarize_model_runs.py`
- `lab/tests/test_combined_runner.py`
- `lab/tests/test_evaluation_integrity.py`
- `lab/tests/test_probe_inventory.py`
- `subjects/combined-candidate/endurant-harness/`

## Upstream source anchors

- Benchmark caveat and frozen profile table: <https://github.com/Ancienttwo/repo-harness/blob/65eb3891783559c6c51d8c0bc6c537fa00c323c8/docs/researches/20260723-epc-program-closeout.research.md#L186-L224>
- Diff-fingerprint implementation: <https://github.com/Ancienttwo/repo-harness/blob/65eb3891783559c6c51d8c0bc6c537fa00c323c8/src/effects/review/diff-fingerprint.ts>
- Bounded verifier/process-group cleanup: <https://github.com/Ancienttwo/repo-harness/blob/65eb3891783559c6c51d8c0bc6c537fa00c323c8/assets/templates/helpers/run-bounded-verifier-command.ts>
- Evidence-selection and hard wall-time budget contract: <https://github.com/Ancienttwo/repo-harness/blob/65eb3891783559c6c51d8c0bc6c537fa00c323c8/docs/reference-configs/sprint-contracts.md#L60-L106>
