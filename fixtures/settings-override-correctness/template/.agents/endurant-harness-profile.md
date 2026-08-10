# Endurant Harness Fixture Profile

## Canonical commands

- Focused behavior: `python3 scripts/verify.py focused`
- Affected scope: `python3 scripts/verify.py affected`
- Local CI preflight: `python3 scripts/verify.py ci-preflight`
- Synthetic benchmark, performance work only: `python3 scripts/verify.py synthetic`

## Verification selection

- Behavior changes require focused correctness and local CI preflight.
- Do not run the synthetic benchmark unless the task makes a performance claim.
