---
name: deploy-str-hermes
description: Use when deploying this repo to wakeup — "deploy hermes", "deploy the STR agent", "push this to wakeup", "update the prod clone", "restart the gateway with the new config". Fast-forwards ~/services/sams-str-hermes-agent to merged main, applies runtime/ to /var/lib/hermes, brings up the Compose service, and verifies the container is actually serving.
---

# Deploy STR Hermes

Deploy merged `main` to the container that actually runs on `wakeup`.

| | |
|---|---|
| Host | `wakeup` |
| Checkout | `~/services/sams-str-hermes-agent` — **this is what runs** |
| Container | `hermes`, Docker Compose, `restart: unless-stopped` |
| State | the named volume `sams-str-hermes-agent_agent-home`, mounted at `/var/lib/hermes`. There is no host path — a stale `/var/lib/hermes` from before the cutover may still exist and is NOT what the agent reads |

This is the **redeploy** path: an existing host, already bootstrapped. First-time
setup of a host — credentials, installing and registering `agent-mgr`,
OAuth, Plow activation — is the README's `Bringing it up` section, and
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
- **Never print secret values.** `/var/lib/hermes/.env` holds the Hostex and Seam credentials and the Plow chat *configuration*; the Plow credential itself is `~/.plow-credentials-str`. Check presence or last 3 chars in either, never `cat` them.
- **Every boot replaces `/var/lib/hermes/config.yaml` and `SOUL.md` wholesale** — `docker/cont-init.d/05-install-agent-payload.sh` reinstalls both from what the image bakes, so a host-side edit to either is lost at the next boot; edit `runtime/` and rebuild. The property hubs under `properties/` are the vault's own: edit their prose there, it survives. One carve-out: a hub's `## Operations` list survives nowhere — `bin/build-hubs` regenerates it from the vault's own pages on every nightly. Rename the page, don't edit the link. The vault seed no longer overlays into the vault at all ([#43](https://github.com/plow-pbc/str-hermes-agent/issues/43)): the live `AGENTS.md` and `.env` belong to the vault repo. What no boot touches is `/var/lib/hermes/.env`: the `/sethome` home target lives there as `PLOW_CHAT_HOME_CHANNEL`, so a rebuild does not unbind the home chat.

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
tree — it is installed into `/var/lib/hermes/plugins` from a pinned upstream SHA, so
a file hand-dropped *there* is running in production and this check cannot see
it. The plugin install baked into the image (step 3) rewrites only the files it
manages under `plugins/plow-chat-platform/`, so it bounds drift in *that*
plugin and nothing more — a sibling directory dropped into `/var/lib/hermes/plugins`
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

## 3. Build the image

There is no separate deploy step any more, and no hook whose output to check.
`runtime/config.yaml` and `runtime/SOUL.md` are baked into the image, and
`docker/cont-init.d/05-install-agent-payload.sh` reinstalls both into the home on
every boot — unconditionally, because a named-volume home seeds from the image
only while empty and would otherwise shadow every later revision
(plow-hermes-agent#58). Applying a `runtime/` edit **is** the build.

```sh
docker compose build
```

`build` explicitly, never a bare `up -d`: compose builds only when the tagged
image is absent, and on a redeploy it never is. A bare `up` would recreate the
container on the old image and report success — the same silent-no-op class the
old `--force-recreate` note below guards against, one layer earlier.

`runtime/config.yaml` is canonical for what this repo owns — the Hostex allowlist and the Seam server. The model route is not in it: the base image's `plow-init` writes `model`/`providers` into the live config from its seed at every boot, so a route problem is diagnosed in `/var/lib/hermes/config.yaml` and the container's boot log, never repaired in the tracked file. Editing the live copy for anything else is how the two drift, and the next boot silently wins.

The vault is no longer this step's business either. `agent-mgr deploy` used to run
`scripts/restore-runtime-config.sh`, which refused without a vault at
`~/hermes-vault`; `docker/cont-init.d/04-require-vault-corpus.sh` enforces the
same rule at boot instead. A boot check is strictly better: it runs every time
rather than only on deploy, and it parks rather than coming up wrong.

## 4. Bring it up

```sh
just restart
```

`just restart`, not `docker compose up -d --force-recreate` directly. agent-mgr
invoked `scripts/no-nightly-running` as `AGENT_PRE_TRANSITION` before every
transition; compose has no such hook, so the justfile recipes are the only thing
left that can refuse. Reaching for `docker compose` here is the bypass the veto
exists to prevent.

**Stop if it refuses.** Force-recreating the container mid-ingest can land
between a page write and its manifest entry, and the vault keeps the page — so
the next run re-ingests that conversation and appends its facts a second time.
Nothing reports it; the pages just quietly say things twice. Wait for the run to
finish.

Ordered after the build, not before: the build is minutes long, and a 03:00 fire
can start in anything sitting between the check and the transition it guards.
The recipe is what sequences the two.

`--force-recreate` under the hood, not bare `up -d`: with the image unchanged,
`up -d` is a no-op that prints `Container hermes Running` and leaves the gateway
serving the configuration it loaded at its last start. And `up`, not Docker's
`restart`, because Compose substitutes the environment at container create time.

`/var/lib/hermes/.env` is untouched by any of this, so the `/sethome` home
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
docker compose exec -T hermes hermes cron list
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
docker compose ps --format '{{.Name}} {{.Status}}'
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
| `Restarting` loop | bad config or missing credential | `docker compose logs --tail 50 hermes`; `/var/lib/hermes/logs/gateway.log` |
| `mcp test seam` says not found | live config predates the Seam block, or step 3's script was missing | re-run steps 3 and 4; confirm `mcp_servers.seam` is in `runtime/config.yaml` |
| Plow never connects | `PLOW_AGENT_TOKEN` missing from `~/.plow-credentials-str` — not the dotenv, which carries no Plow credential | check key presence only, never print values; if `ls /var/lib/hermes/plugins` is empty, re-run steps 3 and 4 — installing without the recreate leaves the plugin on disk and unloaded; if the credential itself is missing, re-mint it with `plow-pbc/plow-agents` — not README § Plow Chat activation, whose remedy cannot re-mint for an agent that already holds a line (plow-pbc/str-hermes-agent#31) |
| `Restarting` loop, log names a `PLOW_CHAT_GROUP_UIDS` problem | a group entry in `/var/lib/hermes/.env` is malformed or collides | fix the entry the log names — entries are `<cht_ id>=<display name>`, README § Plow group chats; do **not** reactivate, the credentials are fine |
| Files in the home volume owned by `1000`, or by another account | the home was migrated off a bind mount, where it carried the host account's ids | the image runs the agent as `10000:10000` and no longer takes the ids from the invoking account, so re-own it from inside — **pruning the vault**, which is a host bind holding the operator's own checkout: `docker compose exec -u root hermes find /var/lib/hermes -path /var/lib/hermes/repo/vault -prune -o -exec chown 10000:10000 {} +`, then `just restart`. `-prune`, not `chown -R` and not `find -xdev`: the bind shares a device with the volume (`stat -c %D` reports `10302` for both), so `-xdev` crosses it and a bare `-R` re-owns 316 vault files to a uid the host account cannot write |
| Agent ignores the home chat | home binding unset or stale in `/var/lib/hermes/.env` | `./scripts/check-home-binding.sh` for the verdict; `/sethome` fixes UNSET and STALE, and takes effect live |
| boot parks on `vault has no index.md` / `vault/.git is inside the worktree` | `~/hermes-vault` absent, empty, or cloned the wrong way — first bring-up on this host, or it was moved/deleted | clone it per the README's § Bringing it up — **not** a plain `git clone`, which puts `.git` inside the vault worktree (#89) — then re-run step 4. `docker/cont-init.d/04-require-vault-corpus.sh` is what refuses |
