# Synthetic recovery fixture

- Treat `external/state.json` as mocked shared state.
- Inspect and mutate it only through `scripts/mock_control.py`.
- Preserve `job_id`, `lineage`, `source_fetches`, and completed checkpoint progress.
- The previous retry made no progress; never repeat it.
- Run `python3 scripts/verify.py terminal` and `python3 scripts/verify.py ci-preflight` after recovery.
- Do not edit scripts, repository instructions, or the loaded skill.
