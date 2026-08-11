# Claude Code adapter

Derives the Claude Code variant of the Endurant Harness skill from the
canonical Codex package in `endurant-harness/`. The scripts, references,
evals, and protocol are agent-neutral; the variant differs only in:

- three instruction-file mentions widened from `AGENTS.md` to
  `AGENTS.md`/`CLAUDE.md` (Claude Code's project-instruction file);
- a distinct provenance release id (`<source-release>-claude`, e.g.
  `v5-claude`) restamped with the recomputed canonical package hash.

The variant is **generated, never hand-edited** — the canonical package
stays the single source of truth, and the generated tree is not tracked in
this repository. Every transform is an exact-match anchor that fails the
build if upstream wording drifts, and the build verifies itself with the
package's own strict audit and provenance check before it will install.

## Install / update

```bash
python3 adapters/claude-code/build_claude_skill.py --install
```

Installs atomically to `~/.claude/skills/endurant-harness` (Claude Code's
user-skill directory), keeping a rollback copy of any replaced installation
beside the skills directory. New Claude Code sessions discover the skill
automatically; sessions already running keep whatever version they loaded.

After each new canonical release (v6, ...), rerun the same command to
regenerate and reinstall the matching `v<N>-claude` variant. If the build
fails with a transform-anchor error, the canonical wording changed — update
`TRANSFORMS` in `build_claude_skill.py` deliberately, never by hand-editing
the generated tree.

## Design notes

- `agents/openai.yaml` is kept in the variant: the strict audit requires it,
  and it is inert under Claude Code.
- `scripts/endurant.py` is byte-identical to the canonical package. No
  CLAUDE.md probe discovery was added because Claude Code loads CLAUDE.md
  into context itself; the probe's AGENTS.md listing remains correct and
  the engine stays a single implementation.
- Provenance handshake works unchanged: the loaded SKILL.md carries
  `endurant-provenance:<release>-claude:<hash>`, and
  `scripts/endurant.py provenance --loaded-provenance <release>:<hash>`
  reports `current`/`stale`/`unknown` exactly as on Codex.
