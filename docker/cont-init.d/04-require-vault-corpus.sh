#!/usr/bin/env bash
# The vault must arrive by clone, and it must have facts in it.
#
# The rule is scripts/restore-runtime-config.sh's, which agent-mgr invokes as
# AGENT_DEPLOY_HOOK. A boot check is strictly better than a deploy check: it
# runs every time rather than only on deploy, and it parks rather than coming up
# wrong -- the same posture plow-init takes. The deploy hook keeps its own copy
# until agent-mgr stops being the live path; refusing at deploy time leaves the
# vault untouched, which a refusal at boot no longer can.
#
# Keyed on index.md, not on the directory: `docker compose up -d` creates a
# missing bind source as an empty root-owned directory, so a bare check for the
# path passes on a vault with no corpus. `-s`, not `-f`: index.md is what the
# persona sends the agent to read, so a present-but-empty one fails the same way
# none at all does.
#
# HERMES_HOME is defaulted rather than required, and that is load-bearing: a
# `#!/usr/bin/env bash` cont-init script gets s6's own environment, not the
# container's, so the image's HERMES_HOME is not set here. Demanding it is the
# bug that made 03-link-wiki-skills.sh exit 1 on every boot for months. The
# default is the base image's own baked value.
set -euo pipefail

vault="${HERMES_HOME:-/var/lib/hermes}/repo/vault"

if [ ! -s "$vault/index.md" ]; then
  echo "str: $vault has no corpus (index.md missing or empty) -- refusing to start." >&2
  echo "str: clone it from the data repo; an empty vault reads exactly like a healthy deploy." >&2
  exit 1
fi

# The vault must never be a git repository. A reachable .git inside the worktree
# is #89 again, where an ingest turn ran `git restore --source=HEAD` over pages
# it judged missing and destroyed them. Its history belongs outside the
# worktree, which is why the clone instructions use the external-git-dir form; a
# plain `git clone` here means they were not followed, and nothing else would
# say so. Carried over from scripts/restore-runtime-config.sh, which enforced it
# at deploy time -- agent-mgr's lifecycle is the one being retired, and this
# guard had no home in the compose path.
if [ -e "$vault/.git" ]; then
  echo "str: $vault/.git exists -- the vault must not be a git repository (#89)." >&2
  echo "str: re-clone with the external-git-dir form; README has the sequence." >&2
  exit 1
fi
