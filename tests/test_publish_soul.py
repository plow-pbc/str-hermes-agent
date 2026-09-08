"""The SOUL reaches $HERMES_HOME only through this script, and only at deploy.

What it publishes is the tracked persona VERBATIM. That is the whole design:
the base image treats SOUL.md as provisioned identity -- plow-init chowns it
root:root inside a root-owned sticky home at every container start -- so the one
thing that must never live in it is content that changes nightly. The vault
index does; it stays in the vault, and the persona names the path.

These tests run against a scratch home, which is never hardened, so they cover
the direct-write path. The root escalation is only reachable against a real
hardened target and is verified by hand against the live container.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT / "scripts" / "publish-soul"
PERSONA = ROOT / "runtime" / "SOUL.md"


def _run(home: Path, **extra: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "AGENT_HOME": str(home),
        # Never reached: the scratch home is writable, so the direct path wins.
        "AGENT_CONTAINER": "publish-soul-tests-no-such-container",
        **extra,
    }
    return subprocess.run([str(PUBLISH)], env=env, text=True, capture_output=True)


def test_publishes_the_persona_verbatim_at_plow_inits_mode(tmp_path: Path) -> None:
    """Byte-identical to the tracked file, at the mode the boot hardening sets."""
    home = tmp_path / "home"
    home.mkdir()
    result = _run(home)
    assert result.returncode == 0, result.stderr
    soul = home / "SOUL.md"
    assert soul.read_bytes() == PERSONA.read_bytes()
    # 0644 is what harden_home() sets at every container start, so publishing at
    # that mode makes the hardening a no-op rather than a change it undoes.
    assert stat.S_IMODE(soul.stat().st_mode) == 0o644


def test_carries_the_index_path_and_not_the_index(tmp_path: Path) -> None:
    """The regression this whole change exists to prevent.

    Pasting the vault index in is what forced a root-escalation-and-scheduling
    pipeline through a file the platform freezes. The persona sends the agent
    to the file instead; if the published SOUL ever grows the corpus itself,
    that pipeline is back.
    """
    home = tmp_path / "home"
    home.mkdir()
    assert _run(home).returncode == 0
    body = (home / "SOUL.md").read_text()
    assert "index.md" in body
    assert "## Properties" not in body, "the vault index was composed into the SOUL"


def test_second_run_is_quiet_and_rewrites_nothing(tmp_path: Path) -> None:
    """Every deploy calls this; only the rare one that edits the persona writes."""
    home = tmp_path / "home"
    home.mkdir()
    assert _run(home).returncode == 0
    mtime = (home / "SOUL.md").stat().st_mtime_ns
    result = _run(home)
    assert result.returncode == 0, result.stderr
    assert "already current" in result.stdout
    assert (home / "SOUL.md").stat().st_mtime_ns == mtime, "rewrote an unchanged SOUL"


def test_refuses_an_empty_persona_and_leaves_the_live_soul_alone(tmp_path: Path) -> None:
    """An empty identity reads like a healthy deploy, so it must fail loudly.

    Exercised through a repo copy rather than by emptying the tracked file:
    the persona's own path is what the script derives from its location.
    """
    home = tmp_path / "home"
    home.mkdir()
    assert _run(home).returncode == 0
    published = (home / "SOUL.md").read_text()

    fake_repo = tmp_path / "repo"
    (fake_repo / "scripts").mkdir(parents=True)
    (fake_repo / "runtime").mkdir()
    (fake_repo / "runtime" / "SOUL.md").write_text("")
    (fake_repo / "scripts" / "publish-soul").write_bytes(PUBLISH.read_bytes())
    (fake_repo / "scripts" / "publish-soul").chmod(0o755)

    result = subprocess.run(
        [str(fake_repo / "scripts" / "publish-soul")],
        env={**os.environ, "AGENT_HOME": str(home), "AGENT_CONTAINER": "none"},
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert (home / "SOUL.md").read_text() == published, "clobbered the live SOUL"
