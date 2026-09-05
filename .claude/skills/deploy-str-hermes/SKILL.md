---
name: deploy-str-hermes
description: Use when deploying this repo to wakeup — "deploy hermes", "deploy the STR agent", "push this to wakeup", "update the prod clone", "restart the gateway with the new config". Fast-forwards ~/services/sams-str-hermes-agent to merged main, applies runtime/ to ~/.hermes, brings up the Compose service, and verifies the container is actually serving.
---

# Deploy STR Hermes

Deploy merged `main` to the container that actually runs on `wakeup`.

| | |
|---|---|
| Host | `wakeup` |
| Checkout | `~/services/sams-str-hermes-agent` — **this is what runs** |
| Container | `hermes`, Docker Compose, `restart: unless-stopped` |
| State | `~/.hermes` on the host, mounted at `/var/lib/hermes` |

This is the **redeploy** path: an existing host, already bootstrapped. First-time
setup of a host — credentials, installing and registering `agent-mgr`,
OAuth, Plow activation — is the README's `Restoring runtime config` section, and
is not duplicated here.

## Where these run

Every command below runs **on wakeup**, in the deployed checkout:

```sh
(
  test "$(hostname -s)" = wakeup || { echo "FATAL: not on wakeup"; exit 1; }
  cd ~/services/sams-str-hermes-agent || { echo "FATAL: deployed checkout missing"; exit 1; }
  git check-ignore -q .env || { echo "FATAL: .env not gitignored — the tree will read dirty forever"; exit 1; }
  echo "PREFLIGHT OK"
)
```

These `( … )` blocks are pasted into an interactive shell, so keep `!` out of
the guard messages — history expansion would swallow the block.

Require `PREFLIGHT OK`, then enter the checkout in the active shell — the
subshell's `cd` does not persist, and the **printed path is the confirmation**
(a failing `cd` writes to stderr and `&&` suppresses only the `pwd`):

```sh
cd ~/services/sams-str-hermes-agent && pwd
```

The host guard is the one covering a failure that does not announce itself:
because `~/services/<repo>` is the standing convention, on another box that
also runs this stack `cd` succeeds, step 1 may well pass, step 2
fast-forwards *that* box's clone, step 3 overwrites *its* config, and step 4
restarts *its* container — a clean, successful deploy of the wrong host,
reported as success, while wakeup stays exactly as stale as it was.

If you are not on wakeup, get a session there first — the `tailscale-ssh`
skill maps the host — rather than piping these through `ssh` inline.

## Guardrails

- **Never `git push`** from the prod clone. Production never originates commits.
- **Never deploy past a `STOP` from `./scripts/check-deploy-clean.sh`.** The vault lives outside the checkout now (`~/hermes-vault`), so nothing in a prod clone is exempt — any dirty path means someone edited production directly, or recreated a vault inside the checkout — stop and ask.
- **Pull `--ff-only`, on `main` only.** A non-fast-forward means prod diverged; stop and investigate.
- **Never** force-push, `--no-verify`, `git stash`, `git reset --hard`, `git clean`, or `git checkout -- <path>`.
- **Never print secret values.** `~/.hermes/.env` holds the Hostex, Seam, and Plow credentials; check presence or last 3 chars, never `cat` it.
- **Restore replaces `~/.hermes/config.yaml` and `~/.hermes/SOUL.md` wholesale** — the SOUL is composed from `runtime/SOUL.md` plus the vault index, so a host-side edit to it is lost; edit `runtime/SOUL.md` instead. It also overlays the deploy-owned seed into the runtime vault — `AGENTS.md` and `.env` — so an edit made to those *in the vault* is lost on the next deploy; edit `runtime/vault-seed/` instead. The property hubs under `properties/` are the vault's own: edit their prose there, it survives a deploy. One carve-out: a hub's `## Operations` list survives nowhere — `bin/build-hubs` regenerates it from the vault's own pages immediately after the overlay, and again on every nightly. Rename the page, don't edit the link. `agent-mgr deploy str` installs the pinned Plow Chat plugin as part of the same command, so it is the whole of "apply `runtime/`". What it does not touch is the agent's `.env`: the `/sethome` home target lives there as `PLOW_CHAT_HOME_CHANNEL`, so a redeploy does not unbind the home chat.

