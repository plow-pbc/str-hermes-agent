"""What the nightly must do with the vault ingest just grew.

Structural: bin/nightly.sh runs inside the container and calls `hermes chat`,
so exercising it end to end costs more than this class of bug is worth. What
this pins is that tonight's pages reach tomorrow's injected index — a nightly
that ingests new pages but leaves the SOUL describing last week's fails
silently — and that the corpus checks over those pages are acted on rather
than merely run.

The deploy caller is not pinned here: tests/test_runtime_config.py already
drives `restore-runtime-config.sh` as a subprocess and asserts on the SOUL it
produces, which is the behavioural seam this file could only approximate.
"""
from __future__ import annotations

from pathlib import Path

NIGHTLY = (Path(__file__).resolve().parents[1] / "bin" / "nightly.sh").read_text()


def test_nightly_rebuilds_the_soul_after_ingest() -> None:
    """Tonight's pages must be in tomorrow's injected index."""
    assert "build-soul" in NIGHTLY
    ingest = NIGHTLY.index("ingest-all")
    build = NIGHTLY.index("build-soul")
    assert build > ingest, "build-soul must run after ingest, not before"
    # And it must land where the gateway injects from. The destination is
    # overridable so `just test-wiki` can point it at scratch — which means the
    # e2e exercises every SOUL path except the production one. A wrong default
    # here exits 0 into a path nothing reads: no note, digest says ok, e2e green,
    # and the injected index quietly stops tracking the pages the run just wrote.
    assert 'SOUL_OUT:-$HERMES_HOME/SOUL.md' in NIGHTLY
    assert '"$SOUL_OUT"' in NIGHTLY


def test_nightly_reports_a_failed_soul_build() -> None:
    """Every nightly path reports; a silent SOUL failure breaks that contract."""
    tail = NIGHTLY[NIGHTLY.index("build-soul"):]
    assert "note " in tail or "notify " in tail


def test_nightly_runs_the_vault_suite_after_ingest_and_reports_failure() -> None:
    """The chain must act on what the corpus checks find, not on whether they ran.

    The bug this pins is the one that shipped: the lint step's condition tests
    the lint *turn*, so a run reporting 392 malformed citations exited 0 and
    sent a green digest while the vault failed 15 of its own tests. A gate that
    stops gating is silent by construction — it looks exactly like a clean
    night. After ingest, because the suite measures what ingest wrote.
    """
    assert "pytest" in NIGHTLY, "the chain runs no corpus checks"
    assert NIGHTLY.index("pytest") > NIGHTLY.index("ingest-all")

    # Invoked through uv, which the image has. Delegating to the vault's own
    # recipe would need `just`, which it does not — that fails every night and
    # reports a green corpus as broken, indistinguishable in the digest from
    # the real thing.
    assert "uv run --no-project" in NIGHTLY

    # The gate's own note, not any note after it in the file: a tail slice like
    # the SOUL check's above also catches that step's, so deleting this one
    # entirely would leave the test green — unpinning the one thing it holds.
    assert 'note "vault integrity FAILED' in NIGHTLY
    # A suite that could not run says so as itself. Both land in one digest
    # line, and a stale dependency pin reported as a broken corpus sends
    # someone reading pages that are fine.
    assert 'note "vault checks could not run' in NIGHTLY
