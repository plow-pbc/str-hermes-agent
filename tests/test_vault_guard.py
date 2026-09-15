"""The boot refuses ingest staging with no manifest.

`docker compose up -d` creates a missing bind source as an empty root-owned
directory, so this check is what stands between a typo'd staging path and a
nightly that reads every conversation as never ingested and appends its facts to
the wiki a second time.

The rule was `scripts/restore-runtime-config.sh`'s, which agent-mgr invoked as
AGENT_DEPLOY_HOOK. A boot check is strictly better than a deploy check: it runs
every time rather than only on deploy, and it parks rather than coming up wrong,
which is the posture plow-init already takes.

Every shape below is built INSIDE the container, under an overridden HERMES_HOME.
Binding a client-side temp directory assumes the daemon shares this filesystem;
against a remote or dind daemon it does not, and the mount silently arrives as an
empty directory — which is itself one of the cases under test, so the suite would
go green having tested one shape five times.
"""
import subprocess

import pytest

IMAGE = "sams-str-hermes-agent:local"
GUARD = "/etc/cont-init.d/04-require-ingest-manifest.sh"
VAULT = "/tmp/scratch-home/repo/vault"
MANIFEST = f"""echo '{{"sources": {{}}}}' > {VAULT}/.manifest.json"""

VAULT_SHAPES = [
    ("no staging directory at all", "", "no ingest manifest"),
    # What `docker compose up -d` leaves when the bind source does not exist.
    ("an empty directory", f"mkdir -p {VAULT}", "no ingest manifest"),
    ("a present but zero-byte manifest", f"mkdir -p {VAULT} && : > {VAULT}/.manifest.json",
     "no ingest manifest"),
    # #89: an ingest turn ran `git restore --source=HEAD` over files it judged
    # missing.
    ("staging inside a git worktree",
     f"mkdir -p {VAULT}/.git && {MANIFEST}", "must not be a git repository"),
    ("staging with its manifest", f"mkdir -p {VAULT} && {MANIFEST}", None),
]


@pytest.mark.parametrize(("case", "shape", "refusal"), VAULT_SHAPES,
                         ids=[c[0] for c in VAULT_SHAPES])
def test_the_guard_admits_only_staging_with_a_manifest(case, shape, refusal):
    run = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", IMAGE, "-c",
         f"export HERMES_HOME=/tmp/scratch-home; {shape or 'true'}; {GUARD}"],
        capture_output=True, text=True,
    )
    assert (run.returncode == 0) is (refusal is None), run.stderr or case
    if refusal:
        assert refusal in run.stderr + run.stdout, case


def test_a_failing_cont_init_stops_the_container_rather_than_warning():
    """The half that makes the guard real. The base ships 1, at which a failing
    cont-init only warns and the gateway starts anyway -- the Dockerfile records
    the measurement. A guard that only warns is the failure it exists to prevent.

    Asserted on the setting rather than by booting the image: a full boot runs
    s6 and plow-init, which parks for want of a credential and then outlives any
    timeout through its own grace period.
    """
    env = subprocess.run(
        ["docker", "image", "inspect", IMAGE,
         "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert "S6_BEHAVIOUR_IF_STAGE2_FAILS=2" in env, env
