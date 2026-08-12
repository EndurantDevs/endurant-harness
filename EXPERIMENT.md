# Experiment Contract

## Question

Which Endurant Harness improvements measurably increase verified outcome per
token, tool call, and wall-clock second without making coding agents deliberate
or verify more than the task requires?

## Arms

1. `no-harness`: ordinary Codex behavior with no experimental workflow.
2. `current`: the frozen installed Endurant Harness.
3. One isolated arm for each proposed improvement.
4. Within an adaptive or promotion campaign, up to three materially distinct
   candidates for each evidenced mechanism.
5. `combined`: only improvements that pass their isolated gate.

## Proposed parts

| Part | Primary hypothesis | Disproof |
|---|---|---|
| Probe diet | Git-aware scope and hard budgets remove workspace noise and latency. | Scoped probes become slower, lose required files, or omit dirty-tree evidence. |
| Fast lane | A four-fact edit gate reduces time-to-first-edit and tokens on clear work. | Acceptance, scope fidelity, or proof honesty declines. |
| Verification selection | The task selects synthetic and local-CI checks only when evidence requires them. | Performance work skips its benchmark, or ordinary work runs unnecessary broad checks. |
| Runtime adaptive replan | When the original oracle stalls or contradicts an approach, isolated strategy variants recover more tasks without weakening proof or protected behavior. | Agents repeat the failed action, change the oracle, regress protected cases, or spend more without improving whole-run outcomes. |
| Governed promotion | Recurring causal mechanisms can improve the Harness through bounded parent-linked candidates and frozen evaluation. | Gains fall within noise, fail the untouched audit, depend on leaked graders, or regress any invariant. |
| Runner hardening | Total deadlines, output assertions, and subject binding reject false proof with little passing-path overhead. | Passing work slows materially or valid proof is rejected. |
| Rust runtime | A native hot-path runtime reduces cold-start and short probe/runner overhead enough to justify packaging and maintenance cost. | End-to-end task time is unchanged, algorithmic changes explain the win, or parity/portability regresses. |
| Provenance UX | One exact runnable command and forgiving marker parsing reduce provenance retries without changing coding quality. | Current receipts, functional acceptance, or command efficiency do not improve on a provenance-sensitive task. |

## Common controls

- Same fixture bytes and task prompt for every arm.
- Same evaluated Codex CLI version, model, reasoning effort, permissions, tools,
  budgets, and environment within each comparison.
- Candidate origin is explicit; automated proposal agents must record model and reasoning effort, while human-authored candidates must not impersonate one.
- Fresh disposable workspace and session for every run.
- No inherited task memory or repository-specific instructions.
- Frozen parent Harness, evaluator dependencies, fixtures, CLI version, and
  mining/development/audit partitions; the audit is copied and hash-sealed at
  campaign initialization, then used only after confirmation.
- Isolated candidate copies and resources; one owner integrates combined winners.
- Representative protected successes run with every candidate.
- Full raw output retained outside model context.
- Acceptance is determined by fixture commands and file-scope grading, not the
  agent's completion claim.

## Metrics

- acceptance and exact expected behavior;
- unexpected changed files and workflow artifacts;
- selected verification stages;
- baseline/result performance and variance;
- local CI preflight outcome;
- time to first edit and total elapsed time;
- input, cached-input, and output tokens when provider-authoritative;
- tool calls, repair loops, and failed commands;
- terminal failure class, repeated unchanged actions, candidate count, selected
  model/effort, rollback or recovery, and accepted/rejected/no-op decisions;
- original-oracle improvement, protected-case regressions, and merged-winner result;
- whole-run time, tokens/cost, and human corrections rather than phase-only gains;
- unsupported completion, stale proof, and zero-test acceptance.
- runtime cold-start, build time, binary size, and Python/Rust behavior parity.
- exact provenance state and the number of commands used to establish it.

## Execution order

1. Validate fixture determinism, grader failure paths, and protected successes.
2. Freeze the parent, evaluator dependencies, tools, budgets, environment, and all three data partitions; pre-seal the untouched audit.
3. Establish an A/A noise floor and mine recurring causal mechanisms from the mining partition.
4. Materialize at most three bounded parent-linked candidates per mechanism in isolated copies.
5. Run cheap validation gates, then at least five pre-registered interleaved A/B pairs against each parent on the development partition.
6. Accept only the leanest material gain with no invariant regression; a justified no-op is valid.
7. Re-evaluate merged winners under the frozen contract.
8. Freeze lineage, run the untouched audit once, and never tune on its result.
9. Write an adopt, revise, reject, or insufficient-evidence decision; promotion remains human-authorized.

Static/schema checks, task-evaluated development signals, and promotion-audited decisions are distinct evidence tiers. A started run without a complete runner-owned raw capture is permanently inconclusive for that campaign; it is never retried into the same comparison slot.
