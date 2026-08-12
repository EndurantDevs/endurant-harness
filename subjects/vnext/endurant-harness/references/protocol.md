# Extended Protocol

Read this when the three-packet fast path is insufficient, a replan is required, or the task may cross a context boundary.

## Operating invariants

- Follow the nearest applicable repository instructions and user constraints.
- Preserve unrelated work. Never reset, clean, overwrite, or broadly reformat a dirty tree to simplify the task.
- Prefer repository evidence—behavior, tests, logs, code paths, configuration, and relevant history—over intuition.
- Separate **verified**, **inferred**, and **not verified** claims.
- Do not weaken tests, suppress errors, add retries, or expand scope merely to obtain green output.
- Scale proof to blast radius; do not trade safety for a packet-count target.

## Orient and decide

Before production edits:

1. Read applicable `AGENTS.md`, `CLAUDE.md`, or override files and inspect `git status` plus current diffs.
2. Identify package/workspace boundaries, generated files, required services, canonical commands, and known failures.
3. When an explicit path, URL, identifier, revision, or scope is supplied or already established, resolve it before broader listing or search; verify current identity and authority, then broaden when missing, ambiguous, or stale.
4. Find analogous implementation and test patterns before inventing new structure.
5. Define the observable goal, decisive proof, constraints, non-goals, and predicted change surface.
6. Trace `observation -> trigger/test/request -> entry point -> calls/state -> divergence` or establish the feature extension point.
7. State an evidence-linked hypothesis or design and one result that could disprove it.

Resolve reversible ambiguity from repository evidence and existing patterns. Stop for user input only when a choice is materially irreversible, unsafe, externally contractual, or impossible to infer reliably.

Put durable commands and invariants in host `AGENTS.md` or `CLAUDE.md`. Put harness-specific guidance in `.agents/endurant-harness-profile.md`; the probe loads that profile.

## Gates

### Write gate

Production edits require an observable contract, traced path or extension point, evidence-linked hypothesis/design, predicted surface, and proof plan. A reproduction test, diagnostic probe, or reversible experiment may precede the gate when it is the safest way to obtain evidence.

### Replan gate

Return to scan when:

- runtime evidence contradicts the hypothesis;
- the focused signal is unchanged after a patch;
- two attempts target the same explanation without improvement;
- touched components materially exceed the prediction;
- the apparent defect may be environment, permissions, cache, stale generated output, or flakiness;
- verification exposes data-loss, compatibility, security, concurrency, or rollout risk.

Remove speculative edits before pursuing a materially different explanation when doing so will not disturb user work.

### Adaptive replan loop

When the Replan gate trips, adapt the current software-development or authorized-action strategy; never rewrite the loaded Harness or its decisive oracle during the task.

