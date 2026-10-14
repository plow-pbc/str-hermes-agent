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
damage = pathlib.Path(os.environ["DAMAGE"])
if "repair these problems" in prompt:
    if os.environ.get("REPAIRS") == "yes":
        damage.unlink()
else:
    damage.touch()  # an ingest turn that leaves a mistyped citation behind

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

    def run(repairs: bool, already_damaged: bool = False):
        if already_damaged:
            damage.touch()
        env = {**os.environ, "HERMES_HOME": str(home), "VAULT_DIR": str(vault),
               "PROMPTS": str(prompts), "DAMAGE": str(damage),
               "REPAIRS": "yes" if repairs else "no", "PATH": f"{path_dir}:{os.environ['PATH']}"}
        result = subprocess.run([str(bin_dir / "ingest-all"), str(vault)], capture_output=True,
                                text=True, env=env, timeout=60)
        return result, prompts.read_text().split("\n---\n")[:-1]

    return run


@pytest.mark.parametrize(("repairs", "rc", "rounds", "diagnostic"), [
    pytest.param(True, 0, 2, "manifest covers everything in scope", id="repaired"),
    pytest.param(False, 1, 3, "FATAL", id="never repaired"),
])
def test_a_damaged_page_is_named_to_every_later_round(ingest, repairs, rc, rounds, diagnostic):
    result, prompts = ingest(repairs=repairs)
    assert result.returncode == rc, result.stdout + result.stderr
    assert len(prompts) == rounds
    assert "repair" not in prompts[0] and "_raw/hostex/20-80f4.md" in prompts[0]
    for prompt in prompts[1:]:
        assert prompt.startswith("Before anything else, repair these problems")
        assert "hvac: cites 20-8044, which the manifest never recorded" in prompt
        assert "There are no new files to process this round." in prompt
    assert diagnostic in result.stdout + result.stderr


def test_damage_already_on_the_wiki_gets_a_repair_round_with_nothing_new(ingest, tmp_path):
    """Damage from before tonight -- a night that predates these checks -- is
    repaired too, not only damage tonight's turn caused."""
    vault = tmp_path / "home" / "repo" / "vault"
    (vault / "_raw" / "hostex" / "20-80f4.md").unlink()
    result, prompts = ingest(repairs=True, already_damaged=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(prompts) == 1
    assert prompts[0].startswith("Before anything else, repair these problems")
