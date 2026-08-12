# Endurant Harness v7 Release Evidence

Frozen: 2026-08-12

This record separates package validation, mechanism measurements, fresh-agent
behavior, host installation, remote CI, and active-session provenance.

## Release identity

| Item | Verified value |
| --- | --- |
| Release | `v7` |
| Canonical package SHA-256 | `1665272cdeb7b2f7db4facac2ebeb30cfb8df044443d63534976508d2396b130` |
| `SKILL.md` SHA-256 | `f59ba00aea83bda834de9d7ad2526892faec4f02137e35afdff8ef81e13a22de` |
| Deterministic ZIP SHA-256 | `c6822077369d4866294673feb9e5834046739486387f6d36447f5c5717daeba0` |
| ZIP size and members | `269359` bytes; `15` regular files under one `endurant-harness/` prefix |
| Carried runtime receipt SHA-256 | `f46dad9b326f0dba34ecd6265bc641bd9f73cad0a267200ab3983c01f04db443` |

The tracked source receipt is
[`artifacts/benchmarks/v7-release.json`](../artifacts/benchmarks/v7-release.json).
The v5 runtime receipt is reused only because its three bound runtime inputs are
unchanged; it proves CLI compatibility, not v7 policy behavior.

## Shipped policy

- Resolve an explicit path, URL, identifier, revision, or scope before broader
  discovery, verify current identity and authority, and broaden missing, stale,
  or ambiguous results.
- When several signals may share a cause, aggregate already-collected evidence
  once with a bounded repository-native diagnostic before per-signal probes;
  incomplete or contradictory output expands to primary evidence.
- Use a current repository-native affected selector only when it can change the
  next costly or irreversible step. Shared, generated, schema, lockfile, or
  unknown dependency surfaces broaden, and final behavior/local-CI/diff proof
  remains unchanged.
- For unfamiliar typed external actions, inspect version-bound local schema or
  metadata and reject stale, unknown, duplicate, missing, or type-invalid
  targets before mutation. This is safety/context guidance, not a speed claim.
- Parallelize only when available workers, provider limits, required resources,
  queueing, and coordination still predict a wall-time benefit. One owner keeps
  shared or live mutation.

## Efficiency decisions

The detailed counts and limitations are in
[`NEXT-IMPROVEMENTS.md`](NEXT-IMPROVEMENTS.md#harness-skills-follow-up).

| Proposal | Decision |
| --- | --- |
| Aggregate-first diagnosis | Conditional adoption; adjacent aggregate evidence is large, but no agent-level speed claim. |
| Exact locator first | Extend the already adopted symbol-first rule to supplied paths, URLs, identifiers, revisions, and scopes. |
| Affected intermediate proof | Guarded adoption based on analogous preflight evidence; no arbitrary-selector speed claim. |
| Schema-first minimum payload | Reject as a speed optimization; retain only at unfamiliar typed mutation boundaries. |
| Capacity-aware parallelism | Insufficient speed evidence; retain only the measure-before-parallelizing guard. |

An exploratory 31-pair mechanism benchmark was not retained. It found that a
schema-first subprocess path reduced response bytes `4,490,958 -> 239` but made
whole time `42.51%` worse. Two capacity runs preserved eight tasks while
batching `8 -> 2` processes, but their gains crossed the A/A noise boundary in
opposite directions. The benchmark exceeded 1,100 lines with tests and did not
measure agent behavior, so keeping it would add more Harness than it justified.

## Local proof

- Skill Creator validation with normal Python: **PASS**.
- Deterministic suite: **149/149 tests passed** in `81.285s`.
- Strict skill audit: **PASS** with `37` capability cases, `23` schema-checked
  evaluation prompts, `12` trigger prompts, `14` runtime smoke cases, and a
  `450`-word `SKILL.md`.
- Controlled efficiency: **PASS**; instruction footprint `1398 -> 450` words
  (`3.107x` reduction), the synthetic staged runner exceeded the `3x` target,
  and parent invocations were `12 -> 1`. These remain controlled proxies; no
  tracked receipt binds one exact wall-time sample.
- Historical evidence checker and provenance-efficiency receipt: **PASS**.
- Canonical package and `subjects/vnext` mirror: byte-identical.
- Deterministic ZIP build and source-only verification: **PASS**.
- Exact staged local preflight: **PASS** in `65.589s`. An earlier run timed out
  the test command at `120s` under transient parallel host load; the same
  149-test suite had already passed alone, and one unchanged exact rerun passed
  all focused, promotion-evidence, release, diff, and status stages.

## Fresh-agent comparison

Two fresh `gpt-5.6-terra` agents at medium effort received the same raw,
read-only multi-symptom software/authorized-action scenario. One loaded frozen
v6 and one loaded v7; neither was told the intended decisions.

Both agents selected the current version-4 schema, refused duplicate-ID
mutation without a unique stable target, broadened stale affected scope,
serialized mutation, limited proof work to two concurrent jobs, and preserved
the final behavior/integration/diff oracle. The comparison therefore shows no
quality regression and no observed behavioral improvement. It recorded no
timing or token metrics and is not speed evidence.

## Boundaries

- This record covers source and local evaluation. Commit/push, remote CI, and
  installed-host bytes are separate rollout gates and are not inferred from
  this report; no tag or GitHub release is claimed.
- No live system was mutated.
- The currently running task did not reload v7 merely because the files changed.
- Agent-level speed remains unproven. A future claim requires the governed
  promotion contract: fixed model/tools/budget/environment, A/A noise, at least
  five interleaved A/B task pairs, protected successes, and whole-run metrics.
