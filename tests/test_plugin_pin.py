"""The pinned plugin is the one that serves.

#138 stopped bind-mounting this repo's fork of plow-pbc/seed-hermes-plow's Plow
Chat plugin over the installed one; `agent-mgr install-plugin str` now puts the upstream
copy into ~/.hermes/plugins from the SHA agent-mgr pins fleet-wide. What
made that switch load-bearing is provenance: before it, the running adapter came
from a working tree, which is how the group-chat work ran on wakeup for hours
while `main` said something else.

A mount over /opt/data/plugins is the one thing that puts that back *silently* —
the ref goes on reading like provenance while other bytes serve. The two
neighbouring failures don't need a fence here and had one in the first draft of
this file: a non-SHA ref aborts `agent-mgr install-plugin str` at its guard, before the
curl and before anything is installed, and a re-vendored plugins/ directory is
inert until something mounts it — which is this assertion again.

#138's own review dropped all three before merging, this one as fencing
repository shape by naming a deleted path. So, plainly, because that reading is
what deletes this test: `/opt/data/plugins` is not the deleted directory. It is
the container location the installed plugin occupies, and a mount landing there
outranks it with nothing anywhere reporting the swap.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_does_not_shadow_the_installed_plugin():
    """`- ~/.hermes:/opt/data` is how the plugin arrives, and must keep passing.

    Hence the /plugins segment in the pattern: it matches only a mount landing
    *inside* that directory, which is the shape #138 removed —
    `- ./plugins/plow-chat-platform:/opt/data/plugins/plow-chat-platform`.
    Asserted on the target rather than the source because the target is what
    shadows, whatever the source turns out to be.
    """
    # The override, not compose.yml: agent-mgr owns the service definition now,
    # and this repo can still add a mount that shadows the plugin.
    compose = (ROOT / "compose.override.yml").read_text()
    assert not re.search(r"^\s*- \S*:/opt/data/plugins", compose, re.M), (
        "compose.override.yml mounts something over /opt/data/plugins, shadowing the "
        "copy `agent-mgr install-plugin str` puts there from the pinned SHA"
    )
