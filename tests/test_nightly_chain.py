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
    _stub(bin_dir / "plow_relay.py",
          'case "$*" in *" index") exit "${INDEX_RC:-0}";; *" validate --writer str") exit "${VALIDATE_RC:-0}";;'
          ' *"snapshot --push"*) exit "${PUSH_RC:-0}";; *snapshot*) exit "${SNAPSHOT_RC:-0}";;'
          ' *restore*) exit "${RESTORE_RC:-0}";; esac')
    path_dir = tmp_path / "path"
    path_dir.mkdir()
    _stub(path_dir / "hermes", 'printf "%s\\n" "${@: -1}"')  # the digest turn prints its prompt
    vault = tmp_path / "home" / "repo" / "vault"
    (vault / "_raw" / "hostex").mkdir(parents=True)
    (vault / ".manifest.json").write_bytes(b'{"sources": {}}\n')
    (tmp_path / "home" / "logs").mkdir()
    calls = tmp_path / "calls"

    def run(**rcs):
        env = {**os.environ, "HERMES_HOME": str(tmp_path / "home"), "CALLS": str(calls),
               "PATH": f"{path_dir}:{os.environ['PATH']}", **{k: str(v) for k, v in rcs.items()}}
        env.pop("VAULT", None)
        result = subprocess.run([str(bin_dir / "nightly.sh")], capture_output=True, text=True,
                                env=env, timeout=30)
        return result, calls.read_text().splitlines() if calls.exists() else []

    return run, vault


FRONT = [
    "hostex-raw --vault {vault}",
    "ingest-all {vault} ~/Plow/wiki",
    "plow_relay.py run --write ~/Plow/wiki -- wiki index",
    "wiki-provenance {vault} ~/Plow/wiki",
    "plow_relay.py run -- wiki validate --writer str",
]
COMMIT = "plow_relay.py run --write ~/Plow/wiki --write ~/Plow/wiki.git -- wiki snapshot --author str"
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
    ("invalid pages", {"VALIDATE_RC": 1}, "rolled back: wiki validation FAILED; see ", ROLLBACK),
    ("an index that refused, here and again after restore", {"INDEX_RC": 1},
     "rolled back: wiki index FAILED; rollback incomplete: wiki index FAILED; see ", ROLLBACK),
    ("a provenance defect", {"PROVENANCE_RC": 1}, "rolled back: provenance FAILED; see ", ROLLBACK),
    ("a Mac that did not answer validation", {"VALIDATE_RC": 2},
     "rolled back: wiki validation could not run: the owner's Mac did not answer; see ", ROLLBACK),
    ("a restore that failed", {"VALIDATE_RC": 1, "RESTORE_RC": 2},
     "rolled back: wiki validation FAILED; rollback incomplete: wiki restore could not run", ROLLBACK),
]


@pytest.mark.parametrize(("case", "rcs", "status", "tail"), NIGHTS, ids=[n[0] for n in NIGHTS])
def test_only_a_night_that_passes_every_check_reaches_history(night, case, rcs, status, tail):
    """Any failure from ingest onward restores str's pages and the manifest
    instead of snapshotting them, and the digest -- the liveness signal --
    still goes out saying which."""
    run, vault = night
    manifest = (vault / ".manifest.json").read_bytes()
    result, calls = run(**rcs)
    assert result.returncode == 0, result.stderr
    assert status in result.stdout, result.stdout
    assert calls[-1].startswith("hermes chat -Q -q Use the wiki-digest skill")
    assert calls[:-1] == [c.format(vault=vault) for c in FRONT] + tail
    # Only once the pages are back: a manifest that forgot pages still on the
    # wiki re-ingests them into those pages.
    rolled_back = (vault / ".manifest.json").read_bytes() == manifest
    assert rolled_back == (tail[-2:] == ROLLBACK and "RESTORE_RC" not in rcs)


def test_what_a_check_found_is_in_the_log_the_message_names_not_the_message(night, tmp_path):
    """The scheduler keeps a job's stderr only when it exits non-zero, and a
    night that notes a failed check exits 0 -- so the log is the only place
    the finding survives, and the message is what has to say where it is."""
    run, _ = night
    log = tmp_path / "home" / "logs" / "wiki-nightly.log"
    result, _ = run(PROVENANCE_RC=1, PROVENANCE_SAYS="page-a: cites 20-abc, which the manifest never recorded")
    assert result.returncode == 0, result.stderr
    assert f"provenance FAILED; see {log}" in result.stdout
    assert "cites 20-abc" not in result.stdout
    assert "cites 20-abc" in log.read_text()


def test_a_page_written_into_staging_is_reported(night):
    run, vault = night
    (vault / "operations").mkdir()
    (vault / "operations" / "lake-sauna.md").write_text("---\ntitle: T\n---\n")
    result, _ = run()
    assert "ingest wrote pages into local staging instead of the wiki" in result.stdout


def test_a_failed_fetch_says_so_and_stops(night):
    run, _ = night
    result, calls = run(FETCH_RC=1)
    assert result.returncode == 1
    assert "Wiki nightly FAILED at fetch" in result.stdout
    assert [c.split()[0] for c in calls] == ["hostex-raw"]


def test_a_rollback_always_lands_on_the_manifest_of_the_last_snapshot(night):
    """Pages and manifest go back as a pair. A night whose restore failed keeps
    its manifest (its pages are still on the wiki); the next failing night whose
    restore works returns both to the last snapshot -- not to that night's start,
    which would leave the first night's records describing pages gone from the wiki."""
    run, vault = night
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


def test_the_digest_covers_only_strs_pages_and_writes_nothing(night):
    """The wiki is the owner's common one: other writers' roots -- an investor
    pipeline among them -- sit beside str's, and the skill digests whatever it
    is pointed at and then appends to log.md. The owners' digest is str's
    pages, and this turn only reads."""
    run, _ = night
    result, _ = run()
    prompt = result.stdout
    for root in ("~/Plow/wiki/projects/str/properties/", "~/Plow/wiki/projects/str/operations/"):
        assert root in prompt, prompt
    assert "Do not read, list or mention any other page" in prompt, prompt
    assert "skip the skill's log.md append" in prompt, prompt
