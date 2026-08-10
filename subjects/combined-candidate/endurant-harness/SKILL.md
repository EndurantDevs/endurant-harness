---
name: endurant-harness
description: Use for substantial repository changes requiring executable proof. Do not use for explanations, review-only work, trivial edits, or requests forbidding implementation or verification.
---

# Endurant Harness

## Invariants

- Follow `AGENTS.md`; inspect the dirty tree; never reset, clean, overwrite, or reformat unrelated work.
- Prefer repository evidence. Separate **verified**, **inferred**, and **not verified** claims.
- One write owner; subagents only for bounded reading.
- Never weaken tests, suppress errors, add retries, hide failures, or trade safety for speed.

## Fast protocol: scan -> change -> prove

### Direct lane

For clear reversible work, inspect instructions, dirty edits, direct path, predicted files, and checks. Edit once; run focused behavior, available local CI, and diff. Skip probe, hypothesis, analogy, checkpoint, JSON plan, subagent, and broad suites. Escalate for uncertainty, contradictions, coupling, performance/efficiency, migrations, security, deployment, or material risk.

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
- Uncertain work: one scan, change, and proof packet.
- Add a packet only when evidence changes action.
- Keep summaries under 12 lines and full logs outside context.
- For replans, high risk, or context boundaries, use [references/checkpoint.md](references/checkpoint.md).

Use [references/risk.md](references/risk.md) for risk, [references/protocol.md](references/protocol.md) when needed, [references/batch-plan.md](references/batch-plan.md) for the runner, and [references/repository-profile.md](references/repository-profile.md) for durable commands.

## Handoff

Report **changed**, **why**, exact commands, residual risk, and compatibility/rollout notes. Label verified, inferred, and not verified; give conclusions, not a transcript.

When editing this skill, follow [references/maintenance.md](references/maintenance.md).
