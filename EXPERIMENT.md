# Experiment Contract

## Question

Which Endurant Harness improvements measurably increase verified outcome per
token, tool call, and wall-clock second without making coding agents deliberate
or verify more than the task requires?

## Arms

1. `no-harness`: ordinary Codex behavior with no experimental workflow.
2. `current`: the frozen installed Endurant Harness.
3. One isolated arm for each proposed improvement.
4. `combined`: only improvements that pass their isolated gate.

## Proposed parts

| Part | Primary hypothesis | Disproof |
|---|---|---|
| Probe diet | Git-aware scope and hard budgets remove workspace noise and latency. | Scoped probes become slower, lose required files, or omit dirty-tree evidence. |
| Fast lane | A four-fact edit gate reduces time-to-first-edit and tokens on clear work. | Acceptance, scope fidelity, or proof honesty declines. |
| Verification selection | The task selects synthetic and local-CI checks only when evidence requires them. | Performance work skips its benchmark, or ordinary work runs unnecessary broad checks. |
| Runner hardening | Total deadlines, output assertions, and subject binding reject false proof with little passing-path overhead. | Passing work slows materially or valid proof is rejected. |
| Rust runtime | A native hot-path runtime reduces cold-start and short probe/runner overhead enough to justify packaging and maintenance cost. | End-to-end task time is unchanged, algorithmic changes explain the win, or parity/portability regresses. |

## Common controls

- Same fixture bytes and task prompt for every arm.
- Same Codex CLI version, model, reasoning effort, permissions, and environment.
- Fresh disposable workspace and session for every run.
- No inherited task memory or repository-specific instructions.
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
- unsupported completion, stale proof, and zero-test acceptance.
- runtime cold-start, build time, binary size, and Python/Rust behavior parity.

## Execution order

1. Validate fixture determinism and grader failure paths.
2. Freeze current-subject audit and microbenchmarks.
3. Test each implementation part independently.
4. Run one live smoke per arm and fixture.
5. Repeat only valid arms three times.
6. Write a per-part decision: adopt, revise, reject, or insufficient evidence.
