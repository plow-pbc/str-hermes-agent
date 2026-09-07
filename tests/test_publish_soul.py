"""The SOUL reaches $HERMES_HOME only through this script.

The base image's plow-init hardens $HERMES_HOME/SOUL.md to root:root 0644 at
every boot, inside a root-owned sticky home. Neither the agent (hermes, in the
container) nor the operator (odio, on the host) can replace it after that --
only root can. This script is the host's one way to do it, and what these tests
pin is the shape it lands and that it never copies an agent-authored file.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT / "scripts" / "publish-soul"


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A runtime vault with an index, and a home to publish into."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "index.md").write_text("# Index\n\n- [[Sauna]]\n")
    home = tmp_path / "home"
    home.mkdir()
    return vault, home


def _run(vault: Path, home: Path, **extra: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "STR_VAULT": str(vault),
        "AGENT_HOME": str(home),
        # No container: the target is replaceable, so the direct path applies.
        "AGENT_CONTAINER": "publish-soul-tests-no-such-container",
        **extra,
    }
    return subprocess.run(
        [str(PUBLISH)], env=env, text=True, capture_output=True
    )


def test_publishes_persona_and_index_at_plow_init_shape(tmp_path: Path) -> None:
    """The published SOUL carries both halves, at the mode plow-init enforces."""
    vault, home = _fixture(tmp_path)
    result = _run(vault, home)
    assert result.returncode == 0, result.stderr
    soul = home / "SOUL.md"
    body = soul.read_text()
    # The persona half comes from the repo, the index half from the vault.
    assert "Sauna" in body
    assert (ROOT / "runtime" / "SOUL.md").read_text().strip()[:40] in body
    # 0644, not build-soul's 0600: harden_home() sets exactly this at the next
    # boot, so matching it makes that hardening a no-op instead of a change.
    assert stat.S_IMODE(soul.stat().st_mode) == 0o644


def test_second_run_is_quiet_and_changes_nothing(tmp_path: Path) -> None:
    """Idempotent: the cron runs this nightly whether or not anything moved."""
    vault, home = _fixture(tmp_path)
    assert _run(vault, home).returncode == 0
    before = (home / "SOUL.md").read_bytes()
    mtime = (home / "SOUL.md").stat().st_mtime_ns
    result = _run(vault, home)
    assert result.returncode == 0, result.stderr
    assert (home / "SOUL.md").read_bytes() == before
    assert (home / "SOUL.md").stat().st_mtime_ns == mtime, "rewrote an unchanged SOUL"


def test_a_new_index_is_republished(tmp_path: Path) -> None:
    """Tonight's pages reach the injected index on the next publish."""
    vault, home = _fixture(tmp_path)
    assert _run(vault, home).returncode == 0
    (vault / "index.md").write_text("# Index\n\n- [[Sauna]]\n- [[Hot Tub]]\n")
    assert _run(vault, home).returncode == 0
    assert "Hot Tub" in (home / "SOUL.md").read_text()


def test_refuses_a_vault_with_no_index_and_leaves_the_soul_alone(tmp_path: Path) -> None:
    """A composition that cannot be made must not blank the injected identity."""
    vault, home = _fixture(tmp_path)
    assert _run(vault, home).returncode == 0
    published = (home / "SOUL.md").read_text()
    (vault / "index.md").unlink()
    result = _run(vault, home)
    assert result.returncode != 0
    assert (home / "SOUL.md").read_text() == published, "clobbered the live SOUL"


def test_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    """The compose staging area is the script's own, and it cleans up."""
    vault, home = _fixture(tmp_path)
    assert _run(vault, home).returncode == 0
    assert sorted(p.name for p in home.iterdir()) == ["SOUL.md"]
