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
# Two rules shape the error handling:
#   1. A message is sent on every path, because it doubles as the liveness
#      signal — a routine failure must not look like the job dying. On a normal
#      night that message is the wiki-digest; on an abort it is a one-line
#      status, since the vault may not be in a state worth summarising.
#
#      Delivery is bounded, so silence is not quite proof of death. Sending is
#      an agent turn, and an unreachable channel makes it search rather than
#      fail — one such turn ran two hours holding the vault. Past the bound the
#      run writes the message to the log and exits non-zero instead. So silence
#      means the job died OR the channel was unreachable, and the cron log
#      tells them apart. Blocking on delivery is worse: a job wedged all night
#      is a job that did not run tomorrow either.
set -uo pipefail

# The image sets HERMES_HOME (/opt/data today, /var/lib/hermes once this
# agent opts into agent-mgr's boot contract) -- indexing it here, rather than
# hardcoding either literal, keeps VAULT and SOUL_OUT correct under both.
# Required, not defaulted: a container that has lost the variable must fail
# here, not silently resolve a vault or SOUL that is not actually mounted.
HERMES_HOME="${HERMES_HOME:?nightly.sh: HERMES_HOME is unset in the container}"
VAULT="${VAULT:-$HERMES_HOME/repo/vault}"
# The composed SOUL's destination. Overridable for the same reason $VAULT is:
# `just test-wiki` points both at scratch, and this one is the live gateway's
# injected system prompt — a run that composed a scratch vault's index over it
# would leave production advertising pages that exist nowhere but the test.
SOUL_OUT="${SOUL_OUT:-$HERMES_HOME/SOUL.md}"
BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUS=""

# Also to stderr, which is the cron log. $STATUS reaches one place — the digest
# prompt — so a note was readable only by whoever read that message on the
# night it went out; nothing outside the container, `just test-wiki` included,
# could see that the run had noted anything at all.
note() { STATUS+="$1; "; echo "nightly: $1" >&2; }

# Every abort reports through here. Bounded, because the delivery is an agent
# turn: it has to pick a tool and use it, and when the channel is not reachable
# it does not fail, it searches. Measured, one such turn spent two hours and 32
# tool calls looking for a plow_chat it was never going to find, holding the
# vault the whole time. A cron job that hangs is worse than one that reports
# failure, so a message that cannot be delivered inside two minutes is written
# to the log instead and the run ends.
notify() {
  timeout 120 hermes chat -q "Send me this over plow_chat, verbatim: '$1'" \
    || echo "nightly: could not deliver within 120s: $1" >&2
}

if ! "$BIN/hostex-raw" --vault "$VAULT"; then
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
if ! "$BIN/ingest-all" "$VAULT"; then
  # Stop, do not note-and-continue. Carrying on through lint and digest after
  # a terminal ingest failure reports a partially-ingested night as a normal
  # one.
  echo "nightly: FAILED at ingest" >&2
  notify "Wiki nightly FAILED at ingest. No digest was generated; the vault is as the failed run left it. See the cron log."
  exit 1
fi

if ! hermes chat -q "Use the wiki-lint skill on the vault at ${VAULT}. Report contradictions, orphaned pages, and stale citations."; then
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
# Note-and-continue, like the SOUL rebuild below: the digest is this run's
# liveness signal, so a vault that failed its checks has to be reported through
# it rather than silenced by an abort. The pages are already written by now;
# aborting would not unwrite them, it would only withhold the news.
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
# Note-and-continue like the SOUL rebuild: the pages are already written, and
# aborting would withhold the news rather than unwrite them.
if ! "$BIN/build-hubs" "$VAULT"; then
  note "hub rebuild failed; property hubs may not list tonight's pages"
fi

(cd "$VAULT" && uv run --no-project --python 3.13 --with pytest==8.4.2 pytest -q)
rc=$?
if [ "$rc" -eq 1 ]; then
  note "vault integrity FAILED; see the cron log"
elif [ "$rc" -ne 0 ]; then
  note "vault checks could not run (rc=$rc); see the cron log"
fi

# Tonight's pages belong in tomorrow's injected index. Note-and-continue rather
# than abort: the digest below is this run's liveness signal, so a stale SOUL
# must be reported through it, not made silent by skipping the message that
# would have said so.
# $SOUL_OUT defaults to $HERMES_HOME/SOUL.md, which is ~/.hermes/SOUL.md on
# the host — the same file the deploy path writes, through the compose mount.
if ! "$BIN/build-soul" "$VAULT" "$HERMES_HOME/repo/runtime/SOUL.md" "$SOUL_OUT"; then
  note "SOUL rebuild failed; the injected index is stale"
fi

# Bounded for the same reason the aborts are, with room for the real work it
# does: it reads the vault and writes a summary before it sends. This is the
# success path's liveness message, so if it cannot be delivered the run says so
# in the log and exits non-zero rather than sitting on the vault until morning.
if ! timeout 600 hermes chat -q "Use the wiki-digest skill on the vault at ${VAULT} for the last day. Prefix the digest with this run status, verbatim: '${STATUS:-ok}'. Send the result to me over plow_chat. Send it even if nothing changed — this message is how I know the job is alive."; then
  echo "nightly: the digest did not send within 600s" >&2
  exit 1
fi
