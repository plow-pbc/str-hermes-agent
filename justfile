# Short-term-rental operations agent.
#
# Deployment is this repo's own, in compose.yml -- the shape plow-agents'
# compose.example.yml defines. It used to live in plow-pbc/agent-mgr, which is
# deprecated.
#
# Never boot a second gateway to ask the agent something. The image's s6
# entrypoint starts one whatever command you pass it, so a `docker compose run`
# turn evicts the live gateway from its chat websockets and on exit posts a
# shutdown notice into the owners' channel. Talk to the running one.

# The container compose.yml declares. scripts/no-nightly-running asks docker
# about it by name and refuses to guess one, so the two have to agree;
# compose.yml is where it is declared and this is the only place that repeats it.
export AGENT_CONTAINER := "hermes"

# The lifecycle. agent-mgr invoked scripts/no-nightly-running as
# AGENT_PRE_TRANSITION before every `up`, `down` and `restart`; docker compose
# has no such hook, so these recipes are the only thing left that can refuse.
# Never reach for `docker compose` directly to stop or replace the container --
# that is the bypass the veto exists to prevent.
#
# Why it refuses: a transition landing between a page write and its manifest
# entry leaves the vault holding a page nothing recorded, and the next run
# appends its facts a second time with nothing reporting it.
up:
    ./scripts/no-nightly-running
    docker compose up -d

down:
    ./scripts/no-nightly-running
    docker compose down

restart:
    ./scripts/no-nightly-running
    docker compose up -d --force-recreate

# The image first: tests/test_image_contents.py and tests/test_vault_guard.py
# assert against this exact tag, and without it 16 of them fail from a clean
# checkout -- including the one that keeps the private vault out of a public
# image, which passes while inspecting nothing.
test:
    docker build -q -t sams-str-hermes-agent:local .
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
