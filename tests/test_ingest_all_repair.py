"""What `bin/ingest-all` does when a round leaves str's pages failing the wiki's checks.

The checks run after every round, so a page the model damaged is named to the very
next turn, which still has the source in hand. A damage the model never repairs
must fail the run, so the nightly rolls the night back rather than snapshotting it.
Runs the container branch with the turn and both checks stubbed beside the script.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# The turn: record every fed file in the manifest the way wiki-ingest does, and
# repair only when told to and allowed to ($REPAIRS).
TURN = r'''#!/usr/bin/env python3
import hashlib, json, os, pathlib, re, sys
prompt = sys.argv[-1]
pathlib.Path(os.environ["PROMPTS"]).open("a").write(prompt + "\n---\n")
vault = pathlib.Path(os.environ["VAULT_DIR"])
mf = vault / ".manifest.json"
data = json.loads(mf.read_text())
for rel in re.findall(r"_raw/hostex/[\w-]+\.md", prompt):
    data["sources"]["/var/lib/hermes/repo/vault/" + rel] = {
        "content_hash": "sha256:" + hashlib.sha256((vault / rel).read_bytes()).hexdigest()}
mf.write_text(json.dumps(data))
if "repair these problems" in prompt and os.environ.get("REPAIRS") == "yes":
    pathlib.Path(os.environ["DAMAGE"]).unlink()
'''


@pytest.fixture
def ingest(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copy(REPO / "bin" / "ingest-all", bin_dir / "ingest-all")
    damage = tmp_path / "damage"
    for name, body in {
        "plow_relay.py": "exit 0",  # index and validate pass; provenance carries the damage
        "wiki-provenance":
            f'[ -e {damage} ] && {{ echo "projects/str/operations/hvac: cites 20-8044, '
            'which the manifest never recorded"; exit 1; }; exit 0',
    }.items():
        (bin_dir / name).write_text(f"#!/usr/bin/env bash\n{body}\n")
        (bin_dir / name).chmod(0o755)
    path_dir = tmp_path / "path"
    path_dir.mkdir()
    (path_dir / "hermes").write_text(TURN)
    (path_dir / "hermes").chmod(0o755)
    home = tmp_path / "home"
    vault = home / "repo" / "vault"
    (vault / "_raw" / "hostex").mkdir(parents=True)
    (vault / "_raw" / "hostex" / "20-80f4.md").write_text("---\nproperty: lake\n---\nhi\n")
    (vault / ".manifest.json").write_text('{"sources": {}}')
    prompts = tmp_path / "prompts"

    def run(repairs: bool):
        damage.touch()  # the first round's turn leaves a mistyped citation behind
        env = {**os.environ, "HERMES_HOME": str(home), "VAULT_DIR": str(vault),
               "PROMPTS": str(prompts), "DAMAGE": str(damage),
               "REPAIRS": "yes" if repairs else "no", "PATH": f"{path_dir}:{os.environ['PATH']}"}
        result = subprocess.run([str(bin_dir / "ingest-all"), str(vault)], capture_output=True,
                                text=True, env=env, timeout=60)
        return result, prompts.read_text().split("\n---\n")[:-1]

    return run


def test_a_damaged_page_is_named_to_the_next_round_and_repaired(ingest):
    result, prompts = ingest(repairs=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(prompts) == 2
    assert "repair" not in prompts[0] and "_raw/hostex/20-80f4.md" in prompts[0]
    assert prompts[1].startswith("Before anything else, repair these problems")
    assert "hvac: cites 20-8044, which the manifest never recorded" in prompts[1]
    assert "There are no new files to process this round." in prompts[1]
    assert "manifest covers everything in scope" in result.stdout


def test_damage_the_turn_never_repairs_fails_the_run(ingest):
    result, prompts = ingest(repairs=False)
    assert result.returncode == 1
    assert "FATAL" in result.stderr
    assert all("hvac: cites 20-8044" in p for p in prompts[1:]) and len(prompts) >= 2
