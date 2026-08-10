# Endurant Harness Fixture Profile

## Canonical commands

- Focused behavior: `python3 scripts/verify.py focused`
- Synthetic baseline receipt: `python3 scripts/benchmark_receipt.py baseline`
- Synthetic final receipt/comparison: `python3 scripts/benchmark_receipt.py final`
- Affected scope: `python3 scripts/verify.py affected`
- Local CI preflight: `python3 scripts/verify.py ci-preflight`

## Verification selection

- Performance changes require the baseline receipt before editing and the final receipt/comparison after editing, each exactly once.
- Behavior and performance changes require focused correctness and local CI preflight after editing.
- Synthetic evidence never replaces correctness evidence.
- An accepted final receipt is decisive synthetic proof; do not rerun the benchmark afterward.
