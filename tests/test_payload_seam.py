"""The image's payload has to be reachable from where its consumers look.

Everything this agent ships is authoritative under `/opt/plow/str`, root-owned so
a prompt-injected turn cannot rewrite what its own cron runs. None of its
consumers look there, and `docker/cont-init.d/05-install-agent-payload.sh` is the
one seam that closes the gap.
"""
import subprocess

import pytest

IMAGE = "sams-str-hermes-agent:local"
SEAM = "/etc/cont-init.d/05-install-agent-payload.sh"


def _in_home(setup: str, probe: str) -> str:
    """Run the seam against a scratch home, then ask the probe about it."""
    return subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", IMAGE, "-c",
         f'export HERMES_HOME=/tmp/h; mkdir -p /tmp/h; {setup}; '
         f'{SEAM} >/dev/null 2>&1; {probe}'],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.mark.parametrize(("case", "probe", "expected"), [
    # The cutover blocker: hermes cron refuses a script resolving outside
    # $HERMES_HOME/scripts, and a home migrated off the agent-mgr shape brings
    # an empty directory where the bind mount used to be.
    ("the scheduler's scripts", "readlink /tmp/h/scripts", "/opt/plow/str/bin"),
    ("the seam mcp server", "readlink /tmp/h/mcp-seam", "/opt/plow/str/mcp-seam"),
    # A volume seeds from the image only while EMPTY, so a migrated home shadows
    # every later revision of these two (plow-hermes-agent#58).
    ("the persona is refreshed",
     "grep -qF 'short-term rentals' /tmp/h/SOUL.md && echo yes", "yes"),
    ("the config is refreshed",
     "grep -qF /opt/plow/str/mcp-seam/server.py /tmp/h/config.yaml && echo yes", "yes"),
    # harden_home() re-asserts exactly this on SOUL.md moments later, and the
    # agent must not be able to rewrite its own persona.
    ("the persona is root-owned and read-only to the agent",
     "stat -c '%U %a' /tmp/h/SOUL.md", "root 644"),
])
def test_the_seam_makes_the_payload_reachable(case, probe, expected):
    assert _in_home("mkdir -p /tmp/h/scripts", probe) == expected, case


def test_cron_accepts_a_script_through_the_linked_directory():
    """The whole reason the links are on the DIRECTORY rather than each file.

    hermes_cli/cron.py resolves both its scripts dir and the candidate path
    before comparing them, so a link on the directory leaves both sides inside
    /opt/plow/str/bin and the check passes -- while linking each file
    individually resolves outside a scripts dir that did not move, and every job
    this agent runs would be refused. Asserted against cron's own checker, not a
    restatement of its rule."""
    out = _in_home("true", (
        'cd /opt/hermes && /opt/hermes/.venv/bin/python -c '
        '"import sys; sys.path.insert(0, \'/opt/hermes\'); '
        'from hermes_cli.cron import _script_health_issue as c; '
        "print(c('nightly.sh') or 'ACCEPTED', c('checkin-watch.py') or 'ACCEPTED', "
        "'REFUSED' if c('../../etc/passwd') else 'ACCEPTED')\""
    ))
    assert out == "ACCEPTED ACCEPTED REFUSED", out


def test_a_populated_directory_is_left_to_whoever_mounted_it():
    """agent-mgr's compose.override.yml bind-mounts bin/ and mcp-seam/ at these
    exact paths and is still the live path. Replacing a mount point fails the
    boot, and with S6_BEHAVIOUR_IF_STAGE2_FAILS=2 a failed boot is the whole
    container -- so this repo's own seam must not break the shape it is
    migrating away from while that shape still runs the agent."""
    assert _in_home(
        "mkdir -p /tmp/h/scripts && echo host-supplied > /tmp/h/scripts/nightly.sh",
        "cat /tmp/h/scripts/nightly.sh",
    ) == "host-supplied"