## 1. Confirm the checkout is deployable

```sh
./scripts/check-deploy-clean.sh   # must print DEPLOYABLE
git branch --show-current         # expect main
```

The script checks the full tree, untracked files included, and `DEPLOYABLE`
now means what it says: a clean tree. The vault used to be the runtime's
writable area inside the checkout, exempted path by path — it now lives
entirely outside the checkout, at `~/hermes-vault`, mounted in rather than
tracked. A clean prod clone is the normal state; any dirty path, `vault/`
included, means someone edited production directly or recreated a vault
inside the checkout. The Plow Chat plugin is no longer part of this
tree — it is installed into `~/.hermes/plugins` from a pinned upstream SHA, so
a file hand-dropped *there* is running in production and this check cannot see
it. The plugin install inside `agent-mgr deploy str` (step 3) rewrites only the files it
manages under `plugins/plow-chat-platform/`, so it bounds drift in *that*
plugin and nothing more — a sibling directory dropped into `~/.hermes/plugins`
is loaded by the gateway and is checked by neither this gate nor the installer.

**A `STOP` → stop and ask.** It prints what it found. Do not clean it.

**`no such file` on the first deploy after #68.** The script arrives *in* the
commit this step gates, so the one deploy that installs it cannot run it. For
that deploy only, fall back to `git status --porcelain` by hand and require
it print nothing at all — a clean tree is the whole contract now, there is no
exempt path to carve out. Delete this paragraph once that deploy has
happened; it is a rollout note, not a fallback.

**Not on `main`** is common in this clone and usually safe. `git fetch origin --prune`, then reseat **only if both** hold:

```sh
./scripts/check-deploy-clean.sh                        # must print DEPLOYABLE
git rev-parse HEAD                                    # equal to:
git rev-parse "origin/$(git branch --show-current)"   # this
```

If the branch has no upstream, `git rev-parse origin/<branch>` **errors** — count that as a failure, not a pass. Both conditions pass → `git checkout main`. Either fails → the clone may hold work that exists nowhere else. Stop, surface it, let the human decide. Never discard it.

The commonest way to land there is a merged PR: `gh pr merge --delete-branch`
removes the upstream, so a clone left on that branch trips the check even
though its content is merged. Resolvable, but prove both halves — that the PR
merged, and that this content is in it:

```sh
gh pr list --state all --head "$(git branch --show-current)" --json number,state
git fetch origin "refs/pull/<N>/head:refs/remotes/pr<N>head"
t=$(git rev-parse HEAD^{tree}) && git log --format='%T %h %s' "pr<N>head" | grep "^$t "
```

The first command answers what the clone cannot: which PR, and whether it
merged. It survives the branch deletion because GitHub keeps the PR record.
Feed its number in as `<N>`. `[]` means no PR ever carried this branch — stop.
More than one row means the name was reused; take the `MERGED` one, and the
tree check settles it.

Both must hold. A `MERGED` state plus a hit means this tree already exists in
the history reachable from that PR, so `main` carries the content — reseat.
Anything else is the real stop: a closed-unmerged PR proves nothing, and no
hit means the content is only here.

Bind `t` first rather than substituting inline — an empty `rev-parse` would
collapse the pattern to `^`, match every line, and read as a hit. Every other
failure here stops; that one discards.

By tree, because neither alternative works. `--is-ancestor` fails when the
prod clone committed the work itself rather than fetching it: same content,
different object, no ancestry. Patch-ids fail because the merge was squashed.

## 2. Fast-forward and record what shipped

```sh
(
  git fetch origin main
  git merge-base --is-ancestor HEAD origin/main || { echo "FATAL: HEAD is not on origin/main — unpushed or diverged work here"; exit 1; }
  before=$(git rev-parse HEAD)
  git pull --ff-only origin main
  git log --oneline "$before..HEAD"
)
```

The fetch is unconditional and the ancestor check precedes the pull, because a
clean `main` carrying an unpushed commit passes step 1, and `--ff-only` then
reports "Already up to date" and deploys code that was never merged.

