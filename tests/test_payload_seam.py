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

# The two homes the seam has to tell apart. A MIGRATED home carries the empty
# directory a bind-mount target leaves behind; a HOSTED one is the agent-mgr
# shape, where compose.override.yml is still mounting bin/ and mcp-seam/ and the
# deploy stages SOUL and config into the home itself.
MIGRATED = "mkdir -p /tmp/h/scripts"
HOSTED = ("mkdir -p /tmp/h/scripts && echo host-supplied > /tmp/h/scripts/nightly.sh"
          " && echo staged-by-the-deploy > /tmp/h/config.yaml")

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
    ("the persona is refreshed", MIGRATED,
     "grep -qF 'short-term rentals' /tmp/h/SOUL.md && echo yes", "yes"),
    ("the config is refreshed", MIGRATED,
     "grep -qF /opt/plow/str/mcp-seam/server.py /tmp/h/config.yaml && echo yes", "yes"),
    ("the persona is root-owned and read-only to the agent", MIGRATED,
     "stat -c '%U %a' /tmp/h/SOUL.md", "root 644"),
    # Replacing a mount point fails the boot, and at FAILS=2 that is the whole
    # container -- this repo must not break the shape it is migrating away from.
    ("a populated host directory is retained", HOSTED,
     "cat /tmp/h/scripts/nightly.sh", "host-supplied"),
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
