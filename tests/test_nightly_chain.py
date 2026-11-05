"""What bin/nightly.sh delivers for each way a night can go.

The real script runs with its steps stubbed beside it. What the owners read is
the script's stdout, so every assertion is on that message and on which steps
ran with what arguments. A chain that notes a failure only to the log looks
exactly like a clean night from the chat.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _stub(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\necho \"$(basename \"$0\") $*\" >> \"$CALLS\"\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
def night(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copy(REPO / "bin" / "nightly.sh", bin_dir / "nightly.sh")
    _stub(bin_dir / "hostex-raw", 'exit "${FETCH_RC:-0}"')
    # A night's ingest rewrites the manifest; rollback must undo exactly that.
    _stub(bin_dir / "ingest-all", 'echo tonight >> "$1/.manifest.json"; exit "${INGEST_RC:-0}"')
    _stub(bin_dir / "wiki-provenance", 'echo "${PROVENANCE_SAYS:-}"; exit "${PROVENANCE_RC:-0}"')
    wiki = tmp_path / "wiki"
    (wiki / "projects" / "str" / "properties").mkdir(parents=True)
    (wiki / "projects" / "str" / "properties" / "a-house.md").write_text("---\ntitle: A house\n---\n")
    (wiki / "log.md").write_text("")
    _stub(bin_dir / "plow_relay.py",
          'case "$*" in *"tar -czf"*) [ "${COPY_RC:-0}" = 0 ] && tar -czf - -C "$WIKI_ON_MAC" . | base64;'
          ' exit "${COPY_RC:-0}";; *" index") exit "${INDEX_RC:-0}";; *" validate --writer str") exit "${VALIDATE_RC:-0}";;'
          ' *"snapshot --push"*) exit "${PUSH_RC:-0}";; *snapshot*) exit "${SNAPSHOT_RC:-0}";;'
          ' *restore*) exit "${RESTORE_RC:-0}";; esac')
    path_dir = tmp_path / "path"
    path_dir.mkdir()
    # The digest turn prints its prompt and the pages it was handed.
    _stub(path_dir / "hermes", 'printf "%s\\n" "${@: -1}"; find "$TMPDIR" -name "*.md" | sort')
    (tmp_path / "tmp").mkdir()
    vault = tmp_path / "home" / "repo" / "vault"
    (vault / "_raw" / "hostex").mkdir(parents=True)
    (vault / ".manifest.json").write_bytes(b'{"sources": {}}\n')
    (tmp_path / "home" / "logs").mkdir()
    calls = tmp_path / "calls"

    def run(**rcs):
        env = {**os.environ, "HERMES_HOME": str(tmp_path / "home"), "CALLS": str(calls),
               "WIKI_ON_MAC": str(wiki), "TMPDIR": str(tmp_path / "tmp"),
               "PATH": f"{path_dir}:{os.environ['PATH']}", **{k: str(v) for k, v in rcs.items()}}
        env.pop("VAULT", None)
        result = subprocess.run([str(bin_dir / "nightly.sh")], capture_output=True, text=True,
                                env=env, timeout=30)
        return result, calls.read_text().splitlines() if calls.exists() else []

    return run, vault, tmp_path / "tmp"


FRONT = [
    "hostex-raw --vault {vault}",
    "ingest-all {vault} ~/Plow/wiki",
    "plow_relay.py run --write ~/Plow/wiki -- wiki index",
    "wiki-provenance {vault} ~/Plow/wiki",
    "plow_relay.py run -- wiki validate --writer str",
]
COMMIT = "plow_relay.py run --write ~/Plow/wiki --write ~/Plow/wiki.git -- wiki snapshot --author str"
COPY = ("plow_relay.py run -- /bin/sh -c cd ~/Plow/wiki && tar -czf - projects/str/properties"
        " projects/str/operations log.md | base64")
SNAPSHOT = [COMMIT, "plow_relay.py run --write ~/Plow/wiki --write ~/Plow/wiki.git --network --"
            " wiki snapshot --push --author str"]
ROLLBACK = ["plow_relay.py run --write ~/Plow/wiki --write ~/Plow/wiki.git -- wiki restore --writer str",
            "plow_relay.py run --write ~/Plow/wiki -- wiki index"]

NIGHTS = [
    ("a clean night", {}, "'ok'", SNAPSHOT),
    ("a push that failed", {"PUSH_RC": 1}, "wiki push FAILED; see ", SNAPSHOT),
    ("a snapshot that could not commit", {"SNAPSHOT_RC": 2},
     "rolled back: wiki snapshot could not run: the owner's Mac did not answer; see ",
     [COMMIT] + ROLLBACK),
    ("a failed ingest", {"INGEST_RC": 1}, "rolled back: ingest FAILED; see ", ROLLBACK),
    # Every finished round is recorded and checked, so a budget stop is
    # snapshotted and the backlog resumes tomorrow rather than being undone.
    ("an ingest that spent its time budget", {"INGEST_RC": 3},
     "'ingest stopped at its time budget, the rest continues tomorrow; ok'", SNAPSHOT),
    ("invalid pages", {"VALIDATE_RC": 1}, "rolled back: wiki validation FAILED; see ", ROLLBACK),
    ("an index that refused, here and again after restore", {"INDEX_RC": 1},
     "rolled back: wiki index FAILED; rollback incomplete: wiki index FAILED; see ", ROLLBACK),
    ("a provenance defect", {"PROVENANCE_RC": 1}, "rolled back: provenance FAILED; see ", ROLLBACK),
    ("a Mac that did not answer validation", {"VALIDATE_RC": 2},
     "rolled back: wiki validation could not run: the owner's Mac did not answer; see ", ROLLBACK),
    ("a gatekeeper that refused validation", {"VALIDATE_RC": 4},
     "rolled back: wiki validation was refused by the gatekeeper on the owner's Mac; see ", ROLLBACK),
    ("a restore that failed", {"VALIDATE_RC": 1, "RESTORE_RC": 2},
     "rolled back: wiki validation FAILED; rollback incomplete: wiki restore could not run", ROLLBACK),
]


@pytest.mark.parametrize(("case", "rcs", "status", "tail"), NIGHTS, ids=[n[0] for n in NIGHTS])
def test_only_a_night_that_passes_every_check_reaches_history(night, case, rcs, status, tail):
    """Any failure from ingest onward restores str's pages and the manifest
    instead of snapshotting them. A failed night exits non-zero, so the
    scheduler records it failed and alerts with the line saying which, in
    place of a digest; a night that passed sends the digest."""
    run, vault, _ = night
    manifest = (vault / ".manifest.json").read_bytes()
    result, calls = run(**rcs)
    failed = not status.endswith("ok'")
    assert result.returncode == int(failed), result.stderr
    assert status in result.stdout, result.stdout
    if failed:
        assert result.stdout.startswith("nightly: Wiki nightly FAILED: "), result.stdout
        assert not result.stderr, result.stderr  # nothing ahead of the failure in the alert
    else:
        assert calls.pop().startswith("hermes chat -Q -q Use the wiki-digest skill")
        assert calls.pop() == COPY
    assert calls == [c.format(vault=vault) for c in FRONT] + tail
    # Only once the pages are back: a manifest that forgot pages still on the
    # wiki re-ingests them into those pages.
    rolled_back = (vault / ".manifest.json").read_bytes() == manifest
    assert rolled_back == (tail[-2:] == ROLLBACK and "RESTORE_RC" not in rcs)


def test_what_a_check_found_is_in_the_log_the_message_names_not_the_message(night, tmp_path):
    """The scheduler's failure alert is one line, so the log is the only place
    the finding survives, and the message is what has to say where it is."""
    run, _, _ = night
    log = tmp_path / "home" / "logs" / "wiki-nightly.log"
    result, _ = run(PROVENANCE_RC=1, PROVENANCE_SAYS="page-a: cites 20-abc, which the manifest never recorded")
    assert result.returncode == 1
    assert f"provenance FAILED; see {log}" in result.stdout
    assert "cites 20-abc" not in result.stdout
    assert "cites 20-abc" in log.read_text()


def test_a_page_written_into_staging_is_reported(night):
    run, vault, _ = night
    (vault / "operations").mkdir()
    (vault / "operations" / "lake-sauna.md").write_text("---\ntitle: T\n---\n")
    result, _ = run()
    assert "ingest wrote pages into local staging instead of the wiki" in result.stdout


def test_a_failed_fetch_says_so_and_stops(night):
    run, _, _ = night
    result, calls = run(FETCH_RC=1)
    assert result.returncode == 1
    assert "Wiki nightly FAILED at fetch" in result.stdout
    assert [c.split()[0] for c in calls] == ["hostex-raw"]


def test_a_rollback_always_lands_on_the_manifest_of_the_last_snapshot(night):
    """Pages and manifest go back as a pair. A night whose restore failed keeps
    its manifest (its pages are still on the wiki); the next failing night whose
    restore works returns both to the last snapshot -- not to that night's start,
    which would leave the first night's records describing pages gone from the wiki."""
    run, vault, _ = night
    manifest = vault / ".manifest.json"
    snapshotted = manifest.read_bytes()
    run(VALIDATE_RC=1, RESTORE_RC=2)
    assert manifest.read_bytes() == snapshotted + b"tonight\n"
    run(VALIDATE_RC=1)
    assert manifest.read_bytes() == snapshotted
    run()
    clean = manifest.read_bytes()
    run(PROVENANCE_RC=1)
    assert manifest.read_bytes() == clean


