# Endurant Harness Project Profile

## Canonical commands

- Focused integrity: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s lab/tests -p 'test_*.py' -v`
- Release audit: `PYTHONDONTWRITEBYTECODE=1 python3 -S endurant-harness/scripts/audit_skill.py endurant-harness --strict --format text`
- Runner variants: `PYTHONDONTWRITEBYTECODE=1 python3 -S lab/test_runner_variants.py`
- Probe benchmark: `PYTHONDONTWRITEBYTECODE=1 python3 -S lab/benchmark_probe.py --candidate-subject combined-candidate --output-name combined-probe.json`
- Model summary: `PYTHONDONTWRITEBYTECODE=1 python3 -S lab/summarize_model_runs.py`
- Promotion checks: `PYTHONDONTWRITEBYTECODE=1 python3 -S lab/check_results.py`
- Local preflight: `PYTHONDONTWRITEBYTECODE=1 python3 -S endurant-harness/scripts/endurant.py run lab/local-ci-plan.json --repo .`

## Invariants

- Commits, pushes, releases, installed-skill changes, and remote CI require explicit user authorization.
- Raw captures and generated workspaces remain ignored; only sanitized summaries are tracked.
- A local preflight is not evidence of remote CI or deployment.
