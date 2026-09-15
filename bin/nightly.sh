#!/usr/bin/env bash
# Nightly wiki chain. Runs INSIDE the container, invoked by `hermes cron`.
#
#   fetch → ingest → index → provenance → validate → snapshot → digest
#
# The wiki is the owner's common plow-wiki on their Mac (~/Plow/wiki), and str's
# pages live under its str/ roots. The container reaches it only through the
# Latch relay: the ingest and digest turns use the plow_* tools, and the steps
# that run the `wiki` CLI go through bin/plow_relay.py, which runs it on the Mac.
# $VAULT is local ingest staging only: the Hostex raw cache and the manifest
# obsidian-wiki's cache-check reads, neither of which is a wiki page.
#
# One rule shapes the error handling: something is PRINTED on every path,
# because the message doubles as the liveness signal — a routine failure must
# not look like the job dying. On a normal night that is the wiki-digest; on an
# abort it is a one-line status.
#
# This script does not deliver. The scheduler does, from the job's stdout, and
# an empty stdout is delivered as nothing — which is why every path prints.
# `notify()` carries the rest of that contract.
#
# The corollary: stdout is the MESSAGE now, delivered verbatim. Fetch counts,
# ingest progress and check output belong in the cron log, not the owners'
# chat. So the routing is one policy rather than a redirect per command -- see
# the `exec` below.
set -uo pipefail

# Stderr is the default, and fd 3 is the delivered channel. Everything this
# script runs is diagnostics for the cron log; only `notify()` and the digest
# write to fd 3, so a step added later is quiet by default rather than quiet
# only if whoever added it remembered a redirect.
exec 3>&1 1>&2

# The image sets HERMES_HOME (/var/lib/hermes on the Plow base) -- indexing it
# here, rather than hardcoding the literal, keeps VAULT correct across base
# images.
# Required, not defaulted: a container that has lost the variable must fail
# here, not silently resolve a staging directory that is not actually mounted.
HERMES_HOME="${HERMES_HOME:?nightly.sh: HERMES_HOME is unset in the container}"
VAULT="${VAULT:-$HERMES_HOME/repo/vault}"
# A path on the owner's Mac, so the `~` is theirs: quoted here, expanded there.
WIKI="${STR_WIKI:-~/Plow/wiki}"
BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELAY="$BIN/plow_relay.py"
STATUS=""

# Also to stderr, which is the cron log. $STATUS reaches one place — the digest
# prompt — so a note was readable only by whoever read that message on the
# night it went out; nothing outside the container could see that the run had
# noted anything at all.
note() { STATUS+="$1; "; echo "nightly: $1" >&2; }

# Every abort reports by printing: the scheduler delivers this job's stdout, and
# a non-zero exit as an error alert (cron/scheduler.py). This used to spend an
# agent turn on the send, which could not succeed -- `hermes chat -q` has no
# messaging platform — and an unreachable channel makes such a turn search
# rather than fail: two hours and 32 tool calls once. Printing cannot do that.
# See #49.
notify() {
  echo "nightly: $1" >&3
  echo "nightly: $1"
}

# The rest of the chain reports as a run of `note`s on this one invariant: past
# ingest the pages are already written, so aborting would not unwrite them, it
# would only withhold the news. The digest is this run's liveness signal.
# Exit 1 from a check is the corpus; 2 is plow_relay failing to reach the Mac.
# They mean opposite things and the digest is one line, so they are reported apart.
checked() {
  local what="$1"; shift
  "$@"
  case $? in
    0) ;;
    2) note "$what could not run: the owner's Mac did not answer; see the cron log" ;;
    *) note "$what FAILED; see the cron log" ;;
  esac
}

# Tonight's pages into the generated index and hub tables, then every citation on
# them held against the manifest. The index comes first: provenance lists pages
# from it, and a stale one cannot list a page written tonight.
reconcile() {
  checked "wiki index" "$RELAY" run --write "$WIKI" -- wiki --wiki "$WIKI" index
  checked "provenance" "$BIN/wiki-provenance" "$VAULT" "$WIKI"
}

if ! "$BIN/hostex-raw" --vault "$VAULT"; then
  # Fetch failure leaves staging untouched and consistent — still report,
  # or the silence reads as death.
  notify "Wiki nightly FAILED at fetch. The wiki is unchanged."
  exit 1
fi

# Through ingest-all, not a bare `hermes chat` turn. A single turn self-bounds
# — the bootstrap stopped at 14 of 235 conversations and reported success — so
# doing it inline here meant the nightly run silently ingested a fraction of
# what arrived and nothing noticed. ingest-all loops and asserts coverage from
# the manifest between rounds.
if ! "$BIN/ingest-all" "$VAULT" "$WIKI"; then
  # Stop, do not note-and-continue. Carrying on after a terminal ingest failure
  # reports a partially-ingested night as a normal one. But a failed turn can
  # have written a page and not its manifest record, which the next run would
  # re-ingest into that page, so the notice says whether it did.
  echo "nightly: FAILED at ingest" >&2
  reconcile
  notify "Wiki nightly FAILED at ingest. No digest was generated. ${STATUS:-Every page it wrote has a manifest record.} See the cron log."
  exit 1
fi

# The skill's own vault is $VAULT, so a turn that writes a page where the skill
# says rather than where the prompt says leaves it here — on disk, recorded in
# the manifest, and on no wiki any agent reads. Only the raw cache belongs in
# staging.
if find "$VAULT" -mindepth 2 -name '*.md' -not -path "$VAULT/_raw/*" | grep -q .; then
  note "ingest wrote pages into local staging instead of the wiki"
fi

reconcile
checked "wiki validation" "$RELAY" run -- wiki --wiki "$WIKI" validate
# History lives beside the wiki on the Mac and is pushed off it: a compiled corpus
# on one disk with no other copy is the exposure promote-vault was written to
# close. `--push` scans what leaves for credentials, and refuses loudly when the
# history repo has no origin, so a missing remote is in every digest, not a
# silent single copy. Before the digest, so a failed snapshot is in the message
# rather than only in the log.
checked "wiki snapshot" "$RELAY" run --write "$WIKI" --write "$WIKI.git" --network -- \
  wiki --wiki "$WIKI" snapshot --push --author str

# Bounded for the same reason the aborts are, with room for the real work it
# does: it reads the wiki and writes a summary. The turn only has to PRINT it --
# the scheduler delivers this script's stdout.
if ! timeout 600 hermes chat -q "Use the wiki-digest skill on the owner's wiki at ${WIKI} for the last day. The wiki is on the owner's Mac: read its log.md and pages only with plow_read_file. Prefix the digest with this run status, verbatim: '${STATUS:-ok}'. Print the digest as your reply and nothing else — do not try to send it anywhere. Print it even if nothing changed: this reply is delivered as the message that tells me the job is alive, and an empty reply is delivered as nothing at all." >&3; then
  echo "nightly: the digest did not send within 600s" >&2
  exit 1
fi
