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


def test_nightly_does_not_write_the_soul() -> None:
    """The nightly runs as hermes and structurally cannot publish.

    plow-init hardens $HERMES_HOME/SOUL.md to root:root inside a root-owned
    sticky home at every boot, so build-soul's closing rename fails with EPERM
    from in here. It failed silently-ish for exactly one night before this
    changed: note-and-continue, so the only symptom was a digest prefix.
    """
    assert "build-soul" not in NIGHTLY
    assert "SOUL_OUT" not in NIGHTLY


def test_nightly_reports_an_index_the_publish_has_not_caught_up_to() -> None:
    """Tonight's pages are not in the injected index until the promote runs.

    The chain must say so, or the staleness the old build-soul step existed to
    prevent returns as silence -- an agent confidently describing last week's
    corpus, which is the quietest failure this repo can ship.
    """
    assert 'note "injected index is stale' in NIGHTLY
    assert "promote-vault" in NIGHTLY
    # After ingest: it measures what tonight actually wrote.
    assert NIGHTLY.index("injected index is stale") > NIGHTLY.index("ingest-all")


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
