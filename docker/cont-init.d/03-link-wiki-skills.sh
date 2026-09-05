#!/usr/bin/env bash
# Link the enabled wiki skills into $HERMES_HOME/skills at container start.
#
# Runs after the image's own bundled-skill sync (01-hermes-setup), because
# ~/.hermes is mounted over $HERMES_HOME and masks anything the image writes
# there at build time.
#
# Only this subset is linked. The other ~30 skills that ship in the wheel stay
# in the package, unlinked and invisible to the agent — enable one by adding it
# to ENABLED and restarting.
set -euo pipefail

ENABLED=(
  llm-wiki
  wiki-ingest
  wiki-lint
  wiki-digest
  wiki-query
)

SKILLS_DIR="${HERMES_HOME:?the image sets this; a container without it must not guess}/skills"
WIKI_PY="${WIKI_VENV:-/opt/wiki-venv}/bin/python"
SRC="$("$WIKI_PY" -c 'import obsidian_wiki, pathlib; print(pathlib.Path(obsidian_wiki.__file__).parent / "_data" / "skills")')"

if [ ! -d "$SRC" ]; then
  echo "[wiki-skills] obsidian_wiki skills not found at $SRC" >&2
  exit 1
fi

mkdir -p "$SKILLS_DIR"

# $HERMES_HOME/skills is on the bind-mounted volume and persists across boots,
# so dropping a skill from ENABLED would otherwise leave it installed forever.
# Prune anything we manage that is no longer enabled.
for existing in "$SKILLS_DIR"/wiki-* "$SKILLS_DIR"/llm-wiki "$SKILLS_DIR"/okf "$SKILLS_DIR"/okf-*; do
  [ -e "$existing" ] || continue
  name="$(basename "$existing")"
  keep=""
  for skill in "${ENABLED[@]}"; do
    [ "$name" = "$skill" ] && keep=1 && break
  done
  [ -n "$keep" ] || rm -rf "$existing"
done

for skill in "${ENABLED[@]}"; do
  if [ ! -d "$SRC/$skill" ]; then
    echo "[wiki-skills] missing skill: $skill" >&2
    exit 1
  fi
  rm -rf "${SKILLS_DIR:?}/$skill"
  cp -R "$SRC/$skill" "$SKILLS_DIR/$skill"
done

echo "[wiki-skills] linked ${#ENABLED[@]} wiki skills into $SKILLS_DIR"
