# Endurant Harness v5 Release Evidence

Frozen: 2026-08-11

This receipt describes the releasable source package and its local proof. Installation state, a pushed commit, remote CI, and any GitHub release are separate external gates and are not inferred here.

## Release identity

| Item | Verified value |
| --- | --- |
| Release | `v5` |
| Canonical package SHA-256 | `cf8c818dfa13e0d853628bf407efdd70db6b128764f52481e208ee88b138342c` |
| `SKILL.md` SHA-256 | `255bbaf06f72991cb4e12198dfdc56f113a76e4aa6af7ffc519b04d1f719f330` |
| Deterministic ZIP SHA-256 | `7a82fb9974263633a755092ed1962e9c8ae8e009805937df2e19079ed8417bbf` |
| ZIP size and members | `250378` bytes; `15` regular files under one `endurant-harness/` prefix |
| Runtime receipt SHA-256 | `373dd25b704b97a8b4530c2e566927da3756bd08a9ecf621c54fdf154be61654` |

The tracked release receipt is [`artifacts/benchmarks/v5-release.json`](../artifacts/benchmarks/v5-release.json). CI reconstructs the archive in memory and checks the same hash; the local ZIP remains ignored under `dist/`.

## Adopted behavior

- Symbol-first probe relevance for snake_case and camelCase tasks, with deterministic source/test ranking and broad-search fallback.
- A direct-lane ceiling of two batched discovery commands, with escalation before editing when evidence is insufficient or contradictory.
- Red-before-green only for claimed behavior regressions.
- Optional, tracked, hash-pinned fast-preflight and benchmark contracts.
- Package/session provenance that reports `current`, `stale`, or `unknown` without inferring an active-task reload.
- Private per-run logs, bounded output assertions and deadlines, child-process cleanup, and guarded final-diff proof.

The long explicit lane allowlist and a Rust rewrite were rejected by the measured experiments. See [`DECISION.md`](DECISION.md) and [`NEXT-IMPROVEMENTS.md`](NEXT-IMPROVEMENTS.md).

## Local proof

- Staged preflight: **PASS** in `21.169s`.
- Deterministic suite at release: **99/99 tests passed**.
- Strict skill audit: **PASS** with `36` capability cases, `21` evaluation cases, `14` runtime smoke cases, and a `450`-word `SKILL.md`.
- Release source parity: installable `endurant-harness/` and `subjects/vnext/endurant-harness/` were byte-identical.
- Promotion evidence: historical, next-improvement, live-policy, probe, runner, and Rust-decision checks all passed.
- Release reconstruction: canonical package provenance, runtime receipt, member manifest, ZIP bytes, and archive hash all passed source-only verification.

The final runtime A/B used `31` alternating paired samples per surface plus `3` warmups. All semantic and exit gates passed. Median v5 overhead versus the promoted combined runtime was `9.641ms` for `template`, `10.078ms` for `probe`, and `11.793ms` for a no-op runner plan—below the `25ms` acceptance bound. This is accepted guard cost, not a runtime-speedup claim.

## Post-release provenance forward test

A two-pair ordinary-bug smoke compared the preceding `476218926c85...`
package with this exact `cf8c818dfa13...` package. Both arms passed focused,
local-CI, hidden-behavior, exact-scope, and failing-reproduction gates in `2/2`
runs. Exact `current` provenance improved from `1/2` to `2/2`, and median
provenance commands fell from `3` to `1`. Median wall time was `14.67%` lower
and uncached input was `31.04%` lower, but two pairs support only a favorable
exploratory signal—not a general speedup claim. The fail-closed receipt and
full boundary are in [`PROVENANCE-EFFICIENCY.md`](PROVENANCE-EFFICIENCY.md).
The expanded current deterministic suite passes **109/109** tests, including
ten fail-closed provenance receipt integrity cases.

Security regressions cover output-regex deadlines, NUL input, SIGINT/SIGTERM descendant cleanup, existing/symlinked logs, benchmark receipt parent swaps, hard links, post-publication diff drift, stale contracts, and fail-closed descriptor-relative filesystem support.

## Boundaries

- No reuse license has been selected.
- No GitHub release asset is claimed.
- Remote CI is meaningful only for its exact pushed commit.
- Cross-platform distribution is not claimed; the tracked workflow provides Linux/Python compatibility evidence only after it runs successfully.
- An installed package hash does not prove that an already-active Codex task reloaded it. Missing loaded provenance remains `unknown`.
