---
name: endurant-harness
description: Use for substantial repository changes requiring executable proof. Do not use for explanations, review-only work, trivial edits, or requests forbidding implementation or verification.
---

# Endurant Harness

## Invariants

- Follow `AGENTS.md` and constraints. Inspect the dirty tree; never reset, clean, overwrite, or broadly reformat unrelated work.
- Prefer repository evidence. Separate **verified**, **inferred**, and **not verified** claims.
- One write owner; subagents only for bounded reading.
- Never weaken tests, suppress errors, add retries, hide failures, or trade safety for speed.

## Fast protocol: scan -> change -> prove

### Direct lane

For clear, reversible work, inspect instructions, existing edits, the direct path, predicted files, and checks. Edit once; run focused behavior; inspect the diff. Skip probe, hypothesis, analogy search, checkpoint, JSON plan, subagent, and broad suites. Escalate for uncertainty, contradictions, coupling, performance claims, or material risk.

### 1. Scan uncertain work once

Run `python3 -S <skill>/scripts/endurant.py probe --repo <repo> --task "<task>"`. Before production edits, record goal, decisive proof, constraints, and non-goals; exact baseline or reproduction; traced path or extension point and an analogous pattern; hypothesis/design, disproof, predicted files, and checks.

Resolve reversible ambiguity from evidence. Ask only for unsafe, irreversible, or contractual choices.

### 2. Change once

Implement the smallest coherent root-cause fix or vertical slice with behavior coverage. Follow architecture; regenerate outputs through their source tool. Reinspect the path and diff.

Return to scan when evidence contradicts the approach, the signal stays unchanged, two attempts repeat one explanation, scope exceeds prediction, or environment/cache/flakiness is plausible.

### 3. Prove once

For the direct lane, run focused behavior and diff evidence. Otherwise put checks in one staged plan and run `python3 -S <skill>/scripts/endurant.py run <plan> --repo <repo>`. Parallelize only independent commands; serialize shared state. Require behavior, affected-scope, risk-specific, and final-diff proof. Confirm intended tests ran; reject zero-test or stale-cache greens. For services, use bounded readiness and guaranteed cleanup.

Classify failures as code, expectation, environment, permission, flaky, or pre-existing before editing. Behavior evidence outranks integration, static checks, and reasoning. Blocked proof keeps the done gate closed.

## Efficiency budget

- Tiny invoked task or clear reversible work: direct lane; skip probe and checkpoint.
- Uncertain substantial task: one scan, one change, one proof packet.
- Add a packet only when evidence changes action or exposes a blocker.
- Keep summaries under 12 decisive lines; keep full logs outside context.
- For replans, high risk, or context boundaries, use [references/checkpoint.md](references/checkpoint.md).

Use [references/risk.md](references/risk.md) for risk, [references/protocol.md](references/protocol.md) when needed, [references/batch-plan.md](references/batch-plan.md) for the runner, and [references/repository-profile.md](references/repository-profile.md) for durable commands.

## Handoff

Report **changed**, **why**, exact commands, blockers, residual risk, and compatibility/rollout notes. Label verified, inferred, and not verified evidence; give conclusions, not a transcript.

When editing this skill, follow [references/maintenance.md](references/maintenance.md).
