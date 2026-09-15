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

So what is pinned now is the absence: no SOUL write from in-container code --
and, beside it, the pointer that keeps tonight's pages reachable tomorrow: the
persona naming the index rather than carrying it.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NIGHTLY = (ROOT / "bin" / "nightly.sh").read_text()
PERSONA = (ROOT / "runtime" / "persona.md").read_text()


def test_the_nightly_does_not_write_the_soul() -> None:
    """In-container code cannot publish the identity, so it must not try.

    It ran as hermes against a root-owned target and failed with EPERM once a
    night, note-and-continued, and surfaced only as a line in the digest.
    """
    assert "build-soul" not in NIGHTLY
    assert "SOUL_OUT" not in NIGHTLY
    assert "SOUL.md" not in NIGHTLY


def test_the_persona_names_the_index_and_does_not_carry_it() -> None:
    """Pasting the vault index into the identity is the regression this repo removed.

    It forced nightly content through a file the platform freezes root:root at
    every boot, and needed a root-escalation-and-scheduling pipeline to do it.
    The persona sends the agent to the file instead; if it ever grows the corpus
    itself, that pipeline is back.
    """
    assert "index.md" in PERSONA, "the persona no longer names the vault index"
    assert "## Properties" not in PERSONA, "the vault index was composed into the persona"
