import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts/check-home-binding.sh"


def verdict(tmp_path, *, up=True, published=True):
    """Run the check against a stub `docker`.

    The script asks the boot environment through the container, so the two
    observable states are whether the container answers at all and whether
    `test -s` finds a published home channel. The stub tells them apart by
    matching the `test -s` the real `compose exec` would run.
    """
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir(exist_ok=True)
    stub = stub_bin / "docker"
    stub.write_text(
        "#!/bin/sh\n"
        f"[ {int(up)} -eq 1 ] || exit 1\n"
        f'case "$*" in *"test -s"*) exit {0 if published else 1} ;; esac\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    result = subprocess.run(
        [CHECK], env={"HOME": str(tmp_path), "PATH": f"{stub_bin}:/usr/bin:/bin"},
        text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("case", "kwargs", "expected"),
    [
        ("container down", {"up": False}, "CANNOT ASK"),
        ("no home published", {"published": False}, "UNSET"),
        ("home published", {}, "bound"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_verdict_names_the_state_and_never_the_chat(tmp_path, case, kwargs, expected):
    """Both halves of the contract, over one state list.

    The verdict has to be right, and it has to *be* a verdict: these are chat
    identifiers read from a container whose environment also holds tokens, so
    the script reports the shape of the binding and never what it is bound to.
    One matrix asserts both -- two lists drift, and the one that drifts silently
    is the disclosure check.
    """
    line = verdict(tmp_path, **kwargs)
    assert expected in line
    assert "cht_" not in line


def test_unset_does_not_prescribe_sethome(tmp_path):
    """The remedy is the reason this verdict exists.

    `/sethome` cannot fix an unset home channel: without PLOW_HOME_CHANNEL the
    plow_chat plugin does not load, so nothing is there to receive the command.
    The previous contract prescribed it anyway, which is the failure this
    replaced -- a remedy that reads as actionable and changes nothing.
    """
    line = verdict(tmp_path, published=False)
    assert "plow-agents" in line
    assert "Not /sethome" in line