1. Hold fixed the goal, decisive oracle, user authority, constraints, and protected invariants.
2. Name the terminal failed evidence and its causal mechanism, classify it, and mark agent behavior causal, contributing, or incidental. Inspect actual artifacts, external state, and tool results rather than trusting summaries or logical counters.
3. Propose at most three materially different, bounded strategy candidates targeting one evidenced mechanism: hypothesis/search, edit or action surface, tool/order, verification/delegation, or dry-run/canary/retry/rollback. Prefer reversible candidates; when rollback is impossible, require containment, drain, or forward recovery. Give each a predicted signal, stop condition, and recovery.
4. Apply the [parallel-agent contract](#parallel-agents); otherwise try the cheapest safe candidate first. Choose each agent's model and reasoning effort for its role and risk. Keep one shared-state write or external-mutation owner; other agents may mutate only assigned isolated copies/resources.
5. Judge every candidate with the original decisive oracle on comparable inputs and environment where practical. Diagnostics may supplement that oracle, never replace or weaken it.
6. Accept the leanest candidate meeting the target without protected-invariant regression or unacceptable whole-run time/cost. Otherwise reject it, restore candidate-local state when safe or drain to the declared safe state, and try the next candidate. Re-evaluate any combined winners.
7. Record accepted and rejected candidates with runner-observed, hash-bound same-oracle evidence when repository tooling provides it. Stop when the target is proven, three candidates fail, the budget ends, no harness-addressable mechanism remains, or more authority is required. An evidence-backed no-op is valid.

### Done gate

Demonstrate the requested behavior at the appropriate scope. A blocked check remains blocked: name the blocker and residual risk rather than silently substituting weaker evidence.

## Packet discipline

A packet is one model decision followed by all currently known non-conflicting operations. Start another only when evidence changes what should happen next. Before a command-heavy packet, state its purpose, known inputs, stop condition, and evidence to retain.

### Scan packet

Combine the repository probe, targeted symbol/call-site/test searches, bounded reads, analogous-pattern inspection, and baseline evidence. Retain only the contract, path, hypothesis/design, predicted surface, and decisive commands.

When several signals may share a cause, prefer one repository-native bounded diagnosis primitive that aggregates already-collected evidence before per-signal probes; expand incomplete or contradictory output into actual artifacts, external state, and tool results rather than trusting summaries or logical counters.

### Change packet

Make one coherent root-cause fix or vertical slice. Keep behavior-level coverage with production code when practical. Reinspect edited symbols and the diff. Avoid unrelated cleanup, broad formatting, speculative abstractions, compatibility layers, or feature flags unless the contract requires them. Regenerate generated files through their source tool rather than editing outputs directly.

### Proof packet

Put all known checks into one staged plan. Parallelize only commands that do not consume one another's output and cannot conflict through generated files, caches, databases, ports, services, or shared build directories. Serialize writes and resource-contending checks. Use an `always` stage for final diff evidence or cleanup.

Classify every failure before editing:

- product/code;
- expectation or test assumption;
- environment or dependency;
- permission or sandbox;
- flaky or timing-dependent;
- unrelated pre-existing failure.

## Output and process control

- Search by symbols and terms instead of recursive source dumps.
- Read relevant functions or line ranges, not entire large files.
- Run focused selectors before broad suites and confirm intended tests were discovered.
- Use bounded timeouts for tests, services, builds, and benchmarks.
- Keep full logs outside model context; retain command, exit code, and decisive lines.
- Avoid watch mode, interactive prompts, credential waits, and orphan processes.
- For a background service, use a bounded readiness check and guaranteed cleanup.
- Prefer argument arrays. Use reviewed shell execution only when unavoidable; never interpolate repository-controlled text.

## Parallel agents

Use parallel agents for independent, bounded investigation, isolated implementation variants, proof, or skeptical review only when available workers, provider limits, and required resources leave expected wall-time savings after queue and coordination; otherwise queue or serialize. Give each the goal, evidence, oracle, protected invariants, output, and stop condition. Choose model and reasoning effort explicitly: fast/lower effort for bounded lookup, stronger/higher effort for ambiguous or high-risk work and independent judging. Freeze them in controlled Harness comparisons.

Name each agent `<target>_<role>_<scope>` so its purpose is visible, for example `import_optimizer_transform` or `release_reviewer_receipts`; host-added hierarchy such as `/root/` is routing metadata, not part of the chosen name.

Keep one shared-state write or external-mutation owner; other agents may mutate only assigned isolated copies or worktrees with independent resources. The owner selects or integrates them and reruns combined proof. Require a conclusion, `path:symbol` or command evidence, uncertainty, and one recommended next check.

## Skeptical review

Before handoff, verify that:

- evidence connects the patch to the requested behavior;
- the change addresses cause rather than masking a symptom;
- the final surface matches the prediction;
- public behavior, data, failure paths, concurrency, security, and performance remain safe;
- tests prove behavior through the public path where practical;
- selected tests actually ran and caches did not hide a clean failure;
- rollout, rollback, migration, documentation, or operator notes are included when material.

## Version provenance

The marker loaded from `SKILL.md` is the only session-side version claim. Use `scripts/endurant.py provenance --loaded-provenance <release>:<full-package-sha256>` when an exact handoff or rollout needs it. For a forgiving handoff, the CLI also accepts the complete `endurant-provenance:<release>:<hash>` value or its HTML comment wrapper.

- `current` means the loaded release and full canonical package hash exactly match the package now on disk.
- `stale` means both loaded fields are valid but one differs.
- `unknown` means either field is missing or malformed, or current package integrity cannot be established.

For high-risk work, a `stale` or `unknown` result requires a fresh task; otherwise report the state in handoff.

Never infer that an active session reloaded because files changed, discovery refreshed, or a new task sees the update.
