---
name: endurant-harness
description: Use for substantial repository changes or difficult debugging across files or packages requiring executable proof. It reduces turns through bounded discovery, one change, and staged proof. Do not use for explanations, review-only work, trivial edits, or requests forbidding implementation or verification.
---

# Endurant Harness

Ship a correct change with the fewest evidence-changing turns.

## Invariants

- Follow `AGENTS.md` and constraints. Inspect the dirty tree; never reset, clean, overwrite, or broadly reformat unrelated work.
- Prefer repository evidence. Separate **verified**, **inferred**, and **not verified** claims.
- One write owner; subagents only for bounded reading.
- Never weaken tests, suppress errors, add retries, hide failures, or trade safety for packet count.

## Fast protocol: scan -> change -> prove

### 1. Scan once

Run `python3 -S <skill>/scripts/endurant.py probe --repo <repo> --task "<task>"`. Before production edits, record:

- goal, decisive proof, constraints, and non-goals;
- exact baseline or reproduction;
- traced path or extension point and an analogous pattern;
- hypothesis/design, disproof, predicted files, and checks.

Resolve reversible ambiguity from repository evidence. Ask only for unsafe, irreversible, or contractual choices.

### 2. Change once

Implement the smallest coherent root-cause fix or vertical slice, adding behavior coverage. Follow architecture; regenerate outputs through their source tool. Reinspect the path and diff.

Return to scan when evidence contradicts the hypothesis, the signal stays unchanged, two attempts repeat one explanation, the surface exceeds prediction, or environment/cache/flakiness is plausible. Remove speculative edits when safe.

### 3. Prove once

Put checks in one staged plan and run `python3 -S <skill>/scripts/endurant.py run <plan> --repo <repo>`. Parallelize only independent commands; serialize shared state. Require behavior proof for behavior changes, affected-scope, risk-specific, and final-diff checks. Confirm intended tests ran; reject zero-test or stale-cache greens. For services, use bounded readiness and guaranteed cleanup.

Classify failures as code, expectation, environment, permission, flaky, or pre-existing before editing. Behavior evidence outranks integration, static checks, and reasoning. Blocked required proof keeps the done gate closed.

## Efficiency budget

- Tiny invoked task: inspect, edit, focused check, diff; skip probe and checkpoint.
- Ordinary substantial task: one scan, one change, one proof packet.
- Add a packet only when evidence changes the action or exposes a blocker.
- Keep summaries under 12 decisive lines and full logs outside context.
- For replans, high risk, or context boundaries, use [references/checkpoint.md](references/checkpoint.md); retain gates, alternatives, user edits, and verification.

Use [references/risk.md](references/risk.md) for risky changes, [references/protocol.md](references/protocol.md) when needed, [references/batch-plan.md](references/batch-plan.md) for the runner schema, and [references/repository-profile.md](references/repository-profile.md) for durable commands and invariants.

## Handoff

Report **changed**, **why**, **verified** with exact commands, **not verified** with blockers and residual risk, plus compatibility/rollout notes. Label verified, inferred, and not verified evidence; give conclusions, not a transcript.

When editing this skill, follow [references/maintenance.md](references/maintenance.md).