An empty log means nothing new — say so rather than reporting a deploy.

## 3. Build, then apply the tracked runtime config

Build first. `agent-mgr deploy` derives the boot contract — the home target
the vault seed's `.env` and every mount are written against — from the image
that is present locally, and it only builds one when none is. On a redeploy
the stale image is present, so a deploy before the build would seed the vault
for the old contract and the recreate below would then boot the new one over
it. The build is safe before anything knows the contract: nothing in it reads
`AGENT_HOME_TARGET`.

```sh
agent-mgr compose str build
```

```sh
(
  test -f ./scripts/restore-runtime-config.sh || { echo "FATAL: restore script absent — wrong checkout, or this main predates it"; exit 1; }
  test -x ./scripts/restore-runtime-config.sh || { echo "FATAL: restore script present but not executable — chmod +x it"; exit 1; }
  # Both hooks asserted, not assumed. `resolve` prints only the keys agent-mgr
  # itself owns, so this fails on a build that predates the pre-transition hook
  # -- which would silently remove the nightly guard from every transition while
  # agent.env still declared it and every test stayed green.
  agent-mgr resolve str | grep -q '^AGENT_PRE_TRANSITION=' \
    || { echo "FATAL: the installed agent-mgr does not implement AGENT_PRE_TRANSITION — the nightly guard would NOT run. Update ~/services/agent-mgr."; exit 1; }
  agent-mgr resolve str | grep -q '^AGENT_LIVE=' \
    || { echo "FATAL: the installed agent-mgr does not implement AGENT_LIVE — transitions would not ask first. Update ~/services/agent-mgr."; exit 1; }
  # This repo declares AGENT_DEPLOY_HOOK, but nothing here can see whether
  # agent-mgr honoured it. Unhonoured, config.yaml still
  # lands, step 4 still recreates the container, and the gateway comes up
  # listing its platforms over an un-overlaid vault, empty hub lists and a stale
  # SOUL -- every check green, the deploy shipping nothing. The hook's own
  # closing line is the one signal that it ran.
  out=$(agent-mgr deploy str) || { printf '%s\n' "$out" >&2; echo "FATAL: agent-mgr deploy str failed"; exit 1; }
  printf '%s\n' "$out" >&2
  printf '%s\n' "$out" | grep -q '^Restored tracked Hermes configuration to ' \
    || { echo "FATAL: agent-mgr deploy did not run this repo's AGENT_DEPLOY_HOOK — the vault seed, hubs and SOUL were NOT applied"; exit 1; }
)
```

A subshell again, for the same reason as the preflight: a firing guard must
not close the session.

Absent and non-executable are separated because they need opposite repairs.
If the *absent* guard fires, stop — do not continue to step 4. Check `pwd`
first: these guards are cwd-relative, so the commonest cause is being outside
the checkout. If you are in it, then this `main` predates the script —
`runtime/` and it arrive together — so check out a `main` that contains them
or deploy the branch that adds them. Either way, bringing the gateway up
anyway means step 4 runs against whatever stale `~/.hermes/config.yaml` is
already there.

`runtime/config.yaml` is canonical for what this repo owns — the Hostex allowlist and the Seam server. The model route is not in it: the base image's `plow-init` writes `model`/`providers` into the live config from its seed at every boot, so a route problem is diagnosed in `~/.hermes/config.yaml` and the container's boot log, never repaired in the tracked file. Editing the live copy for anything else is how the two drift, and the next deploy silently wins.

One command, because `agent-mgr` owns the deploy end to end: it creates the
home, installs `runtime/config.yaml` (named by `AGENT_CONFIG` in `agent.env`)
and the pinned plugin, then runs this repo's own deploy hook
(`AGENT_DEPLOY_HOOK` → `scripts/restore-runtime-config.sh`) for the vault seed,
the hub rebuild and the composed SOUL — and reloads the gateway once at the end.
A failing hook fails the deploy, so a refusal cannot read as a landed deploy.

