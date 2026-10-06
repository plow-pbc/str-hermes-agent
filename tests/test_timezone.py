"""Boot sets TZ from ops.toml, keeps a container-provided TZ, never fails."""
import subprocess

import pytest

IMAGE = "sams-str-hermes-agent:local"
# `sh` rather than the shebang: with-contenv needs a live s6 supervision tree.
SCRIPT = "/etc/cont-init.d/03-str-timezone"

CASES = [
    # (name, ops.toml body or None, preset TZ or "", expected TZ)
    ("no ops.toml yet", None, "", "UTC"),
    ("configured zone", 'timezone = "America/Chicago"\n', "", "America/Chicago"),
    ("unknown zone", 'timezone = "Mars/Base"\n', "", "UTC"),
    ("malformed toml", "timezone = \n", "", "UTC"),
    ("container TZ wins", 'timezone = "America/Chicago"\n', "America/Los_Angeles",
     "America/Los_Angeles"),
]


@pytest.mark.parametrize("name,ops,preset,expected", CASES, ids=[c[0] for c in CASES])
def test_timezone(name, ops, preset, expected):
    setup = "mkdir -p /tmp/t && rm -f /tmp/t/TZ"
    if ops is not None:
        setup += f" && printf %s '{ops}' > /tmp/t/ops.toml"
    if preset:
        setup += f" && printf %s '{preset}' > /tmp/t/TZ"
    cmd = (f"{setup} && STR_OPS=/tmp/t/ops.toml STR_TZ_OUT=/tmp/t/TZ sh {SCRIPT}"
           " && cat /tmp/t/TZ")
    out = subprocess.run(["docker", "run", "--rm", "--entrypoint", "sh", IMAGE, "-c", cmd],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout == expected
