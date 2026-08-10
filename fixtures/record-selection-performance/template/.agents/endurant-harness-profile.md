# Endurant Harness Fixture Profile

## Canonical commands

- Focused behavior: `python3 scripts/verify.py focused`
- Synthetic benchmark: `python3 scripts/verify.py synthetic`
- Affected scope: `python3 scripts/verify.py affected`
- Local CI preflight: `python3 scripts/verify.py ci-preflight`

## Verification selection

- Performance changes require an unchanged-workload synthetic baseline before editing and the same benchmark after editing.
- Behavior and performance changes require focused correctness and local CI preflight after editing.
- Synthetic evidence never replaces correctness evidence.