It used to be the other way round: the script hardcoded the home and
re-implemented the config install, which is how `agent-mgr deploy str` came to
be broken without anyone noticing. It is a reconcile, not a first-time install —
verified against the pinned installer rather than assumed: a second run
rewrites every file it manages, so a bumped pin swaps the adapter and a
hand-edited copy is reverted. It does *not* clear the directory first, so a
stray inside `plugins/plow-chat-platform/`, and anything else under
`~/.hermes/plugins`, survives every deploy and is still loaded.

A non-zero exit here most commonly means the runtime vault is missing at
`~/hermes-vault` — the script refuses rather than seeding a vault-less deploy,
which would read as healthy while the agent had schema and no facts. It can
also be the plugin install refusing agent-mgr's `runtime/plow-chat-plugin.ref` when it is
not a 40-char SHA, or failing to fetch it; the message names which. See
Troubleshooting.

## 4. Bring it up

```sh
agent-mgr compose str up -d --force-recreate
```

**Stop if it refuses.** Force-recreating the container mid-ingest can land
between a page write and its manifest entry, and the vault keeps the page — so
the next run re-ingests that conversation and appends its facts a second time.
Nothing reports it; the pages just quietly say things twice. Wait for the run to
finish.

Ordered right after the deploy, not before the build: the build is minutes
long, and a 03:00 fire can start in anything sitting between the check and the
transition it guards. agent-mgr owns when it runs; `agent.env` owns which
script it is.

`--force-recreate`, not bare `up -d`: with the image unchanged, `up -d` is a
no-op that prints `Container hermes Running` and leaves the gateway serving
the configuration it loaded at its last start — including the one step 3 just
replaced. And `up`, not `restart`, because Compose substitutes the environment
at container create time.

`build` ahead of it in step 3, and no `pull` at all. The image is derived here rather than
tracked upstream: the Dockerfile layers `obsidian-wiki` onto the pinned base so
the nightly wiki chain has the skills it runs on. `--force-recreate` recreates
the container from whatever image already exists, so without the build a deploy
carrying a Dockerfile change — or a change to the skill-linking script — ships
the previous image and the chain runs against the wrong tooling. The build is a
cache hit and costs seconds when nothing changed.

Pulling would be wrong for the same reason it always was: every input is pinned,
so `build` reproduces what is already running and picks up only this
repository. Upgrading means bumping a pin deliberately, which is the README's
own separate step.

No `/sethome` afterwards. Hermes persists the home target to `~/.hermes/.env`
(`PLOW_CHAT_HOME_CHANNEL`, `PLOW_CHAT_HOME_CHANNEL_THREAD_ID`), and step 3
installs `config.yaml` and the composed `SOUL.md`, never `.env` — so the
binding survives a redeploy untouched.

## 4.5 End the group's per-member sessions, once

Directly after step 4, and before anything else: the gateway is up and its
scheduler is running, so every minute this waits is a `hostex-inbound` tick
that can announce a draft the mirror still skips.

