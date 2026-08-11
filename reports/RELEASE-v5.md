# Endurant Harness v5 Release Evidence

Frozen: 2026-08-11

This receipt describes the releasable source package and its local proof. Installation state, a pushed commit, remote CI, and any GitHub release are separate external gates and are not inferred here.

## Release identity

| Item | Verified value |
| --- | --- |
| Release | `v5` |
| Canonical package SHA-256 | `e2575e96c926c74f415a80a4eb06a80aa66e1546bdb5b6a6d5bde8ef5016595c` |
| `SKILL.md` SHA-256 | `f351d3fb6f591cfadecaf4d5db163c8bae0ce8e981c630037dea47ef30af2bd0` |
| Deterministic ZIP SHA-256 | `35381e68b3a50cfcd8f4984ee593efe7bebe0c3a91b2d0ecfae74cb7d8f16f71` |
| ZIP size and members | `252554` bytes; `15` regular files under one `endurant-harness/` prefix |
| Runtime receipt SHA-256 | `f46dad9b326f0dba34ecd6265bc641bd9f73cad0a267200ab3983c01f04db443` |

The tracked release receipt is [`artifacts/benchmarks/v5-release.json`](../artifacts/benchmarks/v5-release.json). CI reconstructs the archive in memory and checks the same hash; the local ZIP remains ignored under `dist/`.

## Adopted behavior

- Symbol-first probe relevance for snake_case and camelCase tasks, with deterministic source/test ranking and broad-search fallback.
- A direct-lane ceiling of two batched discovery commands, with escalation before editing when evidence is insufficient or contradictory.
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
- Strict skill audit: **PASS** with `36` capability cases, `21` schema-checked evaluation prompts, `12` schema-checked trigger prompts, `14` executed runtime smoke cases, and a `449`-word `SKILL.md` (under the `450` soft target and `500` hard maximum).
- Release source parity: installable `endurant-harness/` and `subjects/vnext/endurant-harness/` were byte-identical.
- Installed-tree parity and provenance: **PASS** for both Codex and Claude Code user paths; each is byte-identical to source and reports `current` for package `e2575e96c926...` when given the exact loaded marker.
- Promotion evidence: historical, next-improvement, live-policy, probe, runner, and Rust-decision checks all passed.
- Release reconstruction: canonical package provenance, runtime receipt, member manifest, ZIP bytes, and archive hash all passed source-only verification.

The final runtime A/B used `31` alternating paired samples per surface plus `3` warmups. All semantic and exit gates passed. Median v5 overhead versus the promoted combined runtime was `13.148ms` for `template`, `10.883ms` for `probe`, and `7.846ms` for a no-op runner plan—below the `25ms` acceptance bound. This is accepted guard cost, not a runtime-speedup claim.

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
