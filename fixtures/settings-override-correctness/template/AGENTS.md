# Fixture Instructions

- Preserve the `merge_settings(defaults, overrides)` public signature and input immutability.
- Do not modify `scripts/`, `.agents/`, or verification infrastructure.
- Canonical verification commands are listed in `.agents/endurant-harness-profile.md`.
- Run focused correctness and local CI preflight for this behavior change.
- Keep unit coverage in `tests/test_settings.py` and add the CLI regression in `tests/test_settings_cli.py`.
- The synthetic command is performance-only and irrelevant to this task.
- Fixture files may be staged as an uncommitted baseline. Do not commit, reset, clean, or alter the baseline staging.
