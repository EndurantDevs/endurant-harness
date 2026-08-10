# Fixture Instructions

- Preserve the public `select_records(records, requested_ids)` signature and exact semantics.
- Inputs are reusable sequences; every record has a string `id`, and every requested ID is a string.
- Do not modify `scripts/`, `.agents/`, or verification infrastructure.
- Canonical verification commands are listed in `.agents/endurant-harness-profile.md`.
- A performance claim requires the identical synthetic workload before and after the edit, followed by correctness and local CI preflight.
- Fixture files may be staged as an uncommitted baseline. Do not commit, reset, clean, or alter the baseline staging.
