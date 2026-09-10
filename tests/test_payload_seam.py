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

# A MIGRATED home carries the empty directory a bind-mount target leaves behind.
# There was a HOSTED shape too -- agent-mgr mounting bin/ and mcp-seam/ and
# staging the persona and config into the home -- which the seam had to leave
# alone while both paths were live. That path is gone, so the seam no longer
# branches on it and there is nothing to assert.
MIGRATED = "mkdir -p /tmp/h/scripts"

# Asked of cron's own checker rather than restated. Why the links are on the
# DIRECTORY is the seam script's to explain.
CRON_CHECK = (
    'cd /opt/hermes && /opt/hermes/.venv/bin/python -c '
    '"import sys; sys.path.insert(0, \'/opt/hermes\'); '
    'from hermes_cli.cron import _script_health_issue as c; '
    "print(c('nightly.sh') or 'ACCEPTED', c('checkin-watch.py') or 'ACCEPTED', "
    "'REFUSED' if c('../../etc/passwd') else 'ACCEPTED')\""
)

SEAM_CLAIMS = [
    ("the scheduler's scripts", MIGRATED, "readlink /tmp/h/scripts", "/opt/plow/str/bin"),
    ("the seam mcp server", MIGRATED, "readlink /tmp/h/mcp-seam", "/opt/plow/str/mcp-seam"),
    ("cron accepts the directory link", MIGRATED, CRON_CHECK, "ACCEPTED ACCEPTED REFUSED"),
    ("the config is refreshed", MIGRATED,
     "grep -qF /opt/plow/str/mcp-seam/server.py /tmp/h/config.yaml && echo yes", "yes"),
    # plow-init rewrites config.yaml AS THE AGENT, and $HERMES_HOME carries the
    # sticky bit -- so a root-owned one is a file the agent cannot replace, and
    # the boot parks on `os.replace(config.yaml.tmp -> config.yaml)` EPERM.
    # cont-init has already reported exit 0 by then, which is what hid it.
    ("the config belongs to the agent that rewrites it", MIGRATED,
     "stat -c '%U %a' /tmp/h/config.yaml", "hermes 640"),
    # The seam must NOT write the identity. plow-init composes it from the base
    # persona and /opt/hermes/plow-seed/persona.md at every boot; a copy
    # installed here would be half an identity overwriting a whole one, and the
    # window between the two writes is invisible from outside.
    ("the seam leaves the identity to plow-init", MIGRATED,
     "test -e /tmp/h/SOUL.md && echo wrote || echo untouched", "untouched"),
]


@pytest.mark.parametrize(("case", "setup", "probe", "expected"), SEAM_CLAIMS,
                         ids=[c[0] for c in SEAM_CLAIMS])
def test_the_seam_makes_the_payload_reachable(case, setup, probe, expected):
    """Run the seam against a scratch home, then ask the probe about it."""
    out = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", IMAGE, "-c",
         f'export HERMES_HOME=/tmp/h; mkdir -p /tmp/h; {setup}; '
         f'{SEAM} >/dev/null 2>&1; {probe}'],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == expected, case
