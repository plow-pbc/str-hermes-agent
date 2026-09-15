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
    _stub(bin_dir / "ingest-all", 'exit 0')
    _stub(bin_dir / "wiki-provenance", 'exit "${PROVENANCE_RC:-0}"')
    _stub(bin_dir / "plow_relay.py",
          'case "$*" in *" index") exit "${INDEX_RC:-0}";; *" validate") exit "${VALIDATE_RC:-0}";;'
          ' *snapshot*) exit "${SNAPSHOT_RC:-0}";; esac')
    path_dir = tmp_path / "path"
    path_dir.mkdir()
    _stub(path_dir / "hermes", 'printf "%s\\n" "$3"')  # the digest turn prints its prompt
    vault = tmp_path / "home" / "repo" / "vault"
    (vault / "_raw" / "hostex").mkdir(parents=True)
    calls = tmp_path / "calls"

    def run(**rcs):
        env = {**os.environ, "HERMES_HOME": str(tmp_path / "home"), "CALLS": str(calls),
               "PATH": f"{path_dir}:{os.environ['PATH']}", **{k: str(v) for k, v in rcs.items()}}
        env.pop("VAULT", None)
        env.pop("STR_WIKI", None)
        result = subprocess.run([str(bin_dir / "nightly.sh")], capture_output=True, text=True,
                                env=env, timeout=30)
        return result, calls.read_text().splitlines() if calls.exists() else []

    return run, vault


NIGHTS = [
    ("a clean night", {}, "'ok'"),
    ("invalid pages", {"VALIDATE_RC": 1}, "wiki validation FAILED; see the cron log"),
    ("an index that refused", {"INDEX_RC": 1}, "wiki index FAILED; see the cron log"),
    ("a provenance defect", {"PROVENANCE_RC": 1}, "provenance FAILED; see the cron log"),
    ("a Mac that did not answer the snapshot", {"SNAPSHOT_RC": 2},
     "wiki snapshot could not run: the owner's Mac did not answer; see the cron log"),
]


@pytest.mark.parametrize(("case", "rcs", "status"), NIGHTS, ids=[n[0] for n in NIGHTS])
def test_the_digest_carries_what_every_post_ingest_step_found(night, case, rcs, status):
    run, vault = night
    result, calls = run(**rcs)
    assert result.returncode == 0, result.stderr
    assert status in result.stdout, result.stdout
    assert calls[-1].startswith("hermes chat -q Use the wiki-digest skill")
    assert calls[:-1] == [
        f"hostex-raw --vault {vault}",
        f"ingest-all {vault} ~/Plow/wiki",
        "plow_relay.py run --write ~/Plow/wiki -- wiki --wiki ~/Plow/wiki index",
        "plow_relay.py run -- wiki --wiki ~/Plow/wiki validate",
        f"wiki-provenance {vault} ~/Plow/wiki",
        "plow_relay.py run --write ~/Plow/wiki --write ~/Plow/wiki.git -- wiki --wiki ~/Plow/wiki snapshot --author str",
    ]


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
