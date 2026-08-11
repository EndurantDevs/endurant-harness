#!/bin/sh
set -eu

SKILL_NAME=endurant-harness
REPOSITORY=${ENDURANT_REPOSITORY:-https://github.com/EndurantDevs/endurant-harness.git}
REF=${ENDURANT_REF:-main}
AGENT=${ENDURANT_AGENT:-both}
SOURCE=${ENDURANT_SOURCE:-}
WORK=
INSTALL_STAGE=
ACTIVE_TARGET=
ACTIVE_BACKUP=
ACTIVE_MOVED=false

fail() {
  echo "endurant-harness installer: $*" >&2
  exit 1
}

cleanup() {
  if $ACTIVE_MOVED && ! exists "$ACTIVE_TARGET" && exists "$ACTIVE_BACKUP"; then
    mv "$ACTIVE_BACKUP" "$ACTIVE_TARGET"
  fi
  [ -z "${INSTALL_STAGE:-}" ] || rm -rf "$INSTALL_STAGE"
  [ -z "${WORK:-}" ] || rm -rf "$WORK"
}

trap cleanup 0
trap 'exit 2' HUP INT TERM

usage() {
  cat <<'EOF'
Usage: install.sh [--agent codex|claude|both] [--source CHECKOUT]

Environment: ENDURANT_AGENT, ENDURANT_SOURCE, ENDURANT_REF,
ENDURANT_REPOSITORY, ENDURANT_CODEX_SKILLS_DIR, ENDURANT_CLAUDE_SKILLS_DIR.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent) [ "$#" -ge 2 ] || fail "--agent needs a value"; AGENT=$2; shift 2 ;;
    --source) [ "$#" -ge 2 ] || fail "--source needs a value"; SOURCE=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

case "$AGENT" in
  codex|claude|both) ;;
  *) fail "agent must be codex, claude, or both" ;;
esac

command -v python3 >/dev/null 2>&1 || fail "python3 is required"

exists() {
  [ -e "$1" ] || [ -L "$1" ]
}

owned_skill() {
  [ -f "$1/SKILL.md" ] &&
    grep -Eq '^name:[[:space:]]*endurant-harness[[:space:]]*$' "$1/SKILL.md"
}

validate_target() {
  exists "$1" || return 0
  owned_skill "$1" || fail "refusing to replace unrelated target: $1"
}

validate_backup() {
  backup="$(dirname "$(dirname "$1")")/.$SKILL_NAME.previous"
  exists "$backup" || return 0
  owned_skill "$backup" || fail "refusing to replace unrelated backup: $backup"
}

remove_owned() {
  owned_skill "$1" || fail "refusing to remove unrelated backup: $1"
  if [ -L "$1" ]; then rm -f "$1"; else rm -rf "$1"; fi
}

install_tree() {
  source_tree=$1
  target=$2
  parent=$(dirname "$target")
  backup="$(dirname "$parent")/.$SKILL_NAME.previous"
  had_target=false

  mkdir -p "$parent"
  validate_target "$target"

  INSTALL_STAGE=$(mktemp -d "$parent/.$SKILL_NAME.install.XXXXXX")
  stage="$INSTALL_STAGE/$SKILL_NAME"
  cp -R "$source_tree" "$stage"
  if exists "$backup"; then remove_owned "$backup"; fi

  if exists "$target"; then
    ACTIVE_TARGET=$target
    ACTIVE_BACKUP=$backup
    ACTIVE_MOVED=true
    if ! mv "$target" "$backup"; then
      fail "could not preserve $target"
    fi
    had_target=true
  fi
  if ! mv "$stage" "$target"; then
    if $had_target; then mv "$backup" "$target"; fi
    fail "could not activate $target"
  fi
  ACTIVE_MOVED=false
  ACTIVE_TARGET=
  ACTIVE_BACKUP=
  rmdir "$INSTALL_STAGE"
  INSTALL_STAGE=
  echo "installed $target"
}

CODEX_TARGET=
CLAUDE_TARGET=
if [ "$AGENT" = codex ] || [ "$AGENT" = both ]; then
  if [ -n "${ENDURANT_CODEX_SKILLS_DIR:-}" ]; then
    CODEX_TARGET="$ENDURANT_CODEX_SKILLS_DIR/$SKILL_NAME"
  else
    modern="$HOME/.agents/skills/$SKILL_NAME"
    legacy="${CODEX_HOME:-$HOME/.codex}/skills/$SKILL_NAME"
    if exists "$modern" && exists "$legacy"; then
      fail "both Codex skill locations exist; remove one duplicate first"
    fi
    if exists "$legacy"; then CODEX_TARGET=$legacy; else CODEX_TARGET=$modern; fi
  fi
  validate_target "$CODEX_TARGET"
  validate_backup "$CODEX_TARGET"
fi
if [ "$AGENT" = claude ] || [ "$AGENT" = both ]; then
  CLAUDE_TARGET="${ENDURANT_CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/$SKILL_NAME"
  validate_target "$CLAUDE_TARGET"
  validate_backup "$CLAUDE_TARGET"
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/endurant-harness-install.XXXXXX")
if [ -z "$SOURCE" ]; then
  command -v git >/dev/null 2>&1 || fail "git is required for remote installation"
  git clone --quiet --depth 1 --branch "$REF" "$REPOSITORY" "$WORK/repository"
  SOURCE="$WORK/repository"
else
  [ -d "$SOURCE" ] || fail "source checkout not found: $SOURCE"
  SOURCE=$(CDPATH= cd "$SOURCE" && pwd -P)
fi

PACKAGE="$SOURCE/$SKILL_NAME"
[ -f "$PACKAGE/SKILL.md" ] || fail "skill package not found: $PACKAGE"
PYTHONDONTWRITEBYTECODE=1 python3 -S "$PACKAGE/scripts/audit_skill.py" \
  "$PACKAGE" --strict --format text >/dev/null

[ -z "$CODEX_TARGET" ] || install_tree "$PACKAGE" "$CODEX_TARGET"
[ -z "$CLAUDE_TARGET" ] || install_tree "$PACKAGE" "$CLAUDE_TARGET"

echo "Start a fresh Codex task or Claude Code session, then invoke endurant-harness."
