#!/usr/bin/env bash
# Nightly wiki chain. Runs INSIDE the container, invoked by `hermes cron`.
#
#   fetch → ingest → lint → digest
#
# Nothing here commits -- it cannot: ~/hermes-vault.git is not mounted into the
# container and the gateway holds no git credential. The vault is the runtime's
# writable area and lives outside this checkout entirely, at ~/hermes-vault,
# whose git dir sits beside the worktree. Pages reach git when the host-side
# scripts/promote-vault runs; none of it reaches this repo's `main`.
#
# One rule shapes the error handling: something is PRINTED on every path,
# because the message doubles as the liveness signal — a routine failure must
# not look like the job dying. On a normal night that is the wiki-digest; on an
# abort it is a one-line status, since the vault may not be worth summarising.
#
# This script does not deliver. The scheduler does, from the job's stdout, and
# an empty stdout is delivered as nothing — which is why every path prints.
# `notify()` carries the rest of that contract.
#
# The corollary, and the reason for the `>&2` on every step below: stdout is the
# MESSAGE now, delivered verbatim. Fetch counts, ingest progress, lint findings,
# hub output and the vault suite all belong in the cron log, not in the owners'
# chat, so each writes to stderr. Only an abort and the digest reach stdout.
set -uo pipefail

# The image sets HERMES_HOME (/var/lib/hermes on the Plow base) -- indexing it
# here, rather than hardcoding the literal, keeps VAULT correct across base
# images.
# Required, not defaulted: a container that has lost the variable must fail
# here, not silently resolve a vault that is not actually mounted.
HERMES_HOME="${HERMES_HOME:?nightly.sh: HERMES_HOME is unset in the container}"
VAULT="${VAULT:-$HERMES_HOME/repo/vault}"
BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUS=""

# Also to stderr, which is the cron log. $STATUS reaches one place — the digest
# prompt — so a note was readable only by whoever read that message on the
# night it went out; nothing outside the container, `just test-wiki` included,
# could see that the run had noted anything at all.
note() { STATUS+="$1; "; echo "nightly: $1" >&2; }

# Every abort reports by printing: the scheduler delivers this job's stdout, and
# a non-zero exit as an error alert (cron/scheduler.py). This used to spend an
# agent turn on the send, which could not succeed -- `hermes chat -q` has no
# messaging platform — and an unreachable channel makes such a turn search
# rather than fail: two hours and 32 tool calls once, holding the vault
# throughout. Printing cannot do that. See #49.
notify() {
  echo "nightly: $1"
  echo "nightly: $1" >&2
}

if ! "$BIN/hostex-raw" --vault "$VAULT" >&2; then
  # Fetch failure leaves the vault untouched and consistent — still report,
  # or the silence reads as death.
  notify "Wiki nightly FAILED at fetch. Vault unchanged."
  exit 1
fi

# Through ingest-all, not a bare `hermes chat` turn. A single turn self-bounds
# — the bootstrap stopped at 14 of 235 conversations and reported success — so
# doing it inline here meant the nightly run silently ingested a fraction of
# what arrived and nothing noticed. ingest-all loops and asserts coverage from
# the manifest between rounds.
if ! "$BIN/ingest-all" "$VAULT" >&2; then
  # Stop, do not note-and-continue. Carrying on through lint and digest after
  # a terminal ingest failure reports a partially-ingested night as a normal
  # one.
  echo "nightly: FAILED at ingest" >&2
  notify "Wiki nightly FAILED at ingest. No digest was generated; the vault is as the failed run left it. See the cron log."
  exit 1
fi

if ! hermes chat -q "Use the wiki-lint skill on the vault at ${VAULT}. Report contradictions, orphaned pages, and stale citations." >&2; then
  note "lint errored"
fi

# What lint found, as opposed to whether lint ran. The condition above tests the
# turn, so a lint that runs perfectly and reports hundreds of defects takes the
# success path — the 2026-08-04 regeneration reported malformed_citations=392,
# exited 0, and sent a green digest over a vault failing 15 of its own tests.
#
# Gating on lint's own counters is the wrong lever: they are model-authored, and
# `malformed_citations` is not even in the wiki-lint skill's log-line template,
# so the set varies run to run. The vault's suite is the deterministic half of
# the same signal — it is what caught the 15 — so the chain runs that instead.
#
# Note-and-continue: the digest below is this run's liveness signal, so a
# defect here has to be reported through it rather than silenced by an abort.
# The pages are already written by now; aborting would not unwrite them, it
# would only withhold the news.
#
# Spelled out rather than `just test` in the vault: `just` is not installed in
# this image, so delegating would have failed every night and reported it as a
# corpus defect. `uv` is — the image build uses it.
# The two outcomes are reported apart because they mean opposite things and the
# digest is one line: exit 1 is the corpus, anything else is this invocation —
# nothing collected, a dependency the pin above does not carry, uv unable to
# resolve. Reporting a stale dep set as a broken corpus is the same
# indistinguishable-message failure the paragraph above avoids.
# Tonight's pages belong in tonight's hub lists, and the suite below asserts
# exactly that — so this runs before the gate rather than after it, and a deploy
# that reverted a hub heals inside the run that would otherwise report it.
# Note-and-continue: the digest below is this run's liveness signal, so a
# defect here has to be reported through it rather than silenced by an abort.
# The pages are already written by now; aborting would not unwrite them, it
# would only withhold the news.
if ! "$BIN/build-hubs" "$VAULT" >&2; then
  note "hub rebuild failed; property hubs may not list tonight's pages"
fi

(cd "$VAULT" && uv run --no-project --python 3.13 --with pytest==8.4.2 pytest -q) >&2
rc=$?
if [ "$rc" -eq 1 ]; then
  note "vault integrity FAILED; see the cron log"
elif [ "$rc" -ne 0 ]; then
  note "vault checks could not run (rc=$rc); see the cron log"
fi

# Bounded for the same reason the aborts are, with room for the real work it
# does: it reads the vault and writes a summary before it sends. This is the
# success path's liveness message. The turn only has to PRINT it -- the
# scheduler delivers this script's stdout -- so the bound now covers reading the
# vault and writing a summary, not an unwinnable hunt for a channel this process
# does not have.
if ! timeout 600 hermes chat -q "Use the wiki-digest skill on the vault at ${VAULT} for the last day. Prefix the digest with this run status, verbatim: '${STATUS:-ok}'. Print the digest as your reply and nothing else — do not try to send it anywhere. Print it even if nothing changed: this reply is delivered as the message that tells me the job is alive, and an empty reply is delivered as nothing at all."; then
  echo "nightly: the digest did not send within 600s" >&2
  exit 1
fi
