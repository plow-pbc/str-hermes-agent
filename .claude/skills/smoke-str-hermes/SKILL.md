---
name: smoke-str-hermes
description: Use to check the deployed Hermes container actually answers — "is hermes alive", "smoke test the agent", "message the container", "did the deploy work", "can it still reach Hostex", "test the STR agent". Sends a real message through the running container and asserts on the reply, rather than inferring health from process state.
---

# Smoke STR Hermes

Drive a real message through the container on `wakeup` and assert on what comes back.

`agent-mgr compose str ps` showing `Up`, a green `agent-mgr up str`, and a `websocket subscribed` log line are all necessary and none of them are evidence. The container can be up with a model route that 401s, an MCP server missing from its config, or an expired credential — each invisible to process state, and each visible the moment you ask it something.

**What these probes do and do not prove.** `hermes chat` starts a *fresh
process* inside the container, which reads `~/.hermes/config.yaml` at its own
startup. So a green run proves the config on disk is good and its integrations
work — it does not prove the long-running gateway reloaded that config. The
only check that exercises the *end-to-end Plow serving path* is a real message
over Plow Chat. Treat steps 1–4 as diagnostics and step 5 as the gate.

That is not the guest path. Guests reach this agent through Hostex: the poller
notices a waiting guest, the agent drafts a reply, and it sends once an owner
approves — on a host that has had README § Enabling it run against it, and a
gateway restarted since the allowlist last changed. Nothing here reaches any of
that. Steps 1-5 drive a Plow message, so a green run says nothing about Hostex
polling, approval, or the send.

Every command below runs **on wakeup**, in the deployed checkout:

```sh
(
  test "$(hostname -s)" = wakeup || { echo "FATAL: not on wakeup"; exit 1; }
  cd ~/services/sams-str-hermes-agent || { echo "FATAL: deployed checkout missing"; exit 1; }
  echo "PREFLIGHT OK"
)
```

Require `PREFLIGHT OK`, then enter the checkout in the active shell — the
**printed path is the confirmation**, since a failing `cd` writes to stderr
and `&&` suppresses only the `pwd`:

```sh
cd ~/services/sams-str-hermes-agent && pwd
```

The host guard catches the failure that does not announce itself: on another
box that also runs this stack, `cd` succeeds and every probe below passes
cleanly against *that* box's container. If it fires, get a session on wakeup
(the `tailscale-ssh` skill maps the host) rather than piping these through
`ssh` inline.

## Guardrails

