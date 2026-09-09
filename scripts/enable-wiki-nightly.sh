#!/usr/bin/env bash
# Register the nightly wiki chain with Hermes' scheduler. Run from the deployed
# checkout. See README § Compiling the wiki nightly.
set -euo pipefail

# Compose resolves its project from the directory, so anchor it to this repo
# rather than to the caller's cwd: run from elsewhere, a bare `docker compose`
# names a different project -- a dev checkout of this same repo is one -- and
# reaches a container that is not this agent.
repo=$(cd "$(dirname "$0")/.." && pwd)
compose() { docker compose --project-directory "$repo" "$@"; }

# Refuse a second job. The chain ingests into the vault, so two of them race
# the same pages — the same shared-state argument the poller's enabler makes
# about its cursor.
# Captured, not piped, so a docker error aborts under `set -e` rather than
# reading as "no job".
existing=$(compose exec -T hermes hermes cron list)
case "$existing" in
  *wiki-nightly*)
    echo "wiki-nightly already exists - remove it first (hermes cron remove wiki-nightly)"
    exit 1 ;;
esac

# `--no-agent` is what makes `--script` legal on its own: without it the CLI
# refuses ("create requires either prompt or at least one skill"), which is how
# the README's documented command silently created nothing.
#
# It is also the right mode on its own merits. nightly.sh is the whole job and
# reports through its own bounded `notify`, so an agent turn would add nothing
# to do — and its stdout carries vault content distilled from guest mail, so
# injecting that into a prompt would put guest-derived text in the instruction
# channel for no gain. That is the surface #44 tracks.
#
# A refusal exits non-zero and `set -e` stops the run. `cron create` echoes the
# job it made, and that echo is the operator's confirmation, so this must not be
# redirected or captured.
compose exec -T hermes hermes cron create '0 3 * * *' \
    --name wiki-nightly --script nightly.sh --no-agent
