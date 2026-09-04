"""Which conversations `bin/ingest-all` considers unprocessed, and feeds.

The manifest is the pipeline's memory of what it has already distilled, and
this is the one place that reads it. Getting the comparison wrong fails
silently in both directions — re-ingesting a conversation appends duplicate
facts to its page, and skipping a changed one drops guest replies on the floor
while the run still reports success.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INGEST_ALL = REPO / "bin" / "ingest-all"

HASHED = "---\npayload_hash: {}\nproperty: lake-house\n---\n\nhi\n"


def record(vault: Path, raw_file: Path) -> None:
    """Record a raw file in the manifest the way wiki-ingest does.

    sha256 over the file's bytes, derived rather than written as a literal: a
    fixture hard-coding the same string on both sides of the comparison passes
    whatever the comparison is, and one that did exactly that is why this suite
    stayed green while the loop could not finish a run.
    """
    manifest = vault / ".manifest.json"
    sources = json.loads(manifest.read_text())["sources"] if manifest.exists() else {}
    sources[f"/opt/data/repo/vault/{raw_file.relative_to(vault)}"] = {
        "content_hash": "sha256:" + hashlib.sha256(raw_file.read_bytes()).hexdigest()
    }
    manifest.write_text(json.dumps({"sources": sources}))


@pytest.fixture
def vault(tmp_path):
    """A vault holding one cached conversation, already recorded as ingested."""
    raw = tmp_path / "_raw" / "hostex"
    raw.mkdir(parents=True)
    conversation = raw / "0-111.md"
    conversation.write_text(HASHED.format("sha256:AAA"))
    record(tmp_path, conversation)
    return tmp_path


@pytest.fixture
def run_ingest(tmp_path):
    """Run ingest-all against a vault and report what actually reached the agent.

    `agent-mgr` is stubbed so no container is needed, and it records the prompt it
    was invoked with — the only evidence of what was *fed*, as opposed to what
    the loop counted. The stub makes no progress, so the script's own stall
    detection bounds the run.
    """
    stub = tmp_path / "stub"
    stub.mkdir()
    fed = tmp_path / "fed.txt"

    def run(vault_path: Path, turn_exit: int = 0):
        # agent-mgr, not docker: the host branch of ingest-all now reaches the
        # container through it, so that is the boundary a stub has to stand at.
        # The HERMES_HOME query is answered before $fed even sees it and
        # without turn_exit applying to it: it is not a turn, so a
        # failed-turn test would otherwise never reach the turn it means to
        # fail (wrong diagnostic), and every fed-count assertion would be off
        # by one call that fed nothing to the agent.
        (stub / "agent-mgr").write_text(
            '#!/bin/sh\n'
            'case "$*" in\n'
            '  *\'printf %s "${HERMES_HOME:?}"\'*) printf %s /opt/data; exit 0 ;;\n'
            'esac\n'
            f'echo "$@" >> {fed}\n'
            f'exit {turn_exit}\n'
        )
        (stub / "agent-mgr").chmod(0o755)
        result = subprocess.run(
            [str(INGEST_ALL), str(vault_path)],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PATH": f"{stub}:{os.environ['PATH']}"},
        )
        return result, (fed.read_text() if fed.exists() else "")

    return run


def test_an_unchanged_conversation_is_not_re_ingested(vault, run_ingest):
    """Re-feeding a page's own source appends its facts to that page twice."""
    result, fed = run_ingest(vault)
    assert "0 conversation(s) not yet in the manifest" in result.stdout
    assert "manifest covers everything in scope" in result.stdout
    assert fed == "", "an unchanged conversation must not reach the agent at all"


def test_a_conversation_whose_content_changed_is_queued_again(vault, run_ingest):
    """A guest reply rewrites the raw file in place — same name, new hash.

    Comparing basenames alone marked it done forever, so every reply after the
    first ingest was silently dropped while the run still reported success.
    """
    raw = vault / "_raw" / "hostex" / "0-111.md"
    raw.write_text(raw.read_text().replace("sha256:AAA", "sha256:BBB"))

    result, fed = run_ingest(vault)
    assert "1 conversation(s) not yet in the manifest" in result.stdout
    assert "_raw/hostex/0-111.md" in fed


