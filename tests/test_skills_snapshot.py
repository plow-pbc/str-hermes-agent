"""Behavior tests for bin/skills-snapshot.py.

Loaded by path because bin/ is not an importable package and the script's name
is not a valid module identifier — the same approach tests/test_hostex_poll.py
uses for the poller. The `.py` suffix is what makes that loadable at all, which
is why the scripts tests import carry one and the ones they shell out to
(`build-soul`, `ingest-all`) do not.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "skills_snapshot", ROOT / "bin" / "skills-snapshot.py"
)
snap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snap)


def store_with(tmp_path, skills, manifest=("airtable", "arxiv")):
    """A skill store: `skills` as name -> relative dir, plus a bundled manifest."""
    store = tmp_path / "skills"
    for path in skills:
        (store / path).mkdir(parents=True)
        (store / path / "SKILL.md").write_text(f"# {pathlib.Path(path).name}\n")
    if manifest is not None:
        (store / ".bundled_manifest").write_text(
            "".join(f"{name}:{i:032x}\n" for i, name in enumerate(manifest))
        )
    return store


def test_only_what_the_image_and_deploy_cannot_account_for_is_hermes_own(tmp_path):
    """The classification is subtractive, so a skill needs no marking to be
    recognised as Hermes's — which is the point, since Hermes marks nothing."""
    store = store_with(tmp_path, [
        "productivity/airtable",            # bundled
        "wiki-ingest",                      # linked by the boot script
        "productivity/property-guest-messaging",  # written by Hermes
    ])
    found = snap.authored(
        snap.find_skills(store), snap.read_bundled(store), {"wiki-ingest"}
    )
    # The category it was filed under travels with it, so the snapshot mirrors
    # the store's shape rather than flattening every skill to one directory.
    assert found == [pathlib.Path("productivity/property-guest-messaging")]


def test_a_name_the_image_also_ships_stops_the_run_rather_than_dropping_it(tmp_path):
    """The manifest carries names, not the categories the image files them
    under, so two paths sharing a bundled name are undecidable. Excluding both
    would discard whichever one Hermes wrote — silently, out of the snapshot
    that exists so a rebuild does not lose it."""
    store = store_with(tmp_path, [
        "productivity/airtable",  # the image's
        "guests/airtable",        # a distinct skill Hermes filed elsewhere
    ], manifest=("airtable",))
    with pytest.raises(SystemExit) as exit:
        snap.authored(snap.find_skills(store), snap.read_bundled(store), set())
    # Both paths are named, since resolving it means renaming one of them.
    assert "guests/airtable" in str(exit.value)
    assert "productivity/airtable" in str(exit.value)


def test_a_linked_skill_is_matched_by_path_so_its_namesake_elsewhere_is_not(tmp_path):
    """The live store holds two different skills called llm-wiki: Karpathy's at
    research/llm-wiki, bundled, and obsidian-wiki's at the top level, which the
    boot script installs there. Matched by name the pair would read as
    ambiguous; by path, each is accounted for by the owner that put it there."""
    store = store_with(tmp_path, ["llm-wiki", "research/llm-wiki"],
                       manifest=("llm-wiki",))
    assert snap.authored(snap.find_skills(store), {"llm-wiki"}, {"llm-wiki"}) == []


def test_a_symlink_in_a_skill_is_recorded_as_a_link_not_as_its_target(tmp_path):
    """The store sits beside .env and auth.json in ~/.hermes, and this workflow
    ends in `git commit`. Followed, a link there writes a live credential into
    the snapshot; recorded, it is a path in a text file and nothing more."""
    store = store_with(tmp_path, ["productivity/leaky"])
    secret = tmp_path / ".env"
    secret.write_text("HOSTEX_TOKEN=not-a-real-token\n")
    (store / "productivity/leaky/stolen").symlink_to(secret)

    snapshot = tmp_path / "agent-skills"
    snap.mirror(store, snap.find_skills(store), snapshot)
    copied = snapshot / "productivity/leaky/stolen"
    assert copied.is_symlink()
    assert copied.readlink() == secret


def test_a_missing_manifest_stops_the_run_rather_than_claiming_every_skill(tmp_path):
    """Without the manifest nothing is subtracted, so all seventy bundled skills
    would read as Hermes's and the whole store would be mirrored into the
    checkout. Wrong, and dressed as a dramatic finding — so it exits."""
    store = store_with(tmp_path, ["productivity/airtable"], manifest=None)
    with pytest.raises(SystemExit):
        snap.read_bundled(store)


def test_the_enabled_list_is_read_from_the_boot_script_that_installs_them():
    """Parsed, not restated: the boot script's ENABLED array is what actually
    lands those skills on the host, so enabling one stays a single edit there.
    A stale copy here would report a deploy-owned skill as Hermes's own."""
    linked = snap.read_linked(ROOT / "docker" / "cont-init.d" / "03-link-wiki-skills.sh")
    assert linked == {"llm-wiki", "wiki-ingest", "wiki-lint", "wiki-digest", "wiki-query"}


def test_a_missing_enabled_array_stops_the_run(tmp_path):
    """Same failure shape as the manifest: an empty set silently promotes every
    wiki skill into the snapshot as though Hermes had written it."""
    script = tmp_path / "03-link-wiki-skills.sh"
    script.write_text("#!/usr/bin/env bash\nSKILLS=(a b)\n")
    with pytest.raises(SystemExit):
        snap.read_linked(script)


def test_the_deployed_clone_is_refused_and_a_development_one_is_not(tmp_path, monkeypatch):
    """The live store is on wakeup, where the deployed clone also sits — so it
    is the easy checkout to be standing in, and writing there dirties a tree
    `check-deploy-clean.sh` exempts nothing from, blocking every later deploy."""
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(SystemExit):
        snap.refuse_in_the_deployed_clone(tmp_path / "services" / "sams-str-hermes-agent")
    snap.refuse_in_the_deployed_clone(tmp_path / "Hacking" / "str6")


def test_a_skill_hermes_deleted_leaves_the_snapshot_as_a_deletion(tmp_path):
    """The snapshot is rebuilt rather than merged, so removing a skill on the
    host shows up in `git diff` instead of lingering as a file nothing has."""
    store = store_with(tmp_path, ["productivity/gone", "productivity/kept"])
    snapshot = tmp_path / "agent-skills"
    snap.mirror(store, snap.find_skills(store), snapshot)
    assert (snapshot / "productivity/gone/SKILL.md").exists()

    snap.mirror(store, [pathlib.Path("productivity/kept")], snapshot)
    assert not (snapshot / "productivity/gone").exists()
    assert (snapshot / "productivity/kept/SKILL.md").exists()


def test_a_store_with_nothing_authored_does_not_empty_the_snapshot(tmp_path):
    """Because mirror rebuilds, an empty result would delete the record — and
    a rebuilt host, which has the bundled skills back and none of Hermes's, is
    exactly the state that produces one. main refuses before mirroring."""
    store = store_with(tmp_path, ["productivity/airtable"], manifest=("airtable",))
    snapshot = tmp_path / "agent-skills"
    snap.mirror(store, snap.find_skills(store), snapshot)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(snap, "SNAPSHOT", snapshot)
    monkey.setattr(snap, "ROOT", tmp_path)
    monkey.setattr(snap, "skills_dir", lambda: store)
    monkey.setattr(snap, "read_linked", lambda _: set())
    with pytest.raises(SystemExit):
        snap.main()
    monkey.undo()
    assert (snapshot / "productivity/airtable/SKILL.md").exists()
