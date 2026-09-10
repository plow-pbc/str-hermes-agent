import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts/check-home-binding.sh"


def verdict(tmp_path, *, running=True, bound=True):
    """Run the check against a stub `docker`.

    The script asks the running container rather than a host file, so the stub
    is on `docker` rather than on `$HOME`. `docker exec <c> true` decides
    reachability; `docker exec <c> test -s <path>` decides whether plow-init
    published a home channel. A stub keeps both branches reachable without a
    container, which is the only way the not-running verdict is testable at all.
    """
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir(exist_ok=True)
    stub = stub_bin / "docker"
    stub.write_text(
        "#!/bin/sh\n"
        f"[ {int(running)} -eq 1 ] || exit 1\n"
        'case "$*" in *" test -s "*) '
        f"exit {0 if bound else 1} ;; esac\n"
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
        ("container down", {"running": False}, "CANNOT ASK"),
        ("no home published", {"bound": False}, "UNSET"),
        ("home published", {}, "bound"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_home_binding_verdicts(tmp_path, case, kwargs, expected):
    assert expected in verdict(tmp_path, **kwargs)


def test_no_identifier_is_ever_printed(tmp_path):
    """The whole contract: a verdict, never a value.

    These are chat identifiers and they come from a container whose environment
    also holds tokens, so the script reports the shape of the binding and never
    what it is bound to. A regression here leaks into whatever log or chat the
    operator pastes the output into.
    """
    for kwargs in ({}, {"bound": False}, {"running": False}):
        assert "cht_" not in verdict(tmp_path, **kwargs)
