---
name: endurant-harness
description: Use for substantial repository changes needing executable proof. Do not use for explanations, reviews, trivial edits, or requests forbidding implementation or verification.
---

<!--endurant-provenance:v5:9dc80c3710b01d16384e3b636c9598e7da8cb4b8146f883e014d5820390d70f3-->

# Endurant Harness

## Invariants

- Follow host `AGENTS.md` or `CLAUDE.md`; inspect dirty tree; never reset, clean, overwrite, or reformat unrelated work.
- Prefer evidence; separate **verified**, **inferred**, and **not verified** claims.
- One write owner; subagents only for bounded reading.
- Never weaken tests, suppress errors, mask with retries, or trade safety for speed.

## Fast protocol: scan -> change -> prove

### Direct lane

Use direct only when behavior, target, focused proof, and reversible single-package scope are known. Allow at most two batched discovery commands: instructions/status/profile/symbol, then source/test. Behavior bugs require a failing regression before production edit. Contradictory evidence returns to scan before editing. Edit; run focused behavior, available local CI, and diff. Skip probe, hypothesis, analogy, checkpoint, JSON plan, subagent, and broad suites. Escalate for coupling, performance/efficiency, migrations, security, or deployment.

### 1. Scan uncertainty once

Run `python3 -S <skill>/scripts/endurant.py probe --repo <repo> --task "<task>"`. Before production edits, record goal, decisive proof, constraints, and non-goals; exact baseline or reproduction; traced path or extension point and an analogous pattern; hypothesis/design, disproof, predicted files, and checks.

Resolve reversible ambiguity; ask only about unsafe, irreversible, or contractual choices.

### 2. Change once

Implement the smallest coherent root-cause fix or vertical slice; cover behavior. Regenerate outputs through their source tool. Reinspect the path and diff.

Return to scan when evidence contradicts approach, signal stays unchanged, attempts repeat, scope grows, or environment/cache/flakiness is plausible.

### 3. Prove once

Select proof by task: focused behavior; follow [end-to-end performance/live recovery](references/risk.md#end-to-end-performance): identical before/after synthetic workload plus correctness, reprofile every optimization, and report phase/whole-run separately; available local CI before remote CI. Skip irrelevant synthetic work.

For escalated work, put checks in one staged plan and run `python3 -S <skill>/scripts/endurant.py run <plan> --repo <repo>`. Parallelize only independent commands; serialize shared state. Require behavior and final-diff proof. Confirm intended tests ran; reject zero-test or stale-cache greens. Services need bounded readiness and guaranteed cleanup.

Behavior evidence outranks integration, static checks, and reasoning. Blocked proof keeps the done gate closed.

## Efficiency budget

- Tiny invoked task: direct lane; skip probe and checkpoint.
- Uncertain: one scan/change/proof packet.
- Add packets only when evidence changes action.
- Keep summaries under 12 lines; full logs outside context.
- For replans, high risk, or context boundaries, use [references/checkpoint.md](references/checkpoint.md).

See [references/risk.md](references/risk.md), [references/protocol.md](references/protocol.md), [references/batch-plan.md](references/batch-plan.md), and [references/repository-profile.md](references/repository-profile.md).

## Handoff

Report **changed**, **why**, exact commands, residual risk, and compatibility/rollout notes. Label verified, inferred, and not verified; give conclusions, not a transcript. Provenance: `python3 -S <skill>/scripts/endurant.py provenance --loaded-provenance <release>:<hash>`; missing provenance means unknown.

When editing this skill, follow [references/maintenance.md](references/maintenance.md).
