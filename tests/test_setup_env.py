"""Boot publishes setup.env's four keys into the container environment, and
nothing else: the file is agent-writable and the script runs as root."""
import subprocess

import pytest

IMAGE = "sams-str-hermes-agent:local"
# `sh` rather than the shebang: with-contenv needs a live s6 supervision tree.
SCRIPT = "/etc/cont-init.d/02-str-setup-env"
FOUR = "HOSTEX_TOKEN=tok-abc.123\nSEAM_API_KEY=seam-1\nPLOW_CHAT_GROUP_UIDS=cht_a=STR Owners\nPLOW_CHAT_APPROVAL_GROUP=\n"

CASES = [
    # (name, setup.env body or None, expected `KEY=value` lines published)
    ("no setup.env yet", None, []),
    ("the four keys", FOUR, ["HOSTEX_TOKEN=tok-abc.123", "PLOW_CHAT_APPROVAL_GROUP=",
                             "PLOW_CHAT_GROUP_UIDS=cht_a=STR Owners", "SEAM_API_KEY=seam-1"]),
    ("anything else is dropped", "LD_PRELOAD=/x\nPATH=/x\n# HOSTEX_TOKEN=no\nHOSTEX_TOKEN\nnot a line\n"
     "HOSTEX_TOKEN_X=1\nexport SEAM_API_KEY=x\nHOSTEX_TOKEN=t", ["HOSTEX_TOKEN=t"]),
]


@pytest.mark.parametrize("name,body,expected", CASES, ids=[c[0] for c in CASES])
def test_setup_env_publish(name, body, expected):
    setup = "mkdir -p /tmp/t/env"
    if body is not None:
        setup += f" && printf %s '{body}' > /tmp/t/setup.env"
    cmd = (f"{setup} && STR_SETUP_ENV=/tmp/t/setup.env STR_ENV_DIR=/tmp/t/env sh {SCRIPT}"
           " && cd /tmp/t/env && for f in *; do [ -e \"$f\" ] && echo \"$f=$(cat \"$f\")\"; done; true")
    out = subprocess.run(["docker", "run", "--rm", "--entrypoint", "sh", IMAGE, "-c", cmd],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == expected
    assert "tok-abc" not in out.stderr  # the log names keys, never values
