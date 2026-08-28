"""The read side of the wiki has to be linked, not just the write side.

The wheel ships 37 skills and the container links only what ENABLED names. For
three weeks that was four write-side skills — ingest, lint, digest and the
theory skill — so the vault was compiled nightly and had no retrieval tool at
all. The name is confirmed present in the pinned wheel; an absent one exits 1
in cont-init and takes the gateway down at boot, not just the skill.
"""
from __future__ import annotations

import re
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "docker" / "cont-init.d" / "03-link-wiki-skills.sh"
).read_text()


def test_wiki_query_is_linked() -> None:
    """Without it the agent can compile knowledge and never look anything up."""
    assert re.search(r"ENABLED=\([^)]*\bwiki-query\b", SCRIPT, re.S), SCRIPT
