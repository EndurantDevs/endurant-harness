# Endurant Harness v5 Release Evidence

Frozen: 2026-08-11

This receipt describes the releasable source package and its local proof. Installation state, a pushed commit, remote CI, and any GitHub release are separate external gates and are not inferred here.

## Release identity

| Item | Verified value |
| --- | --- |
| Release | `v5` |
| Canonical package SHA-256 | `3c534285f59042a2512a172f3a8e2a96205b0b037217e82e5deb6fc8a4292bc3` |
| `SKILL.md` SHA-256 | `cbb7bc38c0faf9b941b1c9d8922006851dd5fcd61f18062b6b62b89d7235b99d` |
| Deterministic ZIP SHA-256 | `67b4629bec62ad10ecd4a4ec44a5d8e2c83b96d399490e230101458d54b32532` |
| ZIP size and members | `264726` bytes; `15` regular files under one `endurant-harness/` prefix |
| Runtime receipt SHA-256 | `f46dad9b326f0dba34ecd6265bc641bd9f73cad0a267200ab3983c01f04db443` |

The tracked release receipt is [`artifacts/benchmarks/v5-release.json`](../artifacts/benchmarks/v5-release.json). CI reconstructs the archive in memory and checks the same hash; the local ZIP remains ignored under `dist/`.

## Adopted behavior

- Symbol-first probe relevance for snake_case and camelCase tasks, with deterministic source/test ranking and broad-search fallback.
- A direct-lane ceiling of two batched discovery commands, with escalation before editing when evidence is insufficient or contradictory.
- A task-local adaptive replan loop that holds the goal and original oracle fixed, tries at most three materially different strategies with role-appropriate model/effort, parallelizes only isolated candidates when worthwhile, and gives shared-state mutation to one owner.
- A governed cross-task promotion loop with frozen parent/evaluation controls, protected successes, parent-linked candidates, merged-winner re-evaluation, one untouched audit, and human-authorized installation or publication.
- Red-before-green only for claimed behavior regressions.
- Optional, tracked, hash-pinned fast-preflight and benchmark contracts.
- Package/session provenance that reports `current`, `stale`, or `unknown` without inferring an active-task reload.
- Private per-run logs, bounded output assertions and deadlines, child-process cleanup, and guarded final-diff proof.
- Stable fingerprints for outside-pointing untracked symlinks, structured invalid-`cwd` failures that preserve teardown, and snake_case broad-search fallback.
- One audited Agent Skills package for Codex and Claude Code, with a guarded one-line installer and update path.

The long explicit lane allowlist and a Rust rewrite were rejected by the measured experiments. See [`DECISION.md`](DECISION.md) and [`NEXT-IMPROVEMENTS.md`](NEXT-IMPROVEMENTS.md).

## Local proof

- Staged preflight: **PASS**.
- Deterministic suite at release: **118/118 tests passed**.
- Strict skill audit: **PASS** with `37` capability cases, `22` schema-checked evaluation prompts, `12` schema-checked trigger prompts, `14` executed runtime smoke cases, and a `450`-word `SKILL.md` (meeting the `450` soft target and below the `500` hard maximum).
- Release source parity: installable `endurant-harness/` and `subjects/vnext/endurant-harness/` were byte-identical.
- Installed-tree parity and provenance: **PASS** for both Codex and Claude Code user paths; each is byte-identical to source and reports `current` for package `3c534285f590...` when given the exact loaded marker.
- Pre-adaptive promotion evidence: historical, next-improvement, live-policy, probe, runner, and Rust-decision checks all passed. The adaptive loops currently have strict capability and schema-specification coverage, not a completed controlled A/B campaign.
- Release reconstruction: canonical package provenance, runtime receipt, member manifest, ZIP bytes, and archive hash all passed source-only verification.

The pre-adaptive runtime A/B used `31` alternating paired samples per surface plus `3` warmups. All semantic and exit gates passed. Median v5 overhead versus the promoted combined runtime was `13.148ms` for `template`, `10.883ms` for `probe`, and `7.846ms` for a no-op runner plan—below the `25ms` acceptance bound. This is accepted CLI guard cost, not a runtime-speedup claim or evidence for either adaptive loop.

## Post-release provenance forward test

A two-pair ordinary-bug smoke compared the preceding `476218926c85...`
package with the measured `cf8c818dfa13...` predecessor to this package. Both arms passed focused,
local-CI, hidden-behavior, exact-scope, and failing-reproduction gates in `2/2`
runs. Exact `current` provenance improved from `1/2` to `2/2`, and median
provenance commands fell from `3` to `1`. Median wall time was `14.67%` lower
and uncached input was `31.04%` lower, but two pairs support only a favorable
exploratory signal—not a general speedup claim. The fail-closed receipt and
full boundary are in [`PROVENANCE-EFFICIENCY.md`](PROVENANCE-EFFICIENCY.md).
The expanded current deterministic suite passes **118/118** tests, including
twelve fail-closed provenance receipt integrity cases.

Security regressions cover output-regex deadlines, NUL input, SIGINT/SIGTERM descendant cleanup, existing/symlinked logs, benchmark receipt parent swaps, hard links, post-publication diff drift, stale contracts, and fail-closed descriptor-relative filesystem support.

## Boundaries

- No reuse license has been selected.
- No GitHub release asset is claimed.
- Remote CI is meaningful only for its exact pushed commit.
- Cross-platform distribution is not claimed; the tracked workflow provides Linux/Python compatibility evidence only after it runs successfully.
- An installed package hash does not prove that an already-active host task reloaded it. Missing loaded provenance remains `unknown`.
