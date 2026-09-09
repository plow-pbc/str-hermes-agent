"""The SOUL reaches $HERMES_HOME only through this script, and only at deploy.

What it publishes is the tracked persona VERBATIM. That is the whole design:
the base image treats SOUL.md as provisioned identity -- plow-init chowns it
root:root inside a root-owned sticky home at every container start -- so the one
thing that must never live in it is content that changes nightly. The vault
index does; it stays in the vault, and the persona names the path.

The home is a named volume, so there is no host path to write and the script
has exactly one path: an escalated `compose exec` into the agent's container.
These tests drive that path for real. The `docker` stub below forwards the
script's own inner shell into a throwaway container off this repo's image, with
a scratch directory bound at $HERMES_HOME -- so `chown root:root`, the mode and
the rename all execute as root against a real filesystem, and the host reads
back what actually landed. Stubbing the transport and running the payload is
what keeps this a behaviour test rather than a mock-call assertion.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT / "scripts" / "publish-soul"
PERSONA = ROOT / "runtime" / "SOUL.md"


IMAGE = "sams-str-hermes-agent:local"
DOCKER = shutil.which("docker")

# Stands in for `docker compose exec -u root -T hermes sh -c <script>`, which is
# the script's only call. The inner script is always the last argument, and it
# is handed to a real container with $HERMES_HOME bound to the scratch home, so
# what runs is the script's own payload rather than a paraphrase of it. The real
# docker is called by absolute path: this stub IS `docker` on PATH.
STUB = """#!/usr/bin/env bash
set -eu
for arg in "$@"; do inner=$arg; done
exec {docker} run --rm -i -v "$SCRATCH_HOME:/tmp/h" -e HERMES_HOME=/tmp/h \
  --entrypoint sh {image} -c "$inner"
"""


def _stub_path(tmp_path: Path, home: Path) -> tuple[Path, dict[str, str]]:
    assert DOCKER, "docker is required for these tests"
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir(exist_ok=True)
    stub = stub_bin / "docker"
    stub.write_text(STUB.format(docker=DOCKER, image=IMAGE))
    stub.chmod(0o755)
    return stub_bin, {"SCRATCH_HOME": str(home)}


def _run(home: Path, script: Path | None = None, **extra: str) -> subprocess.CompletedProcess:
    stub_bin, stub_env = _stub_path(home.parent, home)
    env = {
        **os.environ,
        "PATH": f"{stub_bin}:{os.environ['PATH']}",
        **stub_env,
        **extra,
    }
    return subprocess.run([str(script or PUBLISH)], env=env, text=True, capture_output=True)


def test_publishes_the_persona_verbatim_at_plow_inits_mode(tmp_path: Path) -> None:
    """Byte-identical to the tracked file, at the mode the boot hardening sets."""
    home = tmp_path / "home"
    home.mkdir()
    result = _run(home)
    assert result.returncode == 0, result.stderr
    soul = home / "SOUL.md"
    assert soul.read_bytes() == PERSONA.read_bytes()
    # 0644 is what harden_home() sets at every container start, so publishing at
    # that mode makes the hardening a no-op rather than a change it undoes.
    assert stat.S_IMODE(soul.stat().st_mode) == 0o644


def test_carries_the_index_path_and_not_the_index(tmp_path: Path) -> None:
    """The regression this whole change exists to prevent.

    Pasting the vault index in is what forced a root-escalation-and-scheduling
    pipeline through a file the platform freezes. The persona sends the agent
    to the file instead; if the published SOUL ever grows the corpus itself,
    that pipeline is back.

    This is the sole behavioural owner of this PR's whole point. A
    byte-equality check against runtime/SOUL.md (above) cannot catch a persona
    edit that drops the pointer while leaving everything else untouched --
    equality would stay green on the edited file either way. Only an assertion
    against the published artifact's actual content, like this one, catches
    that. Do not delete this in favor of the byte-equality check.
    """
    home = tmp_path / "home"
    home.mkdir()
    assert _run(home).returncode == 0
    body = (home / "SOUL.md").read_text()
    assert "index.md" in body
    assert "## Properties" not in body, "the vault index was composed into the SOUL"


def test_second_run_is_quiet_and_rewrites_nothing(tmp_path: Path) -> None:
    """Every deploy calls this; only the rare one that edits the persona writes."""
    home = tmp_path / "home"
    home.mkdir()
    assert _run(home).returncode == 0
    mtime = (home / "SOUL.md").stat().st_mtime_ns
    result = _run(home)
    assert result.returncode == 0, result.stderr
    assert "already current" in result.stdout
    assert (home / "SOUL.md").stat().st_mtime_ns == mtime, "rewrote an unchanged SOUL"


def test_refuses_an_empty_persona_and_leaves_the_live_soul_alone(tmp_path: Path) -> None:
    """An empty identity reads like a healthy deploy, so it must fail loudly.

    Exercised through a repo copy rather than by emptying the tracked file:
    the persona's own path is what the script derives from its location.
    """
    home = tmp_path / "home"
    home.mkdir()
    assert _run(home).returncode == 0
    published = (home / "SOUL.md").read_text()

    fake_repo = tmp_path / "repo"
    (fake_repo / "scripts").mkdir(parents=True)
    (fake_repo / "runtime").mkdir()
    (fake_repo / "runtime" / "SOUL.md").write_text("")
    (fake_repo / "scripts" / "publish-soul").write_bytes(PUBLISH.read_bytes())
    (fake_repo / "scripts" / "publish-soul").chmod(0o755)

    result = _run(home, script=fake_repo / "scripts" / "publish-soul")
    assert result.returncode != 0
    assert (home / "SOUL.md").read_text() == published, "clobbered the live SOUL"
