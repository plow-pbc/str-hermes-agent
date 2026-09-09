"""The boot refuses a vault with no corpus.

`docker compose up -d` creates a missing bind source as an empty root-owned
directory, so this check is what stands between a typo'd vault path and an agent
that comes up looking healthy while knowing nothing — the quietest failure this
repo can ship.

The rule was `scripts/restore-runtime-config.sh`'s, which agent-mgr invoked as
AGENT_DEPLOY_HOOK. A boot check is strictly better than a deploy check: it runs
every time rather than only on deploy, and it parks rather than coming up wrong,
which is the posture plow-init already takes.
"""
import pathlib
import subprocess
import tempfile

import pytest

IMAGE = "sams-str-hermes-agent:local"
GUARD = "/etc/cont-init.d/04-require-vault-corpus.sh"
VAULT = "/var/lib/hermes/repo/vault"


@pytest.mark.parametrize(("index", "accepted"), [
    # No index.md at all: the empty directory `docker compose up -d` leaves
    # behind when the bind source does not exist.
    pytest.param(None, False, id="no-corpus"),
    # Present but empty. `-s`, not `-f`: index.md is what the persona sends the
    # agent to read, so a zero-byte one fails exactly like none at all.
    pytest.param("", False, id="empty-index"),
    pytest.param("# index\nreal content\n", True, id="real-corpus"),
])
def test_the_guard_admits_only_a_vault_with_a_corpus(index, accepted):
    with tempfile.TemporaryDirectory() as vault:
        if index is not None:
            (pathlib.Path(vault) / "index.md").write_text(index)
        run = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{vault}:{VAULT}",
             "--entrypoint", "sh", IMAGE, "-c", GUARD],
            capture_output=True, text=True,
        )
    assert (run.returncode == 0) is accepted, run.stderr
    if not accepted:
        # The refusal's own words. "vault" alone matches the guard's path, so
        # `sh: not found` passed this assertion when the script did not exist.
        assert "no corpus" in run.stderr + run.stdout


def test_a_failing_cont_init_stops_the_container_rather_than_warning():
    """The half that makes the guard real, and the half that is not obvious.

    The base ships S6_BEHAVIOUR_IF_STAGE2_FAILS=1, at which a cont-init script
    exiting non-zero prints one warning and the boot carries on and starts the
    gateway anyway. That is how 03-link-wiki-skills.sh exited 1 on every boot
    for months with nobody noticing, and a guard that only warns is the failure
    it exists to prevent -- so the image sets 2.

    Asserted on the setting rather than by booting the image: a full boot runs
    s6 and plow-init, which parks for want of a credential and then outlives
    any timeout through its own grace period. The behaviour behind the setting
    was measured instead, both ways round, on this image and on a probe image
    built from the base: at 1 the boot logs "some scripts exited nonzero" and
    continues to plow-init; at 2 rc.init logs "fatal: stopping the container"
    and the container exits 1. A vault with a corpus passes cont-init and the
    boot proceeds either way.
    """
    env = subprocess.run(
        ["docker", "image", "inspect", IMAGE,
         "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert "S6_BEHAVIOUR_IF_STAGE2_FAILS=2" in env, env
