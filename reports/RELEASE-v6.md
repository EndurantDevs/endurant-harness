# Endurant Harness v6 Release Evidence

Frozen: 2026-08-12

This record separates package validation, local-only task-level adaptive evidence, promotion evidence, host installation, remote CI, and active-session provenance.

## Release identity

| Item | Verified value |
| --- | --- |
| Release | `v6` |
| Canonical package SHA-256 | `05eecf7354d51bdc6eed3575fd1d11fa1c8f45e3b52672fbe79322e65374f611` |
| `SKILL.md` SHA-256 | `ae0aa5f38f9c1cd79177e085976c9b8f36857c66882749a259b224441c4511bf` |
| Deterministic ZIP SHA-256 | `cd20f29a4bc4068daf5789c60864b4be120c879f8523f0d62ba524597f4c1687` |
| ZIP size and members | `266092` bytes; `15` regular files under one `endurant-harness/` prefix |
| Carried runtime receipt SHA-256 | `f46dad9b326f0dba34ecd6265bc641bd9f73cad0a267200ab3983c01f04db443` |

The tracked source receipt is [`artifacts/benchmarks/v6-release.json`](../artifacts/benchmarks/v6-release.json). The ignored ZIP was rebuilt and verified byte-for-byte. The v5 runtime receipt is reused only because its three bound runtime inputs are unchanged; it proves CLI compatibility, not either adaptive loop.

## Shipped design

- A task-local adaptive replan loop for software development and authorized operations. It freezes the task goal, oracle, authority, invariants, exact failed state, candidate contracts, model/effort, and raw evidence; runs isolated variants in parallel only when worthwhile; and lets one owner replay the leanest proven result.
- A separate governed cross-task promotion loop. It mines recurring causal traces, freezes parent/evaluator/tools/budgets/data partitions, records proposal and evaluated-model lineage separately, gates with A/A and interleaved A/B evidence, re-evaluates winners, and permits one untouched audit. Promotion remains human-authorized.
- Parallel agents use meaningful `<target>_<role>_<scope>` names. Host-added hierarchy such as `/root/` remains routing metadata.
- Performance work decomposes phases, stops at the first successful optimization rung, reprofiles after every win, preserves exact correctness, and reports optimized-phase and whole-run gains separately.

## Local proof

- Skill Creator validation: **PASS** with normal Python; `-S` is intentionally not used because the validator imports PyYAML.
- Deterministic suite: **149/149 tests passed**.
- Strict skill audit: **PASS** with `37` capability cases, `22` schema-checked evaluation prompts, `12` trigger prompts, `14` runtime smoke cases, and a `450`-word `SKILL.md`.
- Controlled efficiency: **PASS**; instruction footprint `1398 -> 450` words (`3.107x` reduction), synthetic staged runner `4.968s -> 1.432s` (`3.470x`), and `12 -> 1` parent invocations. These are controlled proxies, not general task throughput.
- Historical evidence checker and provenance-efficiency receipt: **PASS**. Historical inputs are receipt-commit-bound and are not labeled current-v6 evidence.
- Canonical package and `subjects/vnext` mirror: byte-identical.
- Deterministic ZIP build, archive verification, source-only reconstruction, and staged local preflight: **PASS**.

## Task-local forward evidence

Fresh Codex processes received each synthetic task prompt plus frozen candidate-specific strategy context and isolated artifacts. Both final-input receipts reverified locally with zero errors. Their raw captures remain in ignored local artifact directories, so the hashes below are local evidence and cannot be independently reverified from a clean checkout.

| Case | Result | Candidate-phase evidence |
| --- | --- | --- |
| Software settings recovery | **PASS**; selected `explicit-condition`; one owner replayed the complete patch and reran the original/public/hidden proof | Both candidates passed and overlapped. Selected candidate: `42` changed lines, `58.172s`; alternate sentinel: `45` lines, `87.574s`. Receipt `c173a1db0ff86792a69d13fe8fe3fdc8595d110a04a6fa439d7584a3bbf779b7`. |
| Authorized resumable recovery | **PASS**; selected `checkpoint-resume`; one CAS-bound owner action preserved lineage, checkpoint, retry count, and source-fetch count | Resume: `9` changed lines, `17.897s`, pass. Restart: no mutation, `68.704s`, rejected by the unchanged oracle. Receipt `d08de543bdff0f4709603893aaf6c6f850b5d66c6a7e417acda8afbae3f2cbeb`. |

The timings above are candidate-agent phase durations. They are not whole-campaign or general speedups.

## Promotion evidence

The promotion evaluator has `14` focused adversarial tests covering replayed captures, incomplete starts, frozen-path escape, hash-chain rewriting, runtime drift, universal task failure, protected regression, causal-minimum attestation, no-op, lock contention, and verification errors overriding eligibility. No full live promotion campaign or untouched audit was run, so v6 does **not** claim a promotion-audited improvement.

## Host and release boundaries

- The installer test proved identical package bytes for disposable Codex and Claude targets, update rollback, unrelated-target refusal, and duplicate-location refusal.
- A live Codex task-level smoke is established locally by the two receipts above; its raw evidence is not shipped in the repository.
- A live Claude task smoke was attempted but the local Claude CLI returned an expired OAuth-token error before model execution. No Claude runtime-success claim is made; package/install parity remains verified.
- Remote CI applies only after this source is committed and pushed; it is not inferred from local preflight.
- No tag, GitHub release, deployment, or live-system mutation is claimed.
- Installing the package does not reload this already-running task.
