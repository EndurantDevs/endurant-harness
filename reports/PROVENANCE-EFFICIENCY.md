# Endurant Harness provenance UX forward test

Date: 2026-08-11  
Scope: two paired local Codex runs on one synthetic ordinary bug task

## Decision

Retain the v5 provenance UX first shipped in package
`cf8c818dfa13e0d853628bf407efdd70db6b128764f52481e208ee88b138342c`.
It produced an exact `current` receipt in both runs with one provenance command
per run. The preceding package produced `current` in one of two runs and used
three provenance commands per run at the median.

The timing and token results are a favorable exploratory signal, not a general
speed claim. Two pairs on one provenance-sensitive task are insufficient to
separate the UX change from normal model variance.

## Result

| Metric | Previous package | Measured provenance-UX package | Observed change |
| --- | ---: | ---: | ---: |
| Wall time, median | `71.525s` | `61.033s` | `-14.67%` |
| Uncached input, median | `28,451` | `19,621` | `-31.04%` |
| Commands, median | `7.0` | `5.5` | `-21.43%` |
| Pre-production commands, median | `4.0` | `3.0` | `-25.00%` |
| Command output, median | `14,084.5` bytes | `10,546.5` bytes | `-25.12%` |
| First production edit, median | `43.282s` | `40.784s` | `-5.77%` |
| Provenance commands, median | `3.0` | `1.0` | `-66.67%` |

Both crossover pairs favored the current package for wall time and uncached
input. Pairwise wall changes were `-3.79%` and `-23.70%`; uncached-input
changes were `-32.32%` and `-30.20%`.

## Quality gates

| Gate | Previous package | Measured provenance-UX package |
| --- | ---: | ---: |
| Functional focused, local-CI, hidden, and diff checks | `2/2` | `2/2` |
| Exact three-file scope | `2/2` | `2/2` |
| Failing behavior reproduction before production edit | `2/2` | `2/2` |
| Irrelevant synthetic benchmark skipped | `2/2` | `2/2` |
| Exact `current` provenance receipt | `1/2` | `2/2` |
| Overall acceptance including provenance | `1/2` | `2/2` |

The previous arm's failed acceptance was provenance-only: its code, regression
coverage, exact scope, hidden semantics, and local CI still passed.

## Controls

- Same byte-identical settings fixture and prompt for all four runs.
- Fresh staged Git workspace and ephemeral Codex task for each run.
- `gpt-5.6-terra`, low reasoning effort and verbosity.
- Project-scoped skill copies; the globally installed skill was disabled.
- Network, web search, memories, history persistence, and subagents disabled.
- Alternating order: previous, current, current, previous.
- External acceptance from fixture verification and mutation-aware hidden
  grading, not the agent's completion message.

The measured package hashes are exact. The checker reconstructs the measured
`cf8c818dfa13...` package from Git commit `5c69aee81fbfea6b135270f8c2e52f925fc39e6b`,
then applies the tracked reverse patch to reconstruct the previous
`476218926c85...` package. Each reconstructed subject's own provenance command
confirms its embedded marker and computed canonical package hash.

## Evidence and verification

Tracked evidence:

- [`artifacts/benchmarks/provenance-efficiency-ab.json`](../artifacts/benchmarks/provenance-efficiency-ab.json)
- [`lab/provenance_efficiency_receipt.py`](../lab/provenance_efficiency_receipt.py)
- [`lab/tests/test_provenance_efficiency_receipt.py`](../lab/tests/test_provenance_efficiency_receipt.py)
- [`lab/prompts/provenance-efficiency.txt`](../lab/prompts/provenance-efficiency.txt)
- [`lab/baselines/v5-provenance-ux.patch`](../lab/baselines/v5-provenance-ux.patch)

Verify the sanitized receipt and reconstruct both package identities:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -S \
  lab/provenance_efficiency_receipt.py check
```

The ignored raw captures are retained locally under
`artifacts/runtime/provenance-efficiency-ab-20260811/`. Their byte counts and
SHA-256 receipts are tracked, but raw JSONL, workspaces, agent text, and full
logs are intentionally not committed. On the originating machine, add
`--raw-root artifacts/runtime/provenance-efficiency-ab-20260811` to verify them.

The one-off execution driver is identified by SHA-256 in the receipt but is not
published because its source contained machine-local paths. The tracked code
reconstructs both package identities, validates the exact frozen sanitized run
rows, and recomputes every aggregate; it does not claim to reproduce the raw
agent execution from a public clone.

## Boundary

This result supports the provenance UX decision and the exact measured
`cf8c818dfa13...` subject. It is historical evidence, not a speed measurement
of later package hashes. A general speed conclusion would need at least four
new pairs plus a comparable task whose prompt does not exercise provenance.
