"""What the nightly does with the vault ingest -- and what it must NOT do.

Structural: bin/nightly.sh runs inside the container and calls `hermes chat`,
so exercising it end to end costs more than this class of bug is worth.

The contract inverted here. The chain used to recompose SOUL.md from the vault
index every night, and this file pinned that it did. It cannot any more, and
should not: the base image treats SOUL.md as provisioned identity -- plow-init
chowns it root:root inside a root-owned sticky home at every container start,
so the chain (running as hermes) cannot replace it, and the machinery to push
nightly content through that freeze is exactly what this repo removed. The
index lives in the vault the agent already reads, and the persona points at it.

So what is pinned now is the absence: no SOUL write from in-container code, and
a persona that still names the index, because the two together are what keep
tonight's pages reachable tomorrow.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NIGHTLY = (ROOT / "bin" / "nightly.sh").read_text()
PERSONA = (ROOT / "runtime" / "SOUL.md").read_text()
RESTORE = (ROOT / "scripts" / "restore-runtime-config.sh").read_text()


def test_the_nightly_does_not_write_the_soul() -> None:
    """In-container code cannot publish the identity, so it must not try.

    It ran as hermes against a root-owned target and failed with EPERM once a
    night, note-and-continued, and surfaced only as a line in the digest.
    """
    assert "build-soul" not in NIGHTLY
    assert "SOUL_OUT" not in NIGHTLY
    assert "SOUL.md" not in NIGHTLY


def test_the_persona_sends_the_agent_to_the_index() -> None:
    """The pointer is now the whole mechanism, so it is the thing to pin.

    Nothing pastes the index into the prompt any more. If the persona stops
    naming the file, the corpus is still compiled nightly and simply never
    read -- an agent answering from priors about properties it has pages for,
    with nothing anywhere reading as broken.
    """
    assert "index.md" in PERSONA
    assert "$HERMES_HOME/repo/vault" in PERSONA


def test_the_deploy_publishes_the_persona_through_the_publisher() -> None:
    """The one writer of SOUL.md is the host, at deploy, and it composes nothing.

    Direct is what broke: after the first boot the target is root-owned inside
    a sticky home, where the deploy user can neither rename over it nor write
    it. publish-soul is what knows to escalate.
    """
    assert "publish-soul" in RESTORE
    assert "build-soul" not in RESTORE


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
