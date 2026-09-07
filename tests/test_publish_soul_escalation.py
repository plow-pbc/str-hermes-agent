"""publish-soul's fallback ladder once the direct write is refused: a
running container (docker exec, as root), a stopped one (agent-mgr's
one-shot, as root), or neither -- loud failure.

Real root and a real container are both out of reach for these tests --
publish-soul is never run against the live host or the live container, per
instruction -- so `docker` and `agent-mgr` are replaced with scratch
stand-ins on PATH. What is under test is publish-soul's OWN branching and
the shape of what it runs, not docker's or agent-mgr's own behaviour.

The direct write is forced to fail the same way everywhere here: a fake `mv`
on PATH that refuses only the specific direct target this fixture builds and
falls through to the real mv for every other call, including the
container-side write -- there is no root available to reproduce the real
sticky-directory refusal locally.
"""
from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path

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
      shift 3   # -u root -i
      shift     # container name
      shift     # sh
      shift     # -c
      script="$1"; shift
      shift     # _
      arg="${1:-}"
      exec sh -c "$script" _ "$arg"
    else
      shift     # container name
      printf '%s\\n' "${FAKE_DOCKER_HERMES_HOME:-}"
      exit 0
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
  shift 3
  script=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -c) script="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  export HERMES_HOME="${FAKE_AGENT_MGR_HERMES_HOME:?}"
  exec bash -c "$script"
else
  echo "fake agent-mgr: unhandled: $*" >&2
  exit 127
fi
"""


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _fake_chown(fakebin: Path) -> None:
    """A no-op standing in for `chown root:root`, which genuinely needs real
    root -- the simulated container-side write runs as this test's own
    (non-root) user. Ownership is not what these tests assert on; content
    and mode are."""
    _write_exe(fakebin / "chown", "#!/usr/bin/env bash\nexit 0\n")


def _fake_mv(fakebin: Path, blocked_target: Path) -> None:
    _write_exe(fakebin / "mv", "#!/usr/bin/env bash\n"
        "set -eu\n"
        "for a in \"$@\"; do\n"
        f"  if [ \"$a\" = {shlex.quote(str(blocked_target))} ]; then\n"
        "    echo \"mv: fake: Operation not permitted\" >&2\n"
        "    exit 1\n"
        "  fi\n"
        "done\n"
        "exec /bin/mv \"$@\"\n")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "index.md").write_text("# Index\n\n- [[Sauna]]\n")
    home = tmp_path / "home"
    home.mkdir()
    return vault, home


def test_escalates_through_a_running_container(tmp_path: Path) -> None:
    """The container is up: publish through `docker exec -u root`."""
    vault, home = _fixture(tmp_path)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _fake_mv(fakebin, home / "SOUL.md")
    _fake_chown(fakebin)
    _write_exe(fakebin / "docker", FAKE_DOCKER)
    container_home = tmp_path / "container-home"
    container_home.mkdir()

    env = {
        **os.environ,
        "PATH": f"{fakebin}:/usr/bin:/bin",
        "STR_VAULT": str(vault),
        "AGENT_HOME": str(home),
        "AGENT_CONTAINER": "fake-hermes",
        "FAKE_DOCKER_RUNNING": "1",
        "FAKE_DOCKER_HERMES_HOME": str(container_home),
    }
    result = subprocess.run([str(PUBLISH)], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "published" in result.stdout
    published = (container_home / "SOUL.md").read_text()
    assert "Sauna" in published
    assert stat.S_IMODE((container_home / "SOUL.md").stat().st_mode) == 0o644


def test_escalates_through_agent_mgr_when_the_container_is_stopped(tmp_path: Path) -> None:
    """The container exists but is stopped: fall back to a root one-shot
    through `agent-mgr compose str run`, never `docker compose` directly."""
    vault, home = _fixture(tmp_path)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _fake_mv(fakebin, home / "SOUL.md")
    _fake_chown(fakebin)
    _write_exe(fakebin / "docker", FAKE_DOCKER)
    _write_exe(fakebin / "agent-mgr", FAKE_AGENT_MGR)
    container_home = tmp_path / "container-home"
    container_home.mkdir()

    env = {
        **os.environ,
        "PATH": f"{fakebin}:/usr/bin:/bin",
        "STR_VAULT": str(vault),
        "AGENT_HOME": str(home),
        "AGENT_CONTAINER": "fake-hermes",
        "FAKE_DOCKER_RUNNING": "0",
        "FAKE_AGENT_MGR_HERMES_HOME": str(container_home),
    }
    result = subprocess.run([str(PUBLISH)], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "via agent-mgr" in result.stdout
    assert "stopped" in result.stdout
    published = (container_home / "SOUL.md").read_text()
    assert "Sauna" in published
    assert stat.S_IMODE((container_home / "SOUL.md").stat().st_mode) == 0o644


def test_fails_loudly_with_neither_a_running_container_nor_agent_mgr(tmp_path: Path) -> None:
    """Neither escalation path is available: fail loudly and actionably,
    naming both facts, rather than silently doing nothing."""
    vault, home = _fixture(tmp_path)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _fake_mv(fakebin, home / "SOUL.md")
    _write_exe(fakebin / "docker", FAKE_DOCKER)
    # Deliberately no agent-mgr in fakebin, and PATH excludes every real
    # location one might live at (~/.local/bin included) -- the same
    # PATH shape the 04:30 cron line runs under.
    env = {
        **os.environ,
        "PATH": f"{fakebin}:/usr/bin:/bin",
        "STR_VAULT": str(vault),
        "AGENT_HOME": str(home),
        "AGENT_CONTAINER": "fake-hermes",
        "FAKE_DOCKER_RUNNING": "0",
    }
    result = subprocess.run([str(PUBLISH)], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "not running" in result.stderr
    assert "agent-mgr is not on PATH" in result.stderr
    assert not (home / "SOUL.md").exists()