- **`exec -T … < /dev/null`, not `run`.** `agent-mgr compose str run --rm hermes ...` starts a *throwaway* container from the image; it can pass while the deployed gateway is broken. `exec` runs inside the container that is serving. `-T` is required: without it Compose allocates a TTY and every probe dies with "the input device is not a TTY" from a non-interactive shell or over `ssh`, a false negative unrelated to agent health. `< /dev/null` belongs on every probe for the same reason a new one will: compose attaches stdin, `-T` suppresses only the TTY, and any probe inheriting a script on stdin eats it. (The one legitimate `run` is `auth list` in step 1, which needs `-T` too — it reads the shared `~/.hermes` mount and is the right tool precisely *because* `exec` is hung. It carries `--entrypoint` so that even this exception starts no gateway: the image's own entrypoint boots s6, and a rival gateway is the last thing a hung one needs. `agent-mgr` refuses a `compose run` without the flag for exactly that reason.)
- **Read-only probes.** Ask about reservations, locks, listings. Never `send_message`, never `unlock_door` — a smoke test must not text a guest or open a door.
- **Never print secrets.** On failure report the failure, not the environment.
- Allow a 240s timeout on steps 1–4: liveness is ~5s, a tool-backed probe up
  to ~60s. **Step 5 needs more — allow 420s.** It waits on the serving
  gateway rather than a fresh process, so it inherits that turn's latency
  including a cold context compaction; the script's own wait is 360s, and a
  harness timeout under it kills the probe before its triage line prints.

## 1. Liveness — does the agent answer at all

```sh
agent-mgr compose str exec -T hermes hermes chat -q 'Reply with exactly: PONG' < /dev/null
```

Expect `PONG` in the reply box. This proves the container is serving, the model route resolves, and its credentials are valid.

A hang means the model provider is unreachable or OAuth expired — check with `agent-mgr compose str run --rm -T --entrypoint /opt/hermes/.venv/bin/hermes hermes auth list < /dev/null` (the `run` exception above). An error naming a base URL or provider means the live `~/.hermes/config.yaml` carries a model route production does not run; compare it against tracked `runtime/config.yaml`.

## 2. Tool reachability — does it still reach Hostex

```sh
agent-mgr compose str exec -T hermes hermes chat -q \
  'Use your Hostex tools to tell me how many reservations arrive in the next 30 days. Answer with just the number and the word reservations.' < /dev/null
```

Expect a number and the word `reservations`, **and** a `⚡` tool-call line
naming a Hostex tool somewhere in the transcript (e.g.
`⚡ tool_sear Hostex reservations`). Both, or it is not a pass — a number with
no call line is the model answering from nothing, and a call line with an
implausible number is worth checking against Hostex directly.

This is the probe that matters: it exercises the container and the agent loop
and shows the Hostex tool registered. The call line means the agent reached
the tool — and nothing more.

**A plausible answer is not a pass on its own.** Verified against the running
container: under `-q` the transcript renders the call and its duration —

```text
  ┊ ⚡ tool_sear Hostex reservations  0.0s
```

— and never the result body, so whether the call *succeeded* is not observable
here. `-q` is not optional either; it is the flag that takes the query. No
failing Hostex call has been produced to see what one renders.

Then check the server itself connects — a separate concern from the
credential:

```sh
agent-mgr compose str exec -T hermes hermes mcp test hostex < /dev/null
```

Expect `✓ Connected` and a tool count.

**No probe here checks the credential.** Do not try to test it by overriding
`HOSTEX_TOKEN` — the value comes from the mounted `~/.hermes/.env`, not the
process environment, so the override is ignored and the check still passes.
The count in step 2 is suggestive of a live call; cross-checking it against
Hostex would settle it, and nothing here does that. So do not report the
credential as verified on a smoke run alone.

## 3. Lock surface — is Seam configured

```sh
agent-mgr compose str exec -T hermes hermes mcp test seam < /dev/null
```

Expect every tool named in `runtime/config.yaml` under
`mcp_servers.seam.tools.include` — count them there rather than against a list
here, which is the copy that goes stale.

Two failures, one of which is quieter than the other:

`✗ Server 'seam' not found in config. Available: hostex` means the live config
predates the Seam block. The agent is up, answering, and reads Hostex fine —
and cannot touch a door. Nothing else here catches it; this is the check that
caught it in production.

**A short count is the same failure one step less obvious.** `Tools
discovered: 3` when the allowlist names far more is a gateway serving a config
from before the current deploy: lock and unlock work, so a smoke that only
looks for those three passes, while every access-code tool is simply absent
from chat — and the agent will say it has no such capability rather than
erroring. Read the count, not just the names.

Either one: re-run **`deploy-str-hermes`** steps 3 and 4 — not the two commands
by hand, which skip the guards and the force-recreate.

Stop at `mcp test`. Do not call `lock_door` or `unlock_door` against real
hardware in a smoke test.

## 4. Plow path — connected, with a caveat

Grep each pattern separately and require both. A single `grep -E 'A|B' | tail -2` is satisfied by two matches of the *same* alternative, and the pipe swallows grep's exit status so an empty log reads as a silent pass:

```sh
grep -c '✓ plow_chat connected' ~/.hermes/logs/gateway.log
grep -c 'websocket subscribed' ~/.hermes/logs/gateway.log
```

The two lines have different owners, and only one of them is ours. `✓ plow_chat
connected` comes from `gateway.run`, so it survives whatever the platform
plugin is. `websocket subscribed` comes from the plugin itself — which since
#138 is the upstream one pinned at agent-mgr's `runtime/plow-chat-plugin.ref`, not a copy
in this checkout. So the string moves when that pin moves: if this step reports
zero on a gateway that is demonstrably serving, read the pinned plugin's own
`logger.info` calls before believing it, and update this pattern rather than
the gateway.

It is emitted **once per subscribed chat**, and the log is not truncated at
boot, so `grep -c` counts retained events across every restart — 80 matches
against four chats, on a host where the gateway has come up many times. Neither
count means anything but non-zero; the timestamp comparison below is what
speaks to *this* boot, and `tail -1` there is the newest subscription rather
than the connection.

Both must be non-zero. They must also postdate the last restart — old lines
from a previous boot are the trap, so compare the newest of each against when
the container actually started:

```sh
docker inspect -f '{{.State.StartedAt}}' hermes
grep '✓ plow_chat connected' ~/.hermes/logs/gateway.log | tail -1
grep 'websocket subscribed' ~/.hermes/logs/gateway.log | tail -1
```

**These two are in different zones — convert before comparing.** `StartedAt` is
RFC3339 `Z`, always UTC; Docker emits nothing else. `gateway.log` prefixes
`YYYY-MM-DD HH:MM:SS,mmm` through Python logging, which renders in the
*container's* local time — and `AGENT_TZ` in `agent.env` sets that to
`America/Los_Angeles`, so the log reads Pacific and matches wakeup's own
`date`:

```text
StartedAt:   2026-07-31T23:46:54.015288458Z
gateway.log: 2026-07-31 16:46:59,252 INFO gateway.run: ✓ plow_chat connected
```

Five seconds apart, not seven hours. Compare the fields directly and every
post-restart line reads as ~7 hours *before* the restart, so the step fails on
a healthy gateway. Subtract the Pacific offset from `StartedAt` (7 hours in
PDT, 8 in PST), or read `StartedAt` against `date -u` and the log against
`date`.

Before `TZ` was set the container ran UTC and both sides were
directly comparable — which is why an older transcript of this step compares
them with no conversion and still looks right.

**This is a proxy for delivery, not proof.** It shows the gateway holds a websocket to Plow. It does not show that a text from the operator's phone reaches the agent and gets a reply — that involves the Plow line, the pairing, and the home binding in `~/.hermes/.env`, and only a real text exercises it.

## 5. The serving gate — a real message from a handset

Steps 1–4 all run new processes or read logs. Run **`handset-message`**, which
sends a genuine iMessage from the operator's Mac and reads the reply. From here that is
one hop:

```sh
ssh so@mbp 'cd ~/Hacking/str9 && HERMES_LINE=<the line Hermes answers on> ./bin/handset-message.py "Reply with PONG"'
```

A `REPLY:` line and exit 0 is the pass. Ask a tool question instead when the
deploy touched a tool surface — see that skill, which owns the contract and the
triage.

**A `TIMEOUT` is not automatically a dead line** — the first turn after a
restart can pay a context compaction and finish after the probe gives up. Take
that to the handset skill's triage table, which owns it and reads the gateway
log to place the failure. It stays a `TIMEOUT` here either way: a longer re-run
that comes back with `REPLY:` is what turns it into a pass.

**If it has not been run, the deploy is `unverified`, not passed.** Report it
that way — four green diagnostics with this step absent is precisely the shape
the top of this file exists to prevent.

This step has been three things. It read "ask the operator to text the Plow number — an
agent cannot run it", which left every agent-run deploy `unverified` by
construction. Then it ran a self-test that posted into the thread as the
account itself — agent-runnable, but it needed test-only branches in the
adapter's live send and receive paths, and the synthetic identity it invented
was both an authorization bypass and the thing that made the check fail against
the real gateway. Now it is a real message from a real handset, which needs
nothing in the production path and covers strictly more: the leg *into* Plow,
over iMessage, where the line and the pairing live.

Verified over SSH from `wakeup` — AppleScript automation of Messages works from
a non-GUI session, which is not obvious and is the assumption this hop rests on.

## 6. Report

One line per probe: **pass, fail, or unverified**. Step 5's outcome is whatever
`handset-message` reports — that skill owns the vocabulary and the triage
table, and restating it here is what made this paragraph wrong three times
running. Unverified means only that it has not been run.

Say what came back, and for a failure what it implies about the deploy. Do not
summarize a partial pass as healthy, and do not report a deploy as verified on
diagnostics alone.

| Probe | Proves | Blind to |
|---|---|---|
| liveness | container serving, model route valid | tools, Plow |
| Hostex `chat` | agent loop reached the tool | whether the call succeeded |
| `mcp test hostex` | the server connects and registers tools | the credential |
| `mcp test seam` | lock surface configured | whether locks respond |
| Plow log | gateway holds the socket | delivery, and the home binding in `~/.hermes/.env` |
| handset message | the whole path a real message takes, including the line and the pairing | Hostex guest intake |
