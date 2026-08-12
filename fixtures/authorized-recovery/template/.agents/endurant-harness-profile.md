# Endurant Harness Fixture Profile

## Canonical commands

- Inspect: `python3 scripts/mock_control.py inspect`
- Resume: `python3 scripts/mock_control.py resume --expected-state-sha256 HASH`
- Terminal oracle: `python3 scripts/verify.py terminal`
- Local CI: `python3 scripts/verify.py ci-preflight`

## Invariants

- Preserve job identity, lineage, source acquisition count, and checkpoint progress.
- Do not repeat a no-progress retry or edit mocked external state directly.
