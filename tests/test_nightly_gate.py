"""The nightly guard fails closed.

The rule it enforces -- no container transition while an ingest is running --
is restated in exactly one place: the manual-nightly recovery in the README,
which runs `nightly.sh` through an `exec` passthrough, so no transition fires
and the hook never runs. Everywhere else agent-mgr invokes it. `agent.env` declares this script as
AGENT_PRE_TRANSITION and agent-mgr invokes it before every `up`, `down`,
`restart`, `restore`, and transitioning `compose` passthrough.

That replaced five hand-copied `pgrep` lines and the source scanner written to
keep them honest. Three review rounds went into correcting one copy while the
others asserted the opposite placement, and three more into a scanner that
still could not see a table cell, a justfile recipe, or an imperative in prose.
A hook the tool calls has no copies and no blind spots, so what is left to test
is the guard's own behaviour.
"""
import os
import subprocess

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "no-nightly-running"


def test_the_guard_is_declared_as_the_pre_transition_hook():
    """The declaration is what makes agent-mgr call it. Without it the guard is
    a file nothing runs."""
    settings = [l for l in (ROOT / "agent.env").read_text().splitlines()
                if l.strip() and not l.lstrip().startswith("#")]
    assert "AGENT_PRE_TRANSITION=scripts/no-nightly-running" in settings
    assert GUARD.is_file() and GUARD.stat().st_mode & 0o111


def _fake_docker(tmp_path, *, running=True, exec_status=1, container="hermes"):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    (b / "docker").write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        f'  ps)   {"echo deadbeef" if running else ":"} ;;\n'
        f'  exec) exit {exec_status} ;;\n'
        "esac\nexit 0\n")
    (b / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{b}:{os.environ['PATH']}"}
    if container is None:
        env.pop("AGENT_CONTAINER", None)
    else:
        env["AGENT_CONTAINER"] = container
    return env


@pytest.mark.parametrize(
    ("running", "exec_status", "container", "ok", "error"),
    [
        # A running container whose check could not answer. Exit 2 is pgrep's
        # bad-pattern status; the guard used to report every non-zero as clear.
        pytest.param(True, 2, "hermes", False, "treat as unsafe", id="broken-check"),
        pytest.param(True, 0, "hermes", False, "mid-ingest", id="nightly-running"),
        pytest.param(True, 1, "hermes", True, None, id="no-nightly"),
        # A fresh host has no container, and the bootstrap depends on this
        # being clean. Through the fake, so it stops depending on a real daemon.
        pytest.param(False, 1, "hermes", True, None, id="container-stopped"),
        # The README's manual-nightly recovery gets the container from a command
        # substitution, so an unregistered agent or a resolve that stops
        # printing the key yields an empty value -- and this `:?` refusal is the
        # only thing that turns that into a non-zero exit and short-circuits the
        # `&&`. Soften it and that path starts a second nightly beside a live
        # one with every other row still green.
        pytest.param(True, 1, None, False, "run me through agent-mgr", id="no-container"),
    ],
)
def test_guard_outcomes(tmp_path, running, exec_status, container, ok, error):
    result = subprocess.run(
        [str(GUARD)], capture_output=True, text=True,
        env=_fake_docker(tmp_path, running=running, exec_status=exec_status,
                         container=container),
    )
    assert (result.returncode == 0) is ok, result.stderr
    if error:
        assert error in result.stderr


# Deliberately not here: source scanners over the deploy skill's prose and shell
# spelling. The skill's own inline checks are executable and run at deploy time
# -- `agent-mgr resolve str | grep -q '^AGENT_PRE_TRANSITION='` fails the deploy
# on a build that does not implement the hook, and the restore grep fails it
# when the hook did not run. A test asserting those lines exist duplicates them
# and makes exact prose part of the contract, which is the churn six rounds of
# scanner tuning already demonstrated.