@pytest.mark.parametrize("extra", [0, 1, 3], ids=["one-remaining", "two", "four"])
def test_every_remaining_conversation_reaches_the_agent(vault, run_ingest, extra):
    """`read` discards a final line with no newline after it.

    That dropped the last conversation of every batch whatever its size, and a
    round with exactly one remaining fed nothing at all — so the loop saw no
    progress, stalled twice, and aborted a run that had real work to do.
    """
    raw = vault / "_raw" / "hostex"
    (raw / "0-111.md").write_text(HASHED.format("sha256:BBB"))
    expected = ["0-111.md"]
    for i in range(extra):
        name = f"0-2{i}.md"
        (raw / name).write_text(HASHED.format(f"sha256:N{i}"))
        expected.append(name)

    result, fed = run_ingest(vault)

    assert f"{len(expected)} conversation(s) not yet in the manifest" in result.stdout
    missing = [n for n in expected if f"_raw/hostex/{n}" not in fed]
    assert not missing, f"queued but never fed to the turn: {missing}"


def test_the_work_list_never_round_trips_through_a_file():
    """`before` and the fed batch both read the scope just recomputed.

    The loop used to write its work list to a fixed path and derive both from
    the file. That write was unchecked and `set -e` is off, so a failed
    redirect — a stale root-owned file from an earlier run, in production —
    left the previous run's list in place and fed from it while the round
    accounting looked healthy.

    Structural, because the defect needs the write to *fail*: the path is a
    literal, so no fixture can make it unwritable, and a behavioural test that
    exercises the successful case passes the buggy implementation. Asserting
    the shape is honest where exercising it would be theatre.
    """
    code = "\n".join(
        l for l in INGEST_ALL.read_text().splitlines()
        if not l.lstrip().startswith("#")
    )
    assert 'before=$(printf \'%s\\n\' "$SCOPE" | grep -c .)' in code
    assert 'done < <(printf \'%s\\n\' "$SCOPE" | head -n "$BATCH")' in code


@pytest.mark.parametrize(
    ("corrupt_manifest", "turn_exit", "diagnostic"),
    [
        pytest.param(True, 0, "cannot determine what remains", id="unreadable-manifest"),
        pytest.param(False, 1, "the agent turn failed", id="failed-turn"),
    ],
)
def test_a_failure_aborts_the_run_rather_than_carrying_on(
    vault, run_ingest, corrupt_manifest, turn_exit, diagnostic
):
    """Two ways to lose the loop's footing; neither may read as work completed.

    The scope query is a python heredoc, and a crash in it printed nothing —
    which the loop read as "manifest covers everything in scope", so a corrupt
    manifest produced a clean exit 0 having ingested nothing at all.

    A failed turn is the more expensive one. The turn may have written a page
    before it died without recording it in the manifest, so carrying on to the
    next round re-feeds that conversation and appends its facts to the page a
    second time. Continuing costs more than stopping, which is why the count of
    invocations is asserted and not just the exit status.
    """
    if corrupt_manifest:
        (vault / ".manifest.json").write_text("{ not json")
    else:
        raw = vault / "_raw" / "hostex" / "0-111.md"
        raw.write_text(raw.read_text().replace("sha256:AAA", "sha256:BBB"))

    result, fed = run_ingest(vault, turn_exit=turn_exit)

    assert result.returncode == 1
    assert diagnostic in result.stderr
    assert "covers everything in scope" not in result.stdout
    assert len([line for line in fed.splitlines() if line.strip()]) == (
        0 if corrupt_manifest else 1
    ), "the run must stop at the first failure, not attempt another turn"



@pytest.mark.parametrize(
    "archive",
    [("_raw", "_archived", "hostex"), ("_raw", "hostex", "_archived")],
    ids=["archived-sibling", "archived-nested"],
)
def test_an_archived_conversation_is_fed_at_the_path_it_actually_lives_at(
    vault, run_ingest, archive
):
    """wiki-ingest archives a processed conversation, and not to one place.

    A live run left ten under the first layout and twenty under the second.
    Scoping to named directories put those twenty outside the query — and a
    conversation the query cannot see is one the coverage assertion passes over
    silently, which is the hole the assertion exists to close.

    The assertion is the whole relative path, not the basename. Being in scope
    is only half of it: the feed turns each entry into a path for the agent, so
    a conversation found in the archive but handed over as `_raw/hostex/<id>.md`
    is a turn that processes nothing. A basename substring matches either path
    and would survive exactly that.
    """
    moved = vault.joinpath(*archive)
    moved.mkdir(parents=True, exist_ok=True)
    (vault / "_raw" / "hostex" / "0-111.md").replace(moved / "0-111.md")
    (moved / "0-111.md").write_text(HASHED.format("sha256:BBB"))
    expected = str(pathlib.Path(*archive) / "0-111.md")

    result, fed = run_ingest(vault)

    assert "1 conversation(s) not yet in the manifest" in result.stdout
    assert expected in fed, f"fed something other than {expected}"


