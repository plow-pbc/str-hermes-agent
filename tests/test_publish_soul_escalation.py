"""publish-soul's fallback ladder once the direct write is refused: a
running container (docker exec, as root), a stopped one (agent-mgr's
one-shot, as root), or neither -- loud failure.

`docker` and `agent-mgr` are genuine external boundaries -- there is no live
container or real root in this suite -- so they alone are stubbed on PATH.
The stubs don't replay publish-soul's own root-side script (that needs a
real root shell to `chown`); they just drop whatever arrives on stdin at the
path publish-soul told them to use, which is enough to prove escalation was
actually reached and the right bytes landed.

The direct-write refusal is real, not simulated: SOUL.md is a directory,
mode 0, so the unmodified `mv` genuinely cannot write through it. That isn't
production's exact mechanism (a root-owned file in a sticky, root-owned
home, which needs real root to build) -- but it is a real kernel refusal
from the real `mv`, not a scripted one.

None of this proves the escalation path against a live container. That is a
manual, by-hand check the operator runs against the deployed agent.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT / "scripts" / "publish-soul"

FAKE_DOCKER = """\
#!/usr/bin/env bash
set -eu
case "$1" in
  ps)
    [ "${FAKE_DOCKER_RUNNING:-0}" = 1 ] && echo fakecontainerid123
    exit 0
    ;;
  exec)
    shift
    if [ "${1:-}" = -u ]; then
      cat > "${*: -1}/SOUL.md"
    else
      printf '%s\\n' "${FAKE_DOCKER_HERMES_HOME:-}"
    fi
    ;;
  *)
    echo "fake docker: unhandled: $*" >&2
    exit 127
    ;;
esac
"""

FAKE_AGENT_MGR = """\
#!/usr/bin/env bash
set -eu
if [ "${1:-}" = compose ] && [ "${2:-}" = str ] && [ "${3:-}" = run ]; then
  cat > "${FAKE_AGENT_MGR_HERMES_HOME:?}/SOUL.md"
else
  echo "fake agent-mgr: unhandled: $*" >&2
  exit 127
fi
"""


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _fakebin(tmp_path: Path, execs: dict[str, str]) -> Path:
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    for name, body in execs.items():
        _write_exe(fakebin / name, body)
    return fakebin


@pytest.fixture
def fixture_paths(tmp_path: Path):
    """A vault with an index, and an AGENT_HOME whose SOUL.md the test
    process genuinely cannot overwrite: a directory, mode 0 -- a hardened
    target without needing real root to build one."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "index.md").write_text("# Index\n\n- [[Sauna]]\n")
    home = tmp_path / "home"
    home.mkdir()
    blocked = home / "SOUL.md"
    blocked.mkdir()
    blocked.chmod(0o000)
    yield vault, home
    blocked.chmod(0o700)  # let tmp_path's own cleanup remove it


def _run(fakebin: Path, vault: Path, home: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{fakebin}:/usr/bin:/bin",
        "STR_VAULT": str(vault),
        "AGENT_HOME": str(home),
        "AGENT_CONTAINER": "fake-hermes",
        **extra,
    }
    return subprocess.run([str(PUBLISH)], env=env, text=True, capture_output=True)


def test_escalates_through_a_running_container(tmp_path: Path, fixture_paths) -> None:
    vault, home = fixture_paths
    fakebin = _fakebin(tmp_path, {"docker": FAKE_DOCKER})
    container_home = tmp_path / "container-home"
    container_home.mkdir()

    result = _run(fakebin, vault, home,
                   FAKE_DOCKER_RUNNING="1", FAKE_DOCKER_HERMES_HOME=str(container_home))
    assert result.returncode == 0, result.stderr
    assert "Sauna" in (container_home / "SOUL.md").read_text()


def test_escalates_through_agent_mgr_when_the_container_is_stopped(tmp_path: Path, fixture_paths) -> None:
    vault, home = fixture_paths
    fakebin = _fakebin(tmp_path, {"docker": FAKE_DOCKER, "agent-mgr": FAKE_AGENT_MGR})
    container_home = tmp_path / "container-home"
    container_home.mkdir()

    result = _run(fakebin, vault, home,
                   FAKE_DOCKER_RUNNING="0", FAKE_AGENT_MGR_HERMES_HOME=str(container_home))
    assert result.returncode == 0, result.stderr
    assert "Sauna" in (container_home / "SOUL.md").read_text()


def test_fails_loudly_with_neither_a_running_container_nor_agent_mgr(tmp_path: Path, fixture_paths) -> None:
    vault, home = fixture_paths
    # Deliberately no agent-mgr in fakebin, and PATH excludes every real
    # location one might live at.
    fakebin = _fakebin(tmp_path, {"docker": FAKE_DOCKER})

    result = _run(fakebin, vault, home, FAKE_DOCKER_RUNNING="0")
    assert result.returncode != 0
    assert "not running" in result.stderr
    assert "agent-mgr is not on PATH" in result.stderr
    assert (home / "SOUL.md").is_dir()  # never replaced with a published file
