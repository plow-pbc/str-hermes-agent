#!/usr/bin/env bash
# Register the nightly wiki chain with Hermes' scheduler. Run from the deployed
# checkout. See README § Compiling the wiki nightly.
set -euo pipefail

compose() { "$(dirname "$0")/compose" "$@"; }

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
# It is also the right mode on its own merits, and the reason survives this
# change: nightly.sh is the whole job, and its stdout carries vault content
# distilled from guest mail, so injecting that into a prompt would put
# guest-derived text in the instruction channel for no gain.
#
# `--deliver` is what was missing: without it the job is `Deliver: local` and the
# digest goes nowhere. The script must not send for itself -- `hermes chat -q`
# runs with platform=cli and has no channel — and per cron/scheduler.py a
# --no-agent job's stdout is delivered verbatim, an empty one silently, and a
# non-zero exit as an error alert. So the digest and every abort both reach the
# owner through the scheduler. See #49.
#
# A bare `plow_chat` is the platform's home channel -- PLOW_HOME_CHANNEL, the
# owner's direct chat with the agent -- not the owners' group the other jobs
# post to. The digest is the operator's: it covers the whole shared wiki, other
# agents' pages included, and its check failures are for whoever fixes them.
# The scheduler also mirrors a home delivery into that chat's session, so a
# reply to the digest has it in context.
#
# A refusal exits non-zero and `set -e` stops the run. `cron create` echoes the
# job it made, and that echo is the operator's confirmation, so this must not be
# redirected or captured.
compose exec -T hermes hermes cron create '0 3 * * *' \
    --name wiki-nightly --script nightly.sh --no-agent --deliver plow_chat