def test_a_conversation_in_both_places_is_fed_once_from_the_live_copy(
    vault, run_ingest
):
    """Refetching an archived conversation leaves a copy in each directory.

    Feeding both wastes a turn on a duplicate, and feeding the archived one
    hands the agent the stale text. The live copy wins and it is fed once.
    """
    archived = vault / "_raw" / "_archived" / "hostex"
    archived.mkdir(parents=True, exist_ok=True)
    (archived / "0-111.md").write_text(HASHED.format("sha256:OLD"))
    (vault / "_raw" / "hostex" / "0-111.md").write_text(HASHED.format("sha256:NEW"))
    # The archived copy is given the newer mtime, deliberately. Selection is
    # live-first and then most-recent, and writing the live copy second makes
    # the recency term alone produce the right answer — so the rule under test
    # here goes unexercised and deleting it keeps the suite green. Inverting the
    # timestamps leaves only "live beats archived" able to pick correctly.
    os.utime(archived / "0-111.md", (10_000, 10_000))
    os.utime(vault / "_raw" / "hostex" / "0-111.md", (1, 1))

    result, fed = run_ingest(vault)

    assert "1 conversation(s) not yet in the manifest" in result.stdout
    assert "_raw/hostex/0-111.md" in fed
    assert "_raw/_archived/hostex/0-111.md" not in fed, "fed the stale archived copy"


def test_two_archived_copies_feed_the_newer_one(vault, run_ingest):
    """wiki-ingest archives into either of two layouts, so both can hold a copy.

    A refresh writes to `_raw/hostex/` and leaves the archived copy alone; once
    the refreshed one is archived too, the same name exists twice with different
    bytes and neither is live. Choosing by sort order picks by path spelling,
    which would feed the superseded text and re-derive facts the newer copy has
    already replaced. The rows are ordered so the stale copy sorts first.
    """
    older = vault / "_raw" / "_archived" / "hostex"
    newer = vault / "_raw" / "hostex" / "_archived"
    for d in (older, newer):
        d.mkdir(parents=True, exist_ok=True)
    (vault / "_raw" / "hostex" / "0-111.md").unlink()
    (older / "0-111.md").write_text(HASHED.format("sha256:STALE"))
    (newer / "0-111.md").write_text(HASHED.format("sha256:FRESH"))
    os.utime(older / "0-111.md", (1, 1))
    os.utime(newer / "0-111.md", (10_000, 10_000))

    result, fed = run_ingest(vault)

    assert "1 conversation(s) not yet in the manifest" in result.stdout
    assert "_raw/hostex/_archived/0-111.md" in fed, "fed the older archived copy"
    assert "_raw/_archived/hostex/0-111.md" not in fed


def test_a_conversation_recorded_under_two_paths_stays_out_of_scope(
    vault, run_ingest
):
    """One conversation can hold a manifest entry per location it has occupied.

    wiki-ingest keys on the copy it processed, so archiving one and refetching
    another leaves two entries sharing a basename with different hashes. Reading
    those into one value per basename made the winner arbitrary — whichever sat
    later in the file — and that was the stale archived entry, while the file
    side deliberately feeds the live copy. Stale hash against live bytes never
    matches, so the conversation was in scope permanently: every round ingested
    it, recorded it, moved nothing the query could see, and the stall guard
    aborted the run before lint, SOUL and the digest. Production lost two
    nights to it, 2026-08-23 and -24.

    The stale entry is written second because that is the half that made it
    fail: an ordering where the live entry lands last passes on the old code
    too, and would have let this ship again.
    """
    archived = vault / "_raw" / "_archived"
    archived.mkdir(parents=True, exist_ok=True)
    stale = archived / "0-111.md"
    stale.write_text(HASHED.format("sha256:SUPERSEDED"))
    os.utime(stale, (1, 1))
    record(vault, stale)

    result, fed = run_ingest(vault)

    assert "0 conversation(s) not yet in the manifest" in result.stdout
    assert "manifest covers everything in scope" in result.stdout
    assert fed == "", f"re-fed a conversation the manifest already records: {fed}"


