#!/usr/bin/env bash
# Is this checkout deployable?
#
# A dirty prod clone means someone edited production directly. The vault used
# to be exempt because the runtime wrote into the checkout by design — ingest
# rewrote pages nightly and wiki-query appended to the journal, so the tree
# was dirty by construction. The vault now lives outside the checkout, so
# there's nothing left for the runtime to dirty here: any status this reports
# is someone editing production directly, whatever its name. No exemptions.
#
# Untracked files still count: compose bind-mounts ./bin, ./mcp-seam and
# ./runtime straight into the container, so a file hand-dropped in any of
# them is running in production. ./bin is the sharpest case — it lands at
# $HERMES_HOME/scripts, which is the only place Hermes cron will run from.
# (./plugins was one of these until the Plow Chat plugin moved upstream; the
# plugin now arrives in ~/.hermes/plugins from a pinned SHA, which is outside
# the checkout and therefore outside what this gate can see at all.)
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)" || exit 1
cd "$ROOT"

# Captured, not piped: a process substitution's exit status is invisible to
# set -e, so a git status that dies mid-run (corrupt index, full disk, an
# OOM-killed child — rev-parse survives all three since it never reads the
# index) would otherwise read as zero changes and print DEPLOYABLE on a tree
# nobody inspected. Capturing surfaces that through $?, like any command.
PORCELAIN="$(git status --porcelain)"

if [ -n "$PORCELAIN" ]; then
  # Deliberately not a substring of DEPLOYABLE — a caller checking for that
  # token to mean "go" would read a stop message containing it as a go too.
  echo "STOP — production has changes:"
  printf '%s\n' "$PORCELAIN"
  exit 1
fi

echo "DEPLOYABLE"
