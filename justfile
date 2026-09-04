# Short-term-rental operations agent -- DOMAIN recipes only.
#
# Deployment lives in plow-pbc/agent-mgr, which owns the compose service, the
# bring-up, the pins and the contract tests for every agent on this host:
#
#   agent-mgr up str          agent-mgr logs str
#   agent-mgr agent str "..." agent-mgr deploy str
#
# `agent-mgr agent` replaced this repo's `just agent`, which used
# `docker compose run`. The image's s6 entrypoint starts a gateway whatever
# command you pass it, so each of those turns booted a SECOND gateway against
# ~/.hermes, evicted the live one from its chat websockets, and on exit posted a
# shutdown notice into the owners' channel.
#
# What stays here is what only this agent has: the wiki vault pipeline and the
# skills it writes for itself.

test:
    uv run --no-project --python 3.13 --with aiohttp==3.14.1 --with pytest==8.4.2 --with fastmcp==3.4.5 --with seam==1.209.0 pytest -q

# Airbnb operations wiki — task runner.
#
# Copy the skills Hermes wrote for itself into agent-skills/, for review.
#
# Hermes patches its own skills after a turn and announces it in chat; that
# announcement is the only trace. Run this when it does — then read `git diff`
# and commit what should survive the next host rebuild. Reads the live store at
# $HERMES_HOME/skills, so it only means anything on wakeup.
# See README § Skills Hermes wrote.
skills-snapshot:
    {{justfile_directory()}}/bin/skills-snapshot.py

