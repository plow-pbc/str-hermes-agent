#!/usr/bin/env bash
# The ingest staging must arrive with its manifest.
#
# The pages live in the owner's wiki on their Mac; what this container keeps is
# the Hostex raw cache and .manifest.json, the pipeline's whole memory of what it
# has already distilled. Staging without a manifest reads as "nothing ingested
# yet", so the next nightly would feed every conversation back into pages that
# already hold their facts, appending each one a second time.
#
# Keyed on the manifest, not on the directory: `docker compose up -d` creates a
# missing bind source as an empty root-owned directory, so a bare check for the
# path passes on staging with nothing in it. `-s`, not `-f`: a zero-byte manifest
# fails the same way none at all does.
#
# HERMES_HOME is defaulted rather than required, and that is load-bearing: a
# `#!/usr/bin/env bash` cont-init script gets s6's own environment, not the
# container's, so the image's HERMES_HOME is not set here. Demanding it is the
# bug that made 03-link-wiki-skills.sh exit 1 on every boot for months. The
# default is the base image's own baked value.
set -euo pipefail

vault="${HERMES_HOME:-/var/lib/hermes}/repo/vault"

if [ ! -s "$vault/.manifest.json" ]; then
  echo "str: $vault has no ingest manifest (.manifest.json missing or empty) -- refusing to start." >&2
  echo "str: without it the next nightly re-ingests every conversation into the wiki." >&2
  exit 1
fi

# Staging must never be a git repository. A reachable .git inside it is #89
# again, where an ingest turn ran `git restore --source=HEAD` over files it
# judged missing and destroyed them.
if [ -e "$vault/.git" ]; then
  echo "str: $vault/.git exists -- ingest staging must not be a git repository (#89)." >&2
  exit 1
fi
