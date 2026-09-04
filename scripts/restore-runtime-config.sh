#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# From the descriptor, via agent-mgr, rather than a second spelling of a path
# agent.env already owns. Three copies used to be coupled by an exact-literal
# test; now there is one owner and the fence is unnecessary.
vault="${STR_VAULT:?agent-mgr did not export STR_VAULT -- run me through 'agent-mgr deploy str'}"

# The corpus is not created here, and not synthesized. It arrives by cloning the
# data repo. An empty vault would bring the agent up with the schema and no
# facts, which reads exactly like a healthy deploy — the quietest failure this
# repo can ship, and the one build-soul's own guard exists to prevent. Checked
# before this script writes anything, so a refusal leaves the vault and the
# composed SOUL untouched.

# Keyed on index.md, not on the directory. `docker compose up -d` creates a
# missing bind source as an empty root-owned directory, so a bare `-d` passes on
# a vault with no corpus in it — restore would then install the seed and only
# fail later at build-soul, after mutating the vault. index.md is what the SOUL
# is composed from, so a present-but-empty one fails exactly like none at all —
# hence -s, not -f. A symlinked index fails here too: `build-soul` refuses to
# dereference one, and catching it before the seed install is what keeps a
# refusal from leaving the vault half-written.
if [ -L "$vault/index.md" ] || [ ! -s "$vault/index.md" ]; then
  {
    echo "restore: no usable runtime vault at $vault (index.md missing, empty, or a symlink)"
    echo "  clone it first — README § Restoring runtime config has the sequence."
    echo "  NOT a plain \`git clone\`: that puts .git inside the vault worktree,"
    echo "  which is #89 again. The external-git-dir form is there for that reason."
  } >&2
  exit 1
fi

# The vault must never be a git repository — a reachable .git inside it is #89
# again, where an ingest turn ran `git restore --source=HEAD` over pages it
# judged missing. Its history lives in $vault.git, outside the worktree; a
# plain `git clone` here means the recovery instructions above were not
# followed. Silent otherwise, so this makes that failure loud.
if [ -e "$vault/.git" ]; then
  echo "restore: $vault/.git exists — the vault must not be a git repository (#89)." >&2
  echo "  re-clone with the external-git-dir form documented above" >&2
  exit 1
fi

# Resolved after the vault guards, not before: a refused deploy must touch
# nothing at all, and asking agent-mgr is a side effect however small.
# Run BY agent-mgr, which exports the home it resolved -- so this asks for it
# rather than assuming it, and without a second resolve. Not a standalone
# entry point: `agent-mgr deploy str` is, and it sequences this after the
# config and plugin installs.
hermes_home="${AGENT_HOME:-}"
[ -n "$hermes_home" ] || {
  echo "restore: run me through agent-mgr, which owns the deploy:" >&2
  echo "  agent-mgr deploy str" >&2
  exit 1
}

# Established here, with the other required inputs, and used only after the
# seed copy below -- validate-then-write, not write-then-mutate. Required
# (`:?`), not defaulted, so an agent-mgr that predates the export fails here,
# before anything is written, rather than after the seed copy has already
# landed a config pointing at an unmounted vault.
container_vault="${AGENT_HOME_TARGET:?agent-mgr did not export AGENT_HOME_TARGET -- run me through 'agent-mgr deploy str'}/repo/vault"

umask 077

# The hand-authored half of runtime/. The nightly owns
# everything else in that directory and never writes these, so the two owners
# do not overlap.
# The whole directory, not an enumeration of what is in it today. Naming the
# files here made runtime/vault-seed/ a schema this script and `just test-wiki`
# each restated, so adding a hub or a config file meant editing three places and
# a deploy silently shipped whichever two had been remembered.
#
# --remove-destination, and it is load-bearing. This copy runs on the host as
# the operator, into a directory the agent writes. A planted RELATIVE symlink
# resolves differently on each side: `AGENTS.md -> ../.ssh/authorized_keys`
# lands on /opt/data/repo/.ssh inside the container (nothing there) and on
# ~/.ssh on the host — which no mount exposes, so it is a real crossing rather
# than a slower route to what the agent already holds, and the payoff is an SSH
# lockout. Plain `cp -a` follows it and writes through; --remove-destination
# unlinks first, so the link is replaced instead. A symlinked *directory* is not
# replaceable that way and aborts the copy under `set -e` with nothing written,
# which is the right end state for a condition the deploy never creates.
cp -a --remove-destination "$repo_root/runtime/vault-seed/." "$vault/"

# The seed's OBSIDIAN_VAULT_PATH is a placeholder: obsidian-wiki reads its
# own .env as plain KEY=VALUE, with no ${VAR} expansion of its own, so the
# copy above cannot leave it pointing at wherever the vault actually mounts
# -- this script is the one layer left that can still resolve it.
# container_vault is established above, with the other required inputs.
sed -i "s|^OBSIDIAN_VAULT_PATH=.*|OBSIDIAN_VAULT_PATH=$container_vault|" "$vault/.env"

# The hubs are the vault's own (the seed ships none — the property list is the
# operator's), and their Operations lists are rebuilt here rather than left to
# the next nightly: the 2026-08-24 deploy reverted a hub and that property's
# newest page was unreachable from it until the next run.
#
# Under `set -e`, so a vault build-hubs cannot derive — a model-authored
# `title:` that does not carry its hub's — stops the restore here, before the
# SOUL is rebuilt. build-hubs derives every hub before writing any, so the
# recovery is to fix that page's `title:` and re-run this script.
"$repo_root/bin/build-hubs" "$vault"

# The injected SOUL, composed from the persona and whatever the RUNTIME vault's
# index says today — that is the vault the agent actually reads. Under `set -e`,
# so a failed build aborts the restore rather than leaving last deploy's index
# beside this deploy's config.
"$repo_root/bin/build-soul" "$vault" "$repo_root/runtime/SOUL.md" "$hermes_home/SOUL.md"

printf 'Restored tracked Hermes configuration to %s and seeded %s\n' "$hermes_home" "$vault"
