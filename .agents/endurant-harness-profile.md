# Endurant Harness Project Profile

## Canonical commands

- Skill Creator validation: `skill_validator_dir="${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts"; PYTHONDONTWRITEBYTECODE=1 python3 "$skill_validator_dir/quick_validate.py" endurant-harness`
- Focused integrity: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s lab/tests -p 'test_*.py' -v`
- Adaptive lab: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest lab.tests.test_adaptive_replan lab.tests.test_promotion_campaign -v`
- Release audit: `PYTHONDONTWRITEBYTECODE=1 python3 -S endurant-harness/scripts/audit_skill.py endurant-harness --strict --format text`
- Efficiency benchmark: `PYTHONDONTWRITEBYTECODE=1 python3 -S endurant-harness/scripts/benchmark_efficiency.py endurant-harness --format text`
- Runner variants: `PYTHONDONTWRITEBYTECODE=1 python3 -S lab/test_runner_variants.py`
- Probe benchmark: `PYTHONDONTWRITEBYTECODE=1 python3 -S lab/benchmark_probe.py --candidate-subject combined-candidate --output-name combined-probe.json`
- Model summary: `PYTHONDONTWRITEBYTECODE=1 python3 -S lab/summarize_model_runs.py`
- Promotion checks: `PYTHONDONTWRITEBYTECODE=1 python3 -S lab/check_results.py`
- Provenance receipt: `PYTHONDONTWRITEBYTECODE=1 python3 -S lab/provenance_efficiency_receipt.py check`
- Release source verification: `PYTHONDONTWRITEBYTECODE=1 python3 -S lab/build_v5_release.py verify-source --package endurant-harness --receipt artifacts/benchmarks/v6-release.json --runtime-receipt artifacts/benchmarks/v5-runtime.json`
- Local preflight: `PYTHONDONTWRITEBYTECODE=1 python3 -S endurant-harness/scripts/endurant.py run lab/local-ci-plan.json --repo .`

## Invariants

- Commits, pushes, releases, installed-skill changes, and remote CI require explicit user authorization.
- Raw captures and generated workspaces remain ignored; only sanitized summaries are tracked.
- Live Codex adaptive/promotion campaigns are opt-in evidence runs, never ordinary CI.
- A local preflight is not evidence of remote CI or deployment.
