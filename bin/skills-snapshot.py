#!/usr/bin/env python3
"""Copy the skills Hermes wrote for itself into the checkout, for review.

After a turn, Hermes forks a background review (`agent/background_review.py` in
the image) that may create or patch a skill under `$HERMES_HOME/skills`. It
announces the write in chat — "Self-improvement review: Patched SKILL.md in
skill 'x'" — and that announcement is the only trace. The write itself lands in
a directory no git repo covers, the deploy does not install, and a host rebuild
does not reproduce.

This mirrors those skills into `agent-skills/` so `git diff` shows what changed
and the operator decides whether to keep it. Run it when Hermes says it patched
something, or before a rebuild.

A record, not a source. Nothing installs `agent-skills/` back into the runtime:
the deploy owns `runtime/`, and owning these too would revert Hermes's next
edit on every deploy — the failure #61 and #66 already recorded for other paths.
Restoring after a rebuild is a deliberate copy; README § Skills Hermes wrote has
the command.

Three kinds of skill live in that store and only the third is ours:

  bundled   shipped in the image, listed in `.bundled_manifest`, reproduced by
            pulling the image. Off-limits to the review agent.
  linked    copied in at container start by `docker/cont-init.d/03-link-wiki-
            skills.sh` from the obsidian-wiki wheel; its ENABLED array is the
            list. Reproduced by rebuilding.
  authored  everything else — what Hermes wrote. Reproduced by nothing.

So the classification is subtractive: whatever the image and the deploy cannot
account for is what needs tracking. Both lists are read at run time rather than
restated here, because a copy of either would drift silently and the drift
would read as Hermes having written a skill it did not.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "agent-skills"
LINK_SCRIPT = ROOT / "docker" / "cont-init.d" / "03-link-wiki-skills.sh"
ENABLED_BLOCK = re.compile(r"^ENABLED=\((.*?)^\)", re.MULTILINE | re.DOTALL)


def refuse_in_the_deployed_clone(root: pathlib.Path) -> None:
    """Stop if this is the production checkout under `~/services`.

    The snapshot writes into its own checkout, and the live skill store only
    exists on wakeup — where the deployed clone also sits, so it is the easy
    one to be standing in. Writing there dirties the tree, and
    `scripts/check-deploy-clean.sh` grants no exemptions: every later deploy
    stops until someone works out what changed production. That is the failure
    `#61`, `#66`, and `#67` each recorded once already. Production also never
    originates commits, and a snapshot exists to be committed.
    """
    if (pathlib.Path.home() / "services") in root.parents:
        sys.exit(
            f"skills-snapshot: {root} is the deployed clone — snapshotting here "
            f"would dirty it and block the next deploy. Run this from a "
            f"development checkout on the same host; it reads the same store."
        )


def skills_dir() -> pathlib.Path:
    """The runtime skill store — `~/.hermes/skills` on the host."""
    home = os.environ.get("HERMES_HOME", str(pathlib.Path.home() / ".hermes"))
    return pathlib.Path(home) / "skills"


def read_bundled(store: pathlib.Path) -> set[str]:
    """Skill names the image shipped, from its `name:hash` manifest.

    Raises when the manifest is absent rather than returning an empty set. An
    empty set would classify all seventy bundled skills as Hermes's own and
    mirror the entire store into the checkout — a wrong answer that looks like
    a dramatic finding, which is the shape worth failing on.

    Only the names are read. The hashes are the image's own bookkeeping in an
    undocumented format, and reproducing whatever they digest would buy a
    tamper check on skills the review agent is already forbidden to touch.
    """
    manifest = store / ".bundled_manifest"
    if not manifest.exists():
        sys.exit(f"skills-snapshot: no bundled manifest at {manifest}")
    return {
        line.split(":", 1)[0]
        for line in manifest.read_text().splitlines()
        if ":" in line
    }


def read_linked(script: pathlib.Path) -> set[str]:
    """Skill names the boot script copies in, from its ENABLED array.

    Parsed from the script rather than duplicated, so enabling a wiki skill
    stays a one-line edit there. Raises if the array cannot be found: a silent
    empty set would report those skills as Hermes's own on the next run.
    """
    match = ENABLED_BLOCK.search(script.read_text())
    if not match:
        sys.exit(f"skills-snapshot: no ENABLED=( ... ) array in {script}")
    return {
        word
        for line in match.group(1).splitlines()
        for word in [line.split("#", 1)[0].strip()]
        if word
    }


def find_skills(store: pathlib.Path) -> list[pathlib.Path]:
    """Every installed skill, as paths relative to the store.

    A skill is a directory holding a SKILL.md; the store nests most of them one
    category deep (`productivity/property-guest-messaging`) and keeps others at
    the top. The relative path is what the snapshot mirrors, so a skill keeps
    the category it was filed under.

    Paths rather than names, even though the subtraction below matches on name:
    two categories can hold the same directory name, and keying on it would
    collapse them to whichever the walk reached last. The one it dropped would
    be the authored one as readily as not, and the run would print a plausible
    count either way — a green run that discards the thing it exists to
    capture.
    """
    return sorted(md.parent.relative_to(store) for md in store.rglob("SKILL.md"))


def authored(installed: list[pathlib.Path], bundled: set[str],
             linked: set[str]) -> list[pathlib.Path]:
    """The skills Hermes wrote: what neither the image nor the deploy owns.

    Linked skills are matched by exact path. The boot script installs them at
    the top of the store — `$SKILLS_DIR/$skill` — so there is no question which
    directory a name means, and `research/llm-wiki` stays a different skill
    from the `llm-wiki` the wheel supplies. They are different skills: one is
    Karpathy's, bundled; the other is obsidian-wiki's.

    Bundled ones can only be matched by name, because the manifest records
    names and not the categories the image files them under. That asymmetry is
    what the exit below is for. One path carrying a bundled name is the bundled
    skill. Two carrying it means one of them is and nothing here can say which,
    so excluding both would drop a skill Hermes wrote — silently, and out of
    the snapshot that exists to survive the rebuild. Stopping is the honest
    answer, and the operator resolves it by renaming.
    """
    rest = [path for path in installed if str(path) not in linked]

    by_name: dict[str, list[pathlib.Path]] = {}
    for path in rest:
        by_name.setdefault(path.name, []).append(path)
    ambiguous = sorted(
        str(path)
        for name, paths in by_name.items() if name in bundled and len(paths) > 1
        for path in paths
    )
    if ambiguous:
        sys.exit(
            "skills-snapshot: two skills share a name the image also ships, "
            f"so which is Hermes's is undecidable: {', '.join(ambiguous)}. "
            "Rename the one Hermes wrote — otherwise it is not snapshotted."
        )

    return [path for path in rest if path.name not in bundled]


def mirror(store: pathlib.Path, skills: list[pathlib.Path],
           snapshot: pathlib.Path) -> None:
    """Replace the snapshot with the given skills, so deletions show up too.

    Rebuilt rather than merged: a skill Hermes deleted, or one that graduated
    into the image, should leave the snapshot as a deletion in `git diff`
    instead of lingering as a file nothing on the host still has.

    `symlinks=True` copies a link as a link. Following one would read whatever
    it points at into a file this workflow then asks the operator to commit,
    and the store sits beside `.env` and `auth.json` in `~/.hermes` — so a link
    there is a live Hostex or OpenAI credential landing in git. Recorded as a
    link, it is a path in a text file and nothing more. `REVIEW.md`'s carve-out
    3 keeps a new artifact for the token to reach blocking, deferral or not.
    """
    if snapshot.exists():
        shutil.rmtree(snapshot)
    for path in skills:
        shutil.copytree(store / path, snapshot / path, symlinks=True)


def main() -> None:
    refuse_in_the_deployed_clone(ROOT)
    store = skills_dir()
    if not store.is_dir():
        sys.exit(f"skills-snapshot: no skill store at {store}")

    installed = find_skills(store)
    skills = authored(installed, read_bundled(store), read_linked(LINK_SCRIPT))
    if not skills:
        # `mirror` rebuilds, so an empty result would delete the snapshot and
        # copy nothing back — and the run that produces it is the one this
        # script exists for. A rebuilt host has the bundled and linked skills
        # restored and none of Hermes's, so a snapshot taken before the restore
        # copy would erase the only record of what to restore. Anything not yet
        # committed goes with it.
        sys.exit(
            f"skills-snapshot: nothing in {store} was written by Hermes — "
            f"refusing to empty {SNAPSHOT.relative_to(ROOT)}. If this is a "
            f"rebuilt host, restore the snapshot before running this."
        )
    mirror(store, skills, SNAPSHOT)

    print(f"{len(installed)} skills installed, {len(skills)} written by Hermes:")
    for path in skills:
        print(f"  {path}")
    print(f"\nMirrored into {SNAPSHOT.relative_to(ROOT)} — `git diff` to see "
          f"what changed since the last snapshot.")


if __name__ == "__main__":
    main()
