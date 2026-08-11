# Endurant Harness v5 Release Evidence

Frozen: 2026-08-11

This receipt describes the releasable source package and its local proof. Installation state, a pushed commit, remote CI, and any GitHub release are separate external gates and are not inferred here.

## Release identity

| Item | Verified value |
| --- | --- |
| Release | `v5` |
| Canonical package SHA-256 | `476218926c85e119277ae4e84465bfc483ae124c82133401bf79b9c4c6810818` |
| `SKILL.md` SHA-256 | `b778be5945e5d0305fb61562865fcaf1b0b55d56205aa0098c22bcd5c4790c5e` |
| Deterministic ZIP SHA-256 | `31335f66fcf53445d60d6a65294ba710b177dc993dddfa52a8492ba76f7d19cf` |
| ZIP size and members | `249791` bytes; `15` regular files under one `endurant-harness/` prefix |
| Runtime receipt SHA-256 | `ba77b06fcd5ce6ea73ebc0b337ac2323dacbbce38344b9be94be3bd62ea34a5d` |

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

- Staged preflight: **PASS** in `22.099s`.
- Deterministic suite: **99/99 tests passed**.
- Strict skill audit: **PASS** with `36` capability cases, `21` evaluation cases, `14` runtime smoke cases, and a `450`-word `SKILL.md`.
- Release source parity: installable `endurant-harness/` and `subjects/vnext/endurant-harness/` were byte-identical.
- Promotion evidence: historical, next-improvement, live-policy, probe, runner, and Rust-decision checks all passed.
- Release reconstruction: canonical package provenance, runtime receipt, member manifest, ZIP bytes, and archive hash all passed source-only verification.

The final runtime A/B used `31` alternating paired samples per surface plus `3` warmups. All semantic and exit gates passed. Median v5 overhead versus the promoted combined runtime was `9.025ms` for `template`, `11.704ms` for `probe`, and `10.952ms` for a no-op runner plan—below the `25ms` acceptance bound. This is accepted guard cost, not a runtime-speedup claim.

Security regressions cover output-regex deadlines, NUL input, SIGINT/SIGTERM descendant cleanup, existing/symlinked logs, benchmark receipt parent swaps, hard links, post-publication diff drift, stale contracts, and fail-closed descriptor-relative filesystem support.

## Boundaries

- No reuse license has been selected.
- No GitHub release asset is claimed.
- Remote CI is meaningful only for its exact pushed commit.
- Cross-platform distribution is not claimed; the tracked workflow provides Linux/Python compatibility evidence only after it runs successfully.
- An installed package hash does not prove that an already-active Codex task reloaded it. Missing loaded provenance remains `unknown`.
