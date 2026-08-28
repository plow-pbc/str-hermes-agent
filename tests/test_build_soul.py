"""SOUL.md is injected into every turn, so what build-soul emits is what the
agent knows on every message — guest drafts, cron jobs, and interactive chat
alike.

Behavioural, not structural: these run the real script against a temporary
repo and assert on the file it produces. The failure this guards is quiet —
a SOUL missing its index is an agent that believes it has no memory and says
so confidently, with nothing in any log to say why.
"""
from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_SOUL = ROOT / "bin" / "build-soul"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A bare vault. Nothing is derived from where it sits anymore."""
    v = tmp_path / "vault"
    (v / "properties").mkdir(parents=True)
    (v / "index.md").write_text("# Index\n\n- [Thermostat](operations/t.md) — how heat works\n")
    return v


@pytest.fixture
def persona(tmp_path: Path) -> Path:
    p = tmp_path / "SOUL.md"
    p.write_text((ROOT / "runtime/SOUL.md").read_text())
    return p


def build(vault: Path, persona: Path, out: Path,
          script: Path = BUILD_SOUL) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(script), str(vault), str(persona), str(out)],
        text=True, capture_output=True,
    )


def test_the_persona_is_taken_from_the_argument_not_the_vault_location(
    vault: Path, persona: Path, tmp_path: Path
) -> None:
    """The two callers no longer share a filesystem layout.

    The old derivation read the persona as a sibling of the vault, which held
    only while both callers had the vault inside the checkout. The host caller's
    vault now lives outside it, where that derivation resolves to a path nothing
    creates — and `nightly.sh` note-and-continues past a failed rebuild, so it
    would have surfaced as a silently stale injected index.
    """
    out = tmp_path / "SOUL.md"
    result = build(vault, persona, out)
    assert result.returncode == 0, result.stderr
    soul = out.read_text()
    assert "^[ambiguous]" in soul          # the persona's reading rules
    assert "Thermostat" in soul            # the vault's index


@pytest.mark.parametrize(
    ("missing", "message"),
    [("persona", "no persona at"), ("index", "no index at")],
)
def test_a_missing_input_writes_nothing_and_fails(
    vault: Path, persona: Path, tmp_path: Path, missing: str, message: str
) -> None:
    """Either input absent means a wrong path, not that memory is empty.

    One contract, two inputs — a partial SOUL is the failure both guards exist
    to prevent, so both rows assert the output was never created rather than
    only that the exit code moved.
    """
    if missing == "persona":
        persona.unlink()
    else:
        (vault / "index.md").unlink()
    out = tmp_path / "out.md"

    result = build(vault, persona, out)

    assert result.returncode == 1
    assert message in result.stderr
    assert not out.exists()


def test_the_soul_is_private(vault: Path, persona: Path, tmp_path: Path) -> None:
    """It carries the operator's whole index; the scheduler's umask is 022."""
    out = tmp_path / "SOUL.md"
    assert build(vault, persona, out).returncode == 0
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
