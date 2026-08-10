---
name: endurant-harness
description: Use for substantial repository changes needing executable proof. Do not use for explanations, reviews, trivial edits, or requests forbidding implementation or verification.
---

<!--endurant-provenance:v5:476218926c85e119277ae4e84465bfc483ae124c82133401bf79b9c4c6810818-->

# Endurant Harness

## Invariants

- Follow `AGENTS.md`; inspect the dirty tree; never reset, clean, overwrite, or reformat unrelated work.
- Prefer repository evidence. Separate **verified**, **inferred**, and **not verified** claims.
- One write owner; subagents only for bounded reading.
- Never weaken tests, suppress errors, add retries, hide failures, or trade safety for speed.

## Fast protocol: scan -> change -> prove

### Direct lane

For clear reversible work, allow at most two batched discovery commands: instructions/status/profile/symbol, then source/test. Behavior bugs require a failing regression before production edit. Escalate for uncertainty or contradiction before editing. Edit; run focused behavior, available local CI, and diff. Skip probe, hypothesis, analogy, checkpoint, JSON plan, subagent, and broad suites. Escalate for coupling, performance/efficiency, migrations, security, deployment, or material risk.

### 1. Scan uncertain work once

Run `python3 -S <skill>/scripts/endurant.py probe --repo <repo> --task "<task>"`. Before production edits, record goal, decisive proof, constraints, and non-goals; exact baseline or reproduction; traced path or extension point and an analogous pattern; hypothesis/design, disproof, predicted files, and checks.

Resolve reversible ambiguity from evidence. Ask only about unsafe, irreversible, or contractual choices.

### 2. Change once

Implement the smallest coherent root-cause fix or vertical slice with behavior coverage. Regenerate outputs through their source tool. Reinspect the path and diff.

Return to scan when evidence contradicts the approach, the signal stays unchanged, attempts repeat, scope grows, or environment/cache/flakiness is plausible.

### 3. Prove once

Select proof by task: focused behavior; identical before/after synthetic workload plus correctness for performance/efficiency; affected scope when material; available local CI before remote CI; diff. Skip irrelevant synthetic work for ordinary tasks.

For escalated work, put checks in one staged plan and run `python3 -S <skill>/scripts/endurant.py run <plan> --repo <repo>`. Parallelize only independent commands; serialize shared state. Require behavior and final-diff proof. Confirm intended tests ran; reject zero-test or stale-cache greens. Services need bounded readiness and guaranteed cleanup.

Classify failures as code, expectation, environment, permission, flaky, or pre-existing. Behavior evidence outranks integration, static checks, and reasoning. Blocked proof keeps the done gate closed.

## Efficiency budget

- Tiny invoked task: direct lane; skip probe and checkpoint.
- Uncertain: one scan/change/proof packet.
- Add packets only when evidence changes action.
- Keep summaries under 12 lines; full logs outside context.
- For replans, high risk, or context boundaries, use [references/checkpoint.md](references/checkpoint.md).

See [references/risk.md](references/risk.md), [references/protocol.md](references/protocol.md), [references/batch-plan.md](references/batch-plan.md), and [references/repository-profile.md](references/repository-profile.md).

## Handoff

Report **changed**, **why**, exact commands, residual risk, and compatibility/rollout notes. Label verified, inferred, and not verified; give conclusions, not a transcript. Missing provenance means unknown.

When editing this skill, follow [references/maintenance.md](references/maintenance.md).