The mirror resolves a session by chat id and returns nothing when the chat has
several open sessions belonging to different people, so a group carrying one
session per member is a group whose drafts arrive with nothing behind them —
the delivery succeeds and the job reports ok either way (#84). Step 3 installs
`group_sessions_per_user: false`, but that only governs sessions opened *after*
the restart above; the ones already open stay open, and two of those are still
two.

Run README § [End the group's per-member
sessions](../../../README.md#shared-group-session) — after step 4, because
before it the next message just opens another one. It is its own gate: with
nothing per-member left it ends nothing and prints `0`.

## 4.6 Retarget the inbound job, once

A `hostex-inbound` job created before guest-reply drafts moved to the owners'
group still delivers to the private chat, and one created before the delivery
mirror has no `origin`, so its drafts never reach the session that approves
them. Nothing above changes either — the enable script refuses to run while a
job exists, so a redeploy recreates the container around the old job. `origin`
is not in `cron list`, so an inspection cannot rule the second one out.

```sh
agent-mgr compose str exec -T hermes hermes cron list
```

No `hostex-inbound` job — skip. A job predating either change is recreated, not
diagnosed; work through README § [One-time: point an existing job at the owners'
group](../../../README.md#owners-group-migration), which owns this path.

## 4.7 Register the host-side promote, once

`bin/nightly.sh` compiles the corpus inside the container and commits nothing —
it cannot, since `~/hermes-vault.git` is not mounted there and the gateway holds
no git credential. Promoting is a **host** cron entry, so nothing in steps 1–4
installs it, and a checkout that redeploys cleanly can still leave every night's
output on one disk. That is how 22 days of compiled guest knowledge went
unpushed.

```sh
crontab -l 2>/dev/null | grep promote-vault
```

**Read the line, don't take a green word for it** — `grep -q` would pass a
commented-out entry, or one pointing at a path this host does not have. What
should come back is the README's line verbatim, uncommented, naming
`~/services/sams-str-hermes-agent`.

Nothing printed, or the wrong line — install it, keeping whatever else is in the
crontab:

```sh
{ crontab -l 2>/dev/null; \
  echo '30 4 * * * cd ~/services/sams-str-hermes-agent && ./scripts/promote-vault >> ~/.promote-vault.log 2>&1'; \
} | crontab -
crontab -l | grep promote-vault
```

One line in, verbatim from README § [Nightly](../../../README.md). The `grep`
after is the confirmation and it prints the line rather than a verdict, because
`crontab -` exits 0 whether or not what you piped it landed.

## 5. Verify

```sh
agent-mgr compose str ps --format '{{.Name}} {{.Status}}'
```

Expect `hermes Up ...`. `Restarting` is a crash loop — see Troubleshooting.

Container status is where this skill stops. It proves a process exists, not that the agent answers or that its tools resolve — so hand off to **`smoke-str-hermes`**, which drives a real message through the running container. Do not re-implement those probes here; they live in one place so the two files cannot drift.

## 6. Report

State what shipped (the `git log` range from step 2), the smoke results, and
anything skipped.

**A deploy is `unverified` until a real message has gone through the serving
gateway and come back** (`smoke-str-hermes` step 5, which runs
**`handset-message`** — one `ssh so@mbp` from here). Diagnostics alone cannot
close it: every other probe starts a fresh process or reads a log. A `REPLY:`
line and exit 0 closes it, and an agent can run that, so report the deploy
verified once it does.

## Troubleshooting

| Symptom | Cause | Move |
|---|---|---|
| `Restarting` loop | bad config or missing credential | `agent-mgr compose str logs --tail 50 hermes`; `~/.hermes/logs/gateway.log` |
| `mcp test seam` says not found | live config predates the Seam block, or step 3's script was missing | re-run steps 3 and 4; confirm `mcp_servers.seam` is in `runtime/config.yaml` |
| Plow never connects | `PLOW_CHAT_*` missing or blank in `~/.hermes/.env` | check key presence only, never print values; if `ls ~/.hermes/plugins` is empty, re-run steps 3 and 4 — installing without the recreate leaves the plugin on disk and unloaded; if the keys themselves are missing, re-activate per README § Plow Chat |
| `Restarting` loop, log names a `PLOW_CHAT_GROUP_UIDS` problem | a group entry in `~/.hermes/.env` is malformed or collides | fix the entry the log names — entries are `<cht_ id>=<display name>`, README § Plow group chats; do **not** reactivate, the credentials are fine |
| Files in `~/.hermes` owned by `501`, or by another account | the container was created by a different account, or by hand outside `agent-mgr` | agent-mgr takes the ids from `id -u`/`id -g` at every invocation, so there is nothing to edit: re-own the directory (`sudo chown -R $(id -u):$(id -g) ~/.hermes`), then `agent-mgr compose str up -d --force-recreate` — `restart` will not re-substitute |
| Agent ignores the home chat | home binding unset or stale in `~/.hermes/.env` | `./scripts/check-home-binding.sh` for the verdict; `/sethome` fixes UNSET and STALE, and takes effect live |
| `restore: no runtime vault at …` | `~/hermes-vault` absent — first deploy on this host, or it was moved/deleted | clone it per the README's § Restoring runtime config — **not** a plain `git clone`, which puts `.git` inside the vault worktree (#89) — then re-run step 3 |
