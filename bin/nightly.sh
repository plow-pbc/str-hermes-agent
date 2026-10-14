#!/usr/bin/env bash
# Nightly wiki chain. Runs INSIDE the container, invoked by `hermes cron`.
#
#   fetch → ingest → index → provenance → validate → snapshot | restore → digest
#
# The wiki is the owner's common plow-wiki on their Mac (~/Plow/wiki), and str's
# pages live under its projects/str/ roots. The container reaches it only through the
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
# ingest progress and check output belong in $LOG, not the owner's chat. So the
# routing is one policy rather than a redirect per command -- see the `exec`
# below.
set -uo pipefail

# The image sets HERMES_HOME (/var/lib/hermes on the Plow base) -- indexing it
# here, rather than hardcoding the literal, keeps VAULT correct across base
# images.
# Required, not defaulted: a container that has lost the variable must fail
# here, not silently resolve a staging directory that is not actually mounted.
HERMES_HOME="${HERMES_HOME:?nightly.sh: HERMES_HOME is unset in the container}"
VAULT="${VAULT:-$HERMES_HOME/repo/vault}"
# A path on the owner's Mac, so the `~` is theirs: quoted here, expanded there.
# Fixed, not overridable: Latch's wiki plugin pins WIKI_PATH to this same path.
WIKI='~/Plow/wiki'
BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELAY="$BIN/plow_relay.py"
STATUS=""
LOG="$HERMES_HOME/logs/wiki-nightly.log"

# Stderr is the default, and fd 3 is the delivered channel. Everything this
# script runs is diagnostics for $LOG; only `notify()` and the digest write to
# fd 3, so a step added later is quiet by default rather than quiet only if
# whoever added it remembered a redirect. The tee is what makes $LOG exist: the
# scheduler keeps a job's stderr only when it exits non-zero, and a night that
# notes a failed check still exits 0.
exec 3>&1 2> >(tee "$LOG" >&2) 1>&2

# Also to $LOG. $STATUS reaches one place — the digest prompt — so a note was
# readable only by whoever read that message on the night it went out; nothing
# outside the container could see that the run had noted anything at all.
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

# A night is all or nothing. Every check from ingest onward that fails is a
# `note`, and any note means tonight's pages do not reach history: str's roots
# go back to the last snapshot and the manifest to its pre-run bytes, so the
# next run re-ingests tonight's conversations instead of the bad pages being
# snapshotted and reported every night with nothing repairing them.
# Exit 1 from a check is the corpus; 2 is plow_relay failing to reach the Mac.
# They mean opposite things and the digest is one line, so they are reported apart.
checked() {
  local what="$1"; shift
  "$@"
  case $? in
    0) ;;
    2) note "$what could not run: the owner's Mac did not answer" ;;
    *) note "$what FAILED" ;;
  esac
}

# Tonight's pages into the generated index and hub tables, then every citation on
# them held against the manifest. The index comes first: provenance lists pages
# from it, and a stale one cannot list a page written tonight. No `--wiki`: Latch
# refuses anything but a bare subcommand.
reconcile() {
  checked "wiki index" "$RELAY" run --write "$WIKI" -- wiki index
  checked "provenance" "$BIN/wiki-provenance" "$VAULT" "$WIKI"
}

# The manifest as of the last snapshot: what a restore of str's pages pairs
# with. Taken when a snapshot commits, never at the start of a night -- a night
# whose restore failed would otherwise leave the next night backing up a
# manifest ahead of the pages, and a later restore would pair them wrong. Seeded
# from the live manifest once, on the first run that has none.
MANIFEST="$VAULT/.manifest.json"
SNAPSHOTTED="$HERMES_HOME/wiki-manifest.snapshotted.json"
if [ ! -e "$SNAPSHOTTED" ]; then
  echo "nightly: no snapshotted manifest yet; seeding it from $MANIFEST" >&2
  cp -p "$MANIFEST" "$SNAPSHOTTED" || { notify "Wiki nightly FAILED: cannot seed $SNAPSHOTTED. The wiki is unchanged."; exit 1; }
fi

# Undo everything since the last snapshot. The manifest goes back only once the
# pages have: a manifest that forgot pages still on the wiki is the
# double-append the manifest exists to prevent. A restore that fails is said
# so, and the next failing night retries it from the same pair.
roll_back() {
  local why="$STATUS"
  STATUS=""
  checked "wiki restore" "$RELAY" run --write "$WIKI" --write "$WIKI.git" -- wiki restore --writer str
  [ -z "$STATUS" ] && cp -p "$SNAPSHOTTED" "$MANIFEST"
  checked "wiki index" "$RELAY" run --write "$WIKI" -- wiki index
  STATUS="rolled back: ${why}${STATUS:+rollback incomplete: $STATUS}"
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
# what arrived and nothing noticed. ingest-all loops, asserts coverage from the
# manifest between rounds, and hands each round's check failures to the next.
"$BIN/ingest-all" "$VAULT" "$WIKI" || note "ingest FAILED"

# The skill's own vault is $VAULT, so a turn that writes a page where the skill
# says rather than where the prompt says leaves it here — on disk, recorded in
# the manifest, and on no wiki any agent reads. Only the raw cache belongs in
# staging.
if find "$VAULT" -mindepth 2 -name '*.md' -not -path "$VAULT/_raw/*" | grep -q .; then
  note "ingest wrote pages into local staging instead of the wiki"
fi

reconcile
# Only str's roots: a page another writer owns is that writer's to fix (wiki.toml).
checked "wiki validation" "$RELAY" run -- wiki validate --writer str
# The commit is the night's; the push is not. A night that failed to commit is
# undone like any other failure: left in place, a later restore would revert its
# pages while the manifest still recorded them. A failed push is only noted --
# the next snapshot sends commits left behind origin.
[ -z "$STATUS" ] && checked "wiki snapshot" "$RELAY" run --write "$WIKI" --write "$WIKI.git" -- wiki snapshot --author str
if [ -n "$STATUS" ]; then
  roll_back
else
  cp -p "$MANIFEST" "$SNAPSHOTTED"
  # History lives beside the wiki on the Mac and is pushed off it: a compiled
  # corpus on one disk with no other copy is the exposure promote-vault was
  # written to close. `--push` scans what leaves for credentials, and refuses
  # loudly when the history repo has no origin, so a missing remote is in every
  # digest, not a silent single copy.
  checked "wiki push" "$RELAY" run --write "$WIKI" --write "$WIKI.git" --network -- wiki snapshot --push --author str
fi

# Bounded for the same reason the aborts are, with room for the real work it
# does: it reads the wiki and writes a summary. The turn only has to PRINT it --
# the scheduler delivers this script's stdout. `-Q` keeps that stdout to the
# reply: without it the whole CLI transcript -- the prompt echoed back, tool
# traces, reasoning -- was the message, with the digest at the bottom.
STATUS="${STATUS:+${STATUS}see $LOG}"
if ! timeout 600 hermes chat -Q -q "Use the wiki-digest skill on the owner's wiki at ${WIKI} for the last day. The wiki is on the owner's Mac: read its log.md and pages only with plow_read_file. Prefix the digest with this run status, verbatim: '${STATUS:-ok}'. Print the digest as your reply and nothing else — do not try to send it anywhere. Print it even if nothing changed: this reply is delivered as the message that tells me the job is alive, and an empty reply is delivered as nothing at all." >&3; then
  echo "nightly: the digest did not send within 600s" >&2
  exit 1
fi
