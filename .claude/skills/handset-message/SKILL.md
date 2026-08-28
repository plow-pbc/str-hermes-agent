---
name: handset-message
description: Send a real iMessage from the operator's Mac to the Hermes line and read the reply — "text hermes for real", "test the actual phone path", "does the line work", "send a real message not a self-test", "verify pairing". The only check that exercises handset → Apple → Plow; use after a change to the line, the pairing, or the Plow account.
---

# Handset message

Sends a genuine iMessage from this Mac to the number Hermes answers on, then
reads the reply out of the Messages database.

**This runs on the Mac, not on `wakeup`** — the opposite of every other skill in
this repo. It needs Messages.app and `~/Library/Messages/chat.db`, neither of
which exists on the server. From `wakeup` that is one hop:

```sh
ssh so@mbp 'cd ~/Hacking/str9 && HERMES_LINE=<the line Hermes answers on> ./bin/handset-message.py "Reply with PONG"'
```

**Verified over SSH**, which is the non-obvious part: AppleScript automation of
Messages works from a non-GUI session, so the deploy gate does not need someone
sitting at the Mac. Only that the Mac is awake and reachable.

## What it proves

The whole path, in the direction a real message travels: handset → Apple → Plow
→ websocket → the serving gateway → the agent → its tools → the reply back.

There was briefly a cheaper check that injected at Plow's API instead, posting
into the thread as the account itself. It is gone. It bought a probe that ran
without the Mac, and it cost test-only branches in the adapter's live send and
receive paths plus a synthetic pre-authorized identity — which was both an
authorization bypass and the reason the check failed against the real gateway
while its tests passed. This covers strictly more and leaves nothing behind in
production.

**Not the carrier.** The send asks Messages for `service type = iMessage`, so it
goes over Apple's data path; no SMS is involved and nothing here exercises one.

It sends a real message into the operator's real thread. They see every probe, so keep the
text obviously a test.

## Run it

`HERMES_LINE` is the E.164 number Hermes answers on. It lives on the Mac (shell
rc or the command line), never in this tree.

```sh
./bin/handset-message.py 'Reply with PONG'
```

Expect:

```text
sent to +1555…567, waiting for 374c14ff
REPLY: PONG 374c14ff
```

Exit 0 with a `REPLY:` line is the pass; exit 1 prints `TIMEOUT` and the nonce.
The nonce is asked for and required, because our own send carries it too — a
scan that matched the nonce alone would read the probe's own text back and pass
without the agent answering. `HANDSET_TIMEOUT` overrides the 360s default —
which is sized for a cold context compaction, not for the tail (see the
`TIMEOUT` rows below).

The tool-bearing form is the one worth running after a change to a tool surface,
since it exercises the whole path a real question takes:

```sh
./bin/handset-message.py 'Which of my doors are unlocked? One line.'
```

## Permissions — the two halves differ

**Reading needs nothing.** `chat.db` is a plain sqlite file and the terminal
already has Full Disk Access. No prompt, no AppleScript.

**Sending needs AppleScript automation of Messages**, approved once per terminal
app ("Ghostty wants access to control Messages" → Allow). It is not optional and
there is no database shortcut: `chat.db` is a *record*, not a send queue —
writing a row into it sends nothing, because Apple's daemon does the sending and
does not read that database for instructions.

If the approval has not been given, the send fails and the script exits non-zero
without waiting; grant it and re-run.

## Privacy

The query filters on the Hermes thread and nothing else, read-only. That
database holds the operator's personal conversations; the probe has no reason to look at
them and does not.

## Failure triage

| Symptom | Means | Move |
|---|---|---|
| `no Messages database` | running on `wakeup`, not the Mac | `ssh so@mbp` and run it there — the hop at the top of this file |
| `osascript` error about controlling Messages | the one-time automation approval is missing | grant it (System Settings → Privacy & Security → Automation) and re-run |
| `TIMEOUT`, and no `inbound message` line for this chat after the send | the message never reached the gateway. This is the leg only this probe covers — the line, the pairing, or Apple's delivery | check the Plow line and the pairing; a fresh `Hi~ I don't recognize you yet` in the thread means an identity needs `hermes pairing approve` |
| `TIMEOUT`, and `inbound message` present | it reached the gateway, so every leg only this probe covers is proven and whatever is wrong is downstream of them. Which downstream thing, the log cannot settle: the first turn after a restart pays a context compaction (one 240k-token session took 227.7s, and the log carries a 3426.8s turn), and `response ready` only says the gateway composed a reply, with the trip back still ahead of it | **re-run with a larger `HANDSET_TIMEOUT` before concluding anything.** That is the experiment, and it is cheaper than the reasoning it replaces. A nonce-bearing `REPLY:` ends it. Still timing out, with `response ready` landing well inside the longer wait, is what points at the reply leg — Plow delivery or Apple's |
| reply arrives without the nonce | the model ignored the echo instruction | re-run; the reply is visible in the thread either way |

Read the gateway log first when this fails — it is one grep and it is free.
Both lines below were observed on `wakeup` during the deploy that prompted this
table, so the greps are anchored to real output rather than a guessed format
(chat id and body elided here; the live lines carry both, which is what lets
you match one to your send):

```text
2026-08-25 14:49:43,931 INFO gateway.run: inbound message: platform=plow_chat user=<operator> chat=cht_… msg='…' reply_to_id=None reply_to_text=''
2026-08-25 14:53:31,619 INFO gateway.run: response ready: platform=plow_chat chat=cht_… time=227.7s api_calls=3 response=105 chars
```

`inbound message` is the one that discriminates: present, the message crossed
every leg this probe uniquely covers, and whatever is wrong is downstream of
them; absent, it did not, and the line and the pairing are the place to look.
`response ready` is context, not a verdict. Reading its timestamp against the
deadline looks like it should discriminate and does not: the deadline is when
the reply had to have *arrived here*, with the gateway → Plow → Apple →
`chat.db` legs and a 5s poll still to come, so a turn finishing anywhere near
it is indistinguishable from a wait that was simply too short. The re-run is
what tells them apart.

**Neither line is a pass.** They tell you where you are, not that the path
works: `response ready` says the gateway composed a reply, not that it reached
this Mac, and it carries no nonce, so it cannot even prove the turn was yours.
The pass is what it has always been — a nonce-bearing `REPLY:` and exit 0, from
a run whose wait was long enough. When the log says the turn merely outran the
probe, the move is to re-run it, not to write the deploy off as verified.

## Why the body comes out of a blob

`message.text` is NULL on modern macOS; the body lives in `attributedBody` as a
typedstream. A reader that trusts `text` reports "no reply" against a perfectly
healthy system — this repo has already shipped one false timeout, and that would
have been the second. `_body()` in the script owns that parsing.