def test_the_digest_reads_a_local_copy_of_only_strs_pages_and_writes_nothing(night):
    """The wiki is the owner's common one: other writers' roots -- an investor
    pipeline among them -- sit beside str's, and the skill digests whatever
    vault it is pointed at and then appends to log.md. Every latch call costs
    the Mac's gatekeeper ~12s, so the turn gets str's pages copied locally in
    one call, never the wiki itself, and the copy is gone after the run."""
    run, _, tmp = night
    result, _ = run()
    pages = sorted(tmp.rglob("*.md"))
    assert not pages, pages
    copy = next(line for line in result.stdout.splitlines() if line.endswith("/projects/str/properties/a-house.md"))
    vault = copy.removesuffix("/projects/str/properties/a-house.md")
    assert vault.startswith(str(tmp)), vault
    for root in ("/projects/str/properties/", "/projects/str/operations/"):
        assert f"{vault}{root}" in result.stdout, result.stdout
    assert "~/Plow/wiki" not in result.stdout and "plow_relay" not in result.stdout, result.stdout
    assert "Do not read, list or mention any other page" in result.stdout, result.stdout
    assert "skip the skill's log.md append" in result.stdout, result.stdout


def test_a_copy_that_failed_says_so_instead_of_a_digest(night):
    run, _, _ = night
    result, calls = run(COPY_RC=2)
    assert result.returncode == 1
    assert result.stdout == "nightly: Wiki nightly: could not copy str's pages for the digest\n"
    assert not any(c.startswith("hermes") for c in calls), calls
