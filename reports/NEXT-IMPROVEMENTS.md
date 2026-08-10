# Endurant Harness next-improvement evaluation

Date: 2026-08-11  
Scope: local synthetic evaluation only  
Installed Endurant Harness: read-only; not modified  
Experiment-time remote actions: none; publication happened only after this evidence was frozen

## Decision

Promote two small defaults, add two conditional behaviors, pilot two optional
repository contracts, and reject the longer lane-classification prompt.

| Proposal | Measured speed | Measured quality | Decision |
|---|---|---|---|
| Symbol-first probe relevance | Full-probe median `78.614ms -> 76.007ms`; candidate-path payload `740 -> 87` bytes (88.24% smaller). | Source and test in the top three improved `1/12 -> 12/12` across Python, Rust, and TypeScript symbol tasks; unrelated harness-path noise fell `98.33% -> 0%`. | **Adopt.** Preserve exact snake_case/camelCase symbols, rank source/test hits, suppress unrelated embedded-harness paths, and retain the existing broad fallback. |
| Repository-owned fast preflight | In 31 paired repetitions, duplicate focused-plus-CI proof fell `295.892ms -> 149.000ms` (49.64%). | The prototype check set caught `6/6` focused, lint, type, build, generated-drift, and shared-package seeds; the resolver selected one trusted bundle plus one uncovered synthetic check; verified receipts rejected all six bad runs; no-profile resolution remained unchanged. | **Pilot, optional.** Useful when a repository's trusted preflight really subsumes focused checks. Do not add a global preflight or trust agent-authored coverage claims. |
| Synthetic benchmark receipt/comparator | Comparator construction and validation cost `0.147ms` median (`0.308ms` p95); receipt size was 959 bytes. One historical run contained a redundant final benchmark round worth an observed upper bound of 6.993s, but this is not a causal A/B estimate. | `8/8` argv, environment, workload, correctness, metric, threshold, source, and envelope mutants were rejected. A live repository-contract run executed one baseline and one final benchmark and passed all hidden/local-CI gates. | **Pilot the repository contract. Reject extra core prompt wording.** On the same fixture, the extra wording added 52.92% wall time and 13.68% uncached input with no quality gain. |
| Two-command direct-lane discovery budget | Two clear-task pairs: median wall `54.733s -> 39.268s` (28.26% lower), uncached input `27,466 -> 16,945.5` (38.30% lower), total commands `6 -> 4`, and pre-edit output `9,825 -> 5,614.5` bytes. | All four clear runs passed hidden behavior, mutation, CLI, scope, local-CI, Git, and skill-integrity gates. Ambiguous-symbol and conflicting-shared-contract canaries made no edits and escalated with concrete evidence. | **Adopt provisionally.** Batch discovery into at most two commands before a direct edit; if facts are insufficient or contradictory, leave the direct lane before editing. |
| Explicit lane-classification allowlist | Current policy median was `10.815s`; the 98-word allowlist was `12.485s` (15.44% slower) and used 0.91% more uncached input. | Both policies classified `80/80` cases correctly, including `40/40` hazardous escalations and `40/40` direct cases, with no tools. | **Reject.** Keep the concise current classifier; the extra words added cost without improving this matrix. |
| Conditional red-before-green | The honest red step increased median time to the first production edit `28.272s -> 35.347s` (25.02%). Overall wall time happened to fall 12.67%, but two runs are too few to attribute that to the policy. | Both bug runs added the regression first, observed it fail before production edits, then passed focused, mutation-adequacy, CLI, hidden, and local-CI gates. One feature and one refactor canary correctly made no pre-edit failing-test run. | **Adopt only for claimed behavior regressions.** Do not require it for features, refactors, or separately benchmarked performance work. |
| Version and session provenance | State evaluation cost `0.002459ms` median and at most 48 compact bytes. | `6/6` deterministic current, stale, missing, and tampered cases were classified correctly; missing provenance never became current. Active-session reload behavior was deliberately not claimed as tested. | **Adopt `current`/`stale`/`unknown` receipt semantics.** Require the release ID and full package hash supplied by the loaded instructions; otherwise report `unknown`. |

## Recommended vNext scope

1. Implement symbol-preserving probe ranking and its broad-search fallback.
2. Add the two-command discovery boundary to the direct lane.
3. Add compact package/session provenance with fail-closed `unknown` semantics.
4. Add the benchmark comparator as an optional repository-owned profile, without
   adding special core skill wording.
5. Add red-before-green only to the behavior-regression branch.
6. Pilot fast-preflight coverage metadata in one real repository before wider
   adoption.
7. Leave the current concise lane classifier unchanged.

This ordering keeps ordinary coding agents lightweight. Only the first three
items affect the common path, and each is automatic or bounded. The benchmark,
preflight, and red-first mechanisms activate only when the task or repository
contract calls for them.

## Test design and limits

- Live coding turns used fresh workspaces, the same low-effort model settings,
  web/network/memory/subagents disabled, and the installed Endurant skill
  disabled in favor of the exact subject variant.
- External graders checked hidden behavior, regression adequacy, local CI,
  allowed diff scope, Git HEAD/index preservation, skill-tree integrity, and
  runner-observed evidence ordering.
- Probe, preflight, receipt, and provenance results are deterministic local
  experiments. The live direct-budget and red-first comparisons use two clear
  or bug runs per arm; boundary and non-bug canaries use one run each.
- The fast-preflight experiment proves resolver, seeded-check, receipt, and
  duplicate-proof timing feasibility. It does not yet execute a production
  repository profile end to end; that is the purpose of the proposed pilot.
- Lane classification used two shuffled 40-case batches per arm. It tests the
  policy decision, not full coding behavior.
- The repository-comparator live comparison has one run per same-fixture arm;
  its historical wall-time comparison was not randomized. It supports a pilot,
  not a universal speed claim.
- No remote CI was run. Local preflight success is not remote-CI proof.

Codex discovers skill-file changes automatically, with restart as the fallback
when an update does not appear. However, the `AGENTS.md` instruction chain is
built once per run or TUI session. Therefore provenance must never infer that an
already-active task reloaded merely because files changed; resume it with an
explicit skill invocation or start a fresh run when exact version certainty is
required.

Official references:

- <https://learn.chatgpt.com/docs/build-skills>
- <https://learn.chatgpt.com/docs/agent-configuration/agents-md>

## Reproducible evidence

- `artifacts/benchmarks/next-improvements.json`: deterministic proposal results,
  source hashes, gates, and mutants.
- `artifacts/benchmarks/next-live.json`: sanitized live comparisons, raw-receipt
  hashes, source hashes, decisions, and limitations.
- `lab/benchmark_next_improvements.py`: deterministic benchmark driver.
- `lab/summarize_next_live.py`: live receipt sanitizer and metric recomputation.
- `lab/check_results.py`: fail-closed promotion checks for both artifacts.
- `lab/tests/test_next_improvements.py`: proposal behavior and malformed-input
  tests.
- `lab/tests/test_next_benchmark_integrity.py`: deterministic artifact mutation
  tests.
- `lab/tests/test_next_live_integrity.py`: live artifact mutation tests.
- `lab/live_policy/`: classification and boundary experiment runners and
  graders.
- `subjects/direct-budget/`, `subjects/red-before-green/`, and
  `subjects/benchmark-receipt/`: isolated skill variants.
- `fixtures/record-selection-receipt/`: optional repository benchmark contract.

Raw model JSONL, stderr/stdout, event sinks, and generated workspaces remain
Git-ignored under `artifacts/`.
