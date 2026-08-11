@RTK.md

# Endurant Harness Project Instructions

- Treat `endurant-harness/` as the releasable package and `subjects/` as frozen or isolated experiment subjects.
- Preserve unrelated dirty work. Never reset, clean, stash, or broadly reformat to simplify a task.
- Keep raw model JSONL, generated workspaces, caches, credentials, and full logs under ignored artifact paths.
- Use neutral synthetic identifiers in tracked fixtures, reports, commits, and publication artifacts.
- Keep the installable package free of README, changelog, cache, bytecode, backup, and temporary files.
- Performance claims require controlled unchanged/current and candidate measurements; ordinary correctness work skips irrelevant synthetic benchmarks.
- Use `.agents/endurant-harness-profile.md` as the canonical validation and release command list; do not maintain a partial duplicate here.
- Distinguish local proof, remote CI, published artifacts, installation, and active-session provenance.
- Do not commit, push, publish a release, or invoke remote CI without explicit user authorization for that task.
