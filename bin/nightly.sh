#!/usr/bin/env bash
# Nightly wiki chain. Runs INSIDE the container, invoked by `hermes cron`.
#
#   fetch → ingest → index → provenance → validate → snapshot | restore → digest
#
# The wiki is the owner's common plow-wiki on their Mac (~/Plow/wiki), and str's
# pages live under its projects/str/ roots. The container reaches it only through the
# latch CLI, and every step that touches it -- the ingest and digest turns too --
# goes through bin/plow_relay.py, which reads, writes and runs `wiki` on the Mac.
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
# Fixed, not overridable: it is the `wiki` CLI's own default on the Mac.
WIKI='~/Plow/wiki'
BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELAY="$BIN/plow_relay.py"
STATUS=""
LOG="$HERMES_HOME/logs/wiki-nightly.log"

# Stderr is the default, and fd 3 is the delivered channel. Everything this
# script runs is diagnostics for $LOG; only `notify()` and the digest write to
# fd 3, so a step added later is quiet by default rather than quiet only if
# whoever added it remembered a redirect. Stderr goes to $LOG alone, not on to
# the scheduler: a failed night exits non-zero, and the scheduler delivers a
# failure as one line cut from stderr-then-stdout, so any stderr would push the
# failure out of the message.
exec 3>&1 2>"$LOG" 1>&2

# Also to $LOG. $STATUS reaches one place — the failure line — so a note was
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
# Exit 1 from a check is the corpus; 2 is latch failing to reach the Mac; 4 is
# the Mac's gatekeeper refusing. Each has a different fix and the alert is one
# line, so they are reported apart.
checked() {
  local what="$1"; shift
  "$@"
  case $? in
    0) ;;
    2) note "$what could not run: the owner's Mac did not answer" ;;
    4) note "$what was refused by the gatekeeper on the owner's Mac" ;;
    *) note "$what FAILED" ;;
  esac
}

# Tonight's pages into the generated index and hub tables, then every citation on
# them held against the manifest. The index comes first: provenance lists pages
# from it, and a stale one cannot list a page written tonight. No `--wiki`: the
# CLI's default is the owner's wiki.
reconcile() {
  checked "wiki index" "$RELAY" run --write "$WIKI" -- wiki index
  checked "provenance" "$BIN/wiki-provenance" "$VAULT" "$WIKI"
}

# The manifest as of the last snapshot: what a restore of str's pages pairs
# with. Taken when a snapshot commits, never at the start of a night -- a night
# whose restore failed would otherwise leave the next night backing up a
# manifest ahead of the pages, and a later restore would pair them wrong. Seeded
# from the live manifest once, on the first run that has none.
# Every relay step acts for the home chat, and every turn needs the model
# credential; both live only in the environment plow-init publishes at boot,
# which the scheduler inherits and a bare `docker compose exec` does not. Run
# there, every step fails, the rollback with them, and the alert reads as a
# broken wiki (2026-11-05 04:15). README § Compiling the wiki nightly has the
# command that carries it.
[ -n "${PLOW_HOME_CHANNEL:-}" ] || { notify "Wiki nightly FAILED: run outside the gateway's environment (no PLOW_HOME_CHANNEL). The wiki is unchanged."; exit 1; }

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
#
# The scheduler kills this script at 3600s. Budgeted so the round that starts
# last (about 15 minutes at the batch size), the checks and the 600s digest
# still finish inside it; a backlog past the budget continues the next night.
# Set but empty lifts it, for a run nothing is timing (README § Compiling the wiki nightly).
DEFERRED=""
INGEST_BUDGET="${INGEST_BUDGET-1500}" "$BIN/ingest-all" "$VAULT" "$WIKI"
case $? in
  0) ;;
  3) DEFERRED="ingest stopped at its time budget, the rest continues tomorrow; " ;;
  *) note "ingest FAILED" ;;
esac

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
  { cp -p "$MANIFEST" "$SNAPSHOTTED.tmp" && mv "$SNAPSHOTTED.tmp" "$SNAPSHOTTED"; } || note "manifest checkpoint FAILED"
  # History lives beside the wiki on the Mac and is pushed off it: a compiled
  # corpus on one disk with no other copy is the exposure promote-vault was
  # written to close. `--push` scans what leaves for credentials, and refuses
  # loudly when the history repo has no origin, so a missing remote fails every
  # night, not a silent single copy.
  checked "wiki push" "$RELAY" run --write "$WIKI" --write "$WIKI.git" --network -- wiki snapshot --push --author str
fi

# Any note means the night failed, and it reports failed: on a non-zero exit
# the scheduler records the run as an error and delivers its own one-line
# alert, carrying this line, in place of stdout. So a failed night sends no
# digest -- after a rollback it would only describe the last snapshot, and lead
# with "nothing changed" over a failure.
if [ -n "$STATUS" ]; then
  notify "Wiki nightly FAILED: ${STATUS}see $LOG"
  exit 1
fi

# The digest reads str's pages from a local copy taken in one call. Every latch
# call from the container waits on the Mac's gatekeeper, about 12s, so a turn
# reading page by page through the relay spent its whole 600s on reads.
# Base64 because the relay carries stdout as text, and pipefail so a partial
# tar on the Mac is a failed copy rather than a short one.
PAGES="$(mktemp -d)"
trap 'rm -rf "$PAGES"' EXIT
if ! "$RELAY" run -- /bin/sh -c 'set -o pipefail; cd ~/Plow/wiki && tar -czf - projects/str/properties projects/str/operations log.md | base64' \
    | base64 -d | tar -xzf - -C "$PAGES"; then
  notify "Wiki nightly: could not copy str's pages for the digest"
  exit 1
fi

# Bounded for the same reason the aborts are, with room for the real work it
# does: it reads the wiki and writes a summary. The turn only has to PRINT it --
# the scheduler delivers this script's stdout. `-Q` keeps that stdout to the
# reply: without it the whole CLI transcript -- the prompt echoed back, tool
# traces, reasoning -- was the message, with the digest at the bottom.
#
# The copy is the turn's whole vault: the wiki is shared, other writers' roots
# sit beside str's, and the skill digests whatever vault it is pointed at. The
# skill also appends a DIGEST line to log.md; this turn writes nothing.
if ! timeout 600 hermes chat -Q -q "Use the wiki-digest skill for the last day on str's pages, copied from the owner's wiki to the vault ${PAGES}: the pages under ${PAGES}/projects/str/properties/ and ${PAGES}/projects/str/operations/, and the entries in ${PAGES}/log.md that name one of them. Read them there with your file tools; do not use latch or reach the owner's Mac. The rest of the wiki belongs to other writers. Do not read, list or mention any other page, including in themes, connections or pages worth revisiting. Name a page by its path in the wiki, projects/str/..., never by the copy's path. This run only reads: skip the skill's log.md append and its journal page, and write no file. Prefix the digest with this run status, verbatim: '${DEFERRED}ok'. Print the digest as your reply and nothing else — do not try to send it anywhere. Print it even if nothing changed: this reply is delivered as the message that tells me the job is alive, and an empty reply is delivered as nothing at all." >&3; then
  notify "Wiki nightly: the digest did not send within 600s"
  exit 1
fi