# End-to-end test of the wiki pipeline over the last 7 days.
#
# Hits the live Hostex API and spends tokens, so it is on-demand only — never
# CI, which should neither hold the token nor touch production guest data.
# Runs against .e2e-vault, which is a real vault the production code paths are
# pointed at — not a parallel harness. Every stage below calls the same script
# the nightly chain calls.
test-wiki:
    #!/usr/bin/env bash
    set -uo pipefail
    cd {{justfile_directory()}}
    fail() { echo "FAIL: $1"; exit 1; }
    V=.e2e-vault
    # The vault's own container path, not a sibling of it. `docker compose run`
    # brings the service's volumes with it, so a scratch vault mounted beside
    # the production one leaves production mounted read-write in a run whose
    # whole contract is isolation — and the turn driving it is autonomous. A
    # `-v` at the same target replaces compose's mount instead of joining it, so
    # naming the canonical path is what makes production unreachable here.
    #
    # Same variable compose resolves the real mount from -- agent-mgr exports
    # it from the boot contract, so this recipe's scratch mount always lands
    # on the target compose would have used, unopted or opted-in alike. A
    # wrong guess here mounts the scratch vault somewhere production's mount
    # does not replace, defeating the whole isolation this recipe exists for.
    HH="${AGENT_HOME_TARGET:?set by agent-mgr from the boot contract}"
    CV="$HH/repo/vault"

    # Empty, never delete: $V is a bind-mount source, and unlinking the inode
    # leaves the long-running gateway writing into a directory the host can no
    # longer see.
    mkdir -p "$V"
    find "$V" -mindepth 1 -delete
    mkdir -p "$V"/{properties,operations,people,_raw/hostex}
    # Schema from the checkout, corpus from the runtime vault — the two owners
    # this split created, and only the runtime vault holds both. The whole seed
    # directory, the same way the deploy installs it: enumerating its contents
    # here made this recipe a second owner of that directory's schema.
    cp -a runtime/vault-seed/. "$V"/
    # The seed's OBSIDIAN_VAULT_PATH is a placeholder, same as at install time
    # (restore-runtime-config.sh) -- obsidian-wiki reads its own .env as plain
    # KEY=VALUE with no expansion, so left alone it points this run at the
    # legacy path rather than $V, and a run that then reads production's real
    # vault by accident looks exactly as green as one that read the scratch
    # copy it meant to.
    sed -i "s|^OBSIDIAN_VAULT_PATH=.*|OBSIDIAN_VAULT_PATH=$CV|" "$V/.env"
    # The live corpus and its manifest, so this exercises the nightly that
    # actually runs rather than the one-time bootstrap. Without them every run
    # re-ingests all 235 conversations — ~24 agent rounds, over an hour.
    #
    # Copied out of a directory the nightly also writes, so a run overlapping
    # 3am can take a torn copy. That degrades this scratch test, not
    # production, and at one nightly a night it is not worth a lock.
    RV="$HOME/hermes-vault"
    # index.md, not just the directory: `docker compose up -d` creates a missing
    # bind source as an empty root-owned directory, so an empty vault is a live
    # state the directory check alone would pass — straight into the very run
    # this recipe's corpus copy exists to avoid.
    [ -s "$RV/index.md" ] || fail "no usable runtime vault at $RV (index.md missing or empty) — this recipe runs on wakeup"
    cp "$RV"/index.md "$RV"/log.md "$V"/
    cp "$RV"/.manifest.json "$V"/ 2>/dev/null || true
    cp "$RV"/properties/*.md "$V"/properties/ || fail "no property hubs in $RV/properties — the vault owns them now"
    cp "$RV"/operations/*.md "$V"/operations/
    # Absent until the first standing person is named, so optional like the
    # manifest above — but copied with the corpus, because the manifest may
    # already cite it and coverage keys on conversations, not pages: a cited
    # page left behind is a gap this recipe would report as complete.
    cp "$RV"/people/*.md "$V"/people/ 2>/dev/null || true
    # The corpus checks the chain now runs. Without them pytest collects nothing
    # in the scratch vault, exits 5, and every e2e stamps a corpus failure into
    # the digest status over a corpus this recipe just verified as complete —
    # leaving the one path that exercises the whole chain unable to exercise
    # this step succeeding.
    # Required, not optional like the manifest and people copies above: without
    # it pytest collects nothing, exits 5, and the chain notes a corpus failure
    # over a corpus this recipe goes on to verify as complete.
    cp -a "$RV"/tests "$V"/ || fail "no corpus checks at $RV/tests"
    # Proof that the mount replaced compose's rather than joining it. The
    # isolation rests on $CV being spelled exactly like compose's target and on
    # last-target-wins being what `docker compose run -v` does — neither of
    # which the run can observe. Drift either and the scratch vault still
    # mounts somewhere, the nightly still writes where $VAULT points, every
    # assertion below still passes, and an autonomous turn holds the production
    # vault the whole time. A sentinel only the scratch copy has is the one
    # check that reads the mount instead of the spelling.
    touch "$V/.e2e-vault-marker"

    echo "=== 0. scripts parse"
    bash -n bin/nightly.sh || fail "bin/nightly.sh has a syntax error"
    python3 -c "import ast; [ast.parse(open(f).read()) for f in ('bin/hostex-raw', 'bin/build-hubs')]" \
      || fail "a python script has a syntax error"
    echo "    ok"

    echo "=== 1. the nightly chain, through its own entry point"
    # bin/nightly.sh, not a re-sequencing of its steps: fetch, ingest and lint
    # driven independently would leave their interaction inside the one
    # script unexercised.

    # --entrypoint bash: the image's own entrypoint is the hermes CLI, so a bare
    # path argument is swallowed as a subcommand and the script never runs. That
    # also skips s6, which at container start is what links the wiki skills the
    # ingest turn needs — so run the linking script explicitly.
    #
    # Only that one script, not all of /etc/cont-init.d. It is the one this
    # repo owns and ships, and it is plain bash. The other three are upstream's
    # and are execline (`#!/command/with-contenv`): they need /command on PATH
    # and the /run/s6 envdir that only s6's own boot creates, so a loop over the
    # directory dies on the first of them before ever reaching this one. Nor can
    # `--entrypoint /init` stand in — s6's boot also starts the gateway
    # services, which have no TTY for the first-run setup prompt and take the
    # container down with them. What those three configure is the gateway, which
    # the nightly chain does not use.
    #
    # --user: the same thing s6 does at a real container start, and skipping it
    # is the other half of skipping s6. The image's default user is root, but
    # everything the chain touches on the host side belongs to the invoking
    # user: ~/.hermes/.env is mode 600, and anything written would come back
    # root-owned into a bind mount the host then cannot clean up.
    #
    # Failures surface: if the skills the ingest turn needs were not linked,
    # the run must stop here rather than fail later as an unexplained empty
    # ingest. Output goes to the log with everything else.
    #
    # $HH/scripts/nightly.sh, which is the same file the scheduler runs.
    # The checkout is not mounted — compose.yml mounts ~/hermes-vault at
    # $CV, not a repo-relative path — so this scratch vault comes in on a
    # per-run `-v` rather than widening what production hands the agent for
    # the sake of a test.
    NIGHTLY="[ -e $CV/.e2e-vault-marker ] || { echo \"the vault at $CV is production, not the scratch vault — the -v did not replace compose's mount\" >&2; exit 1; }; /etc/cont-init.d/03-link-wiki-skills.sh || { echo \"linking the wiki skills failed\" >&2; exit 1; }; [ -d $HH/skills/wiki-digest ] || { echo \"the link script ran but the wiki skills are not linked\" >&2; exit 1; }; exec $HH/scripts/nightly.sh"
    #
    # SOUL_OUT off the default too. It defaults to $HERMES_HOME/SOUL.md, which is
    # the live gateway's injected system prompt — this run would compose the
    # scratch vault's index over it and leave production advertising pages that
    # exist only here. The vault mount above covers the vault; this covers the
    # one output that lands outside it.
    # Through agent-mgr, which owns the compose file list, the override and the
    # env-file. Reaching for `docker compose` directly here would restate all
    # three and drift from the deployment the gateway actually runs under.
    #
    # --entrypoint is load-bearing and agent-mgr enforces it: the image's own
    # entrypoint is the hermes CLI, so a bare path argument is swallowed as a
    # subcommand -- and s6 would boot a gateway alongside the live one.
    agent-mgr compose str run --rm --no-deps -T -e VAULT="$CV" -e SOUL_OUT=/tmp/e2e-SOUL.md \
      -v "$PWD/$V:$CV" --user "$(id -u):$(id -g)" --entrypoint bash hermes \
      -c "$NIGHTLY" > /tmp/e2e-nightly.log 2>&1 \
      || fail "nightly chain failed — see /tmp/e2e-nightly.log"
    tail -5 /tmp/e2e-nightly.log | sed 's/^/      /'
    # The chain's non-fatal failures. Each one keeps the run going — a stale
    # injected index, a corpus that failed its checks — so from the outside a
    # run that noted one looks identical to a clean one, and this recipe's own
    # `ok` would print over it. One check for all of them: every note reaches
    # stderr, and every path that writes one also writes the reason above it.
    ! grep -q "^nightly: " /tmp/e2e-nightly.log \
      || fail "the nightly noted a failure — see /tmp/e2e-nightly.log"

    echo "=== 2. it staged conversations"
    # Counted wherever they were archived to, for the same reason bin/ingest-all
    # walks the tree: the skill uses more than one archive layout, and naming
    # them makes this count quietly low.
    RAW=$(find "$V"/_raw -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
    [ "$RAW" -ge 1 ] || fail "no raw conversations staged"
    [ ! -d "$V/_meta" ] || fail "fetch wrote progress state; the cache is the cursor"
    echo "    ok: $RAW conversations staged, no progress state"

    echo "=== 3. it produced pages and recorded every source"
    PAGES=$(ls "$V"/operations/*.md 2>/dev/null | wc -l | tr -d ' ')
    [ "$PAGES" -ge 1 ] || fail "no operations pages"
    grep -rq "properties/" "$V"/operations/ || fail "no page links to a property hub"
    # Captured, then matched. Piping a multi-line producer into `grep -q` makes
    # this guard non-deterministic: grep stops reading at the match and the
    # producer takes a SIGPIPE writing what follows, which under `pipefail`
    # becomes the pipeline's status — so the check can fail exactly when it
    # passes, depending on timing. The same shape is already commented on at
    # the bundle check below; this is the second site.
    COVERAGE="$(./bin/ingest-all "$V")" \
      || fail "the coverage check itself failed — see the output above"
    grep -q "manifest covers everything in scope" <<<"$COVERAGE" \
      || fail "manifest does not cover the staged corpus"
    echo "    ok: $PAGES pages, manifest complete"