CITING = """---
title: {title}
sources:
  - https://hostex.io/app/conversations/{conversation}
---

# {title}
"""


def page(vault: Path, category: str, slug: str, conversation: str) -> None:
    """A vault page citing one conversation, the way the extractor writes it."""
    (vault / category).mkdir(parents=True, exist_ok=True)
    (vault / category / f"{slug}.md").write_text(
        CITING.format(title=slug.replace("-", " "), conversation=conversation)
    )


def sources_of(vault: Path) -> dict:
    return json.loads((vault / ".manifest.json").read_text())["sources"]


def test_a_duplicate_archived_record_names_no_pages(vault, run_ingest):
    """The production failure, and the derivation's whole contract with it.

    wiki-ingest archives under a numeric suffix when the name is taken, so a
    conversation whose guest replies after it was archived once gets a second
    manifest record whose stem — `…-1` — is a filesystem disambiguation, not a
    conversation id. It carried the live record's six pages, and no page could
    ever cite it back, which turned the vault's provenance suite red for six
    pages at once and put `vault integrity FAILED` on the nightly digest.

    Everything is already recorded here, so the loop breaks before its first
    round: the run that has to repair the manifest is the one with nothing to
    ingest, which is the state every manifest this ships to is already in. Both
    page categories the extraction contract writes are present — scoping the
    derivation to `operations/` once left `people/key-people.md` unexamined
    while it carried a source set the manifest never corroborated — and the
    recorded form is `<category>/<slug>.md`, sorted, which is what the vault
    suite joins on.
    """
    archived = vault / "_raw" / "_archived"
    archived.mkdir(parents=True)
    duplicate = archived / "0-111-1.md"
    duplicate.write_text((vault / "_raw" / "hostex" / "0-111.md").read_text())
    record(vault, duplicate)
    page(vault, "operations", "thermostat-and-hvac", "0-111")
    page(vault, "people", "key-people", "0-111")

    result, fed = run_ingest(vault)

    assert "manifest covers everything in scope" in result.stdout
    assert fed == "", "nothing should have been fed"
    sources = sources_of(vault)
    assert sources["/opt/data/repo/vault/_raw/_archived/0-111-1.md"]["pages_produced"] == []
    assert sources["/opt/data/repo/vault/_raw/hostex/0-111.md"]["pages_produced"] == [
        "operations/thermostat-and-hvac.md",
        "people/key-people.md",
    ]


def test_a_url_in_the_body_is_not_provenance(vault, run_ingest):
    """`sources:` in frontmatter is the one copy — see the vault's AGENTS.md.

    A regeneration appended 38 citations to page bodies instead of inserting
    them into the mid-file YAML list. Counting those here would write the
    malformed page into the manifest as sound provenance, which is the shape of
    silent failure this repo blocks on.
    """
    page(vault, "operations", "thermostat-and-hvac", "0-111")
    body = vault / "operations" / "thermostat-and-hvac.md"
    body.write_text(
        body.read_text() + "\n- https://hostex.io/app/conversations/0-999\n"
    )
    # The body-cited conversation needs a record of its own, or the assertion
    # below cannot tell the two implementations apart: with nothing to write
    # into, reading the whole page looks exactly like reading the frontmatter.
    appended = vault / "_raw" / "hostex" / "0-999.md"
    appended.write_text(HASHED.format("sha256:CCC"))
    record(vault, appended)

    run_ingest(vault)

    sources = sources_of(vault)
    assert sources["/opt/data/repo/vault/_raw/hostex/0-999.md"]["pages_produced"] == []
    assert sources["/opt/data/repo/vault/_raw/hostex/0-111.md"]["pages_produced"] == [
        "operations/thermostat-and-hvac.md"
    ]


def test_a_manifest_it_does_not_change_is_left_byte_identical(vault, run_ingest):
    """Two writers share this file — the ingest turn, and now this script.

    A derivation that re-formatted on the way through would rewrite all 120KB
    every night over an unchanged corpus, and `git diff` over the vault is the
    surface the operator promotes from. Pinned as the shape rather than a
    literal, so it fails on compact JSON or a missing trailing newline without
    hard-coding either.
    """
    page(vault, "operations", "thermostat-and-hvac", "0-111")
    run_ingest(vault)
    once = (vault / ".manifest.json").read_text()
    assert once == json.dumps(json.loads(once), indent=2) + "\n"

    run_ingest(vault)

    assert (vault / ".manifest.json").read_text() == once
