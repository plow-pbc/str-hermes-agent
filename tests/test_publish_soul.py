"""The SOUL reaches $HERMES_HOME only through this script.

What it publishes is the tracked persona VERBATIM. That is the whole design:
the base image treats SOUL.md as provisioned identity -- plow-init chowns it
root:root inside a root-owned sticky home at every container start -- so the one
thing that must never live in it is content that changes nightly. The vault
index does; it stays in the vault, and the persona names the path.

The home is a named volume, so there is no host path to write and the script has
exactly one path: an escalated `compose exec` into the agent's container. These
tests drive that path for real -- the `docker` stub forwards the script's own
inner shell into a throwaway container, so `chown root:root`, the mode and the
rename all execute as root against a real filesystem.

**Everything lives daemon-side.** The scratch home is a named volume and every
assertion is read by a probe container, never by the host. An earlier version
bound a host directory at $HERMES_HOME, which is only correct when pytest and
the Docker daemon share a filesystem: under docker-in-docker -- what the review
runner uses -- a client-side bind arrives EMPTY, the container writes into its
own private mount, and the host reads back nothing. That failed loudly here, but
the same shape is what makes a *guard* test pass while inspecting nothing, so
the rule is that no test in this file may name a host path inside a container.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT / "scripts" / "publish-soul"
PERSONA = ROOT / "runtime" / "SOUL.md"

IMAGE = "sams-str-hermes-agent:local"
DOCKER = shutil.which("docker")

# Stands in for `docker compose exec -u root -T hermes sh -c <script>`, the
# script's only call. The inner script is always the last argument, and it is
# handed to a real container with the scratch VOLUME at $HERMES_HOME. The real
# docker is called by absolute path: this stub IS `docker` on PATH.
STUB = """#!/usr/bin/env bash
set -eu
for arg in "$@"; do inner=$arg; done
exec {docker} run --rm -i -v "$SCRATCH_VOL:/tmp/h" -e HERMES_HOME=/tmp/h \
  --entrypoint sh {image} -c "$inner"
"""


class Home:
    """A scratch $HERMES_HOME that exists only as a Docker volume."""

    def __init__(self, tmp_path: Path) -> None:
        assert DOCKER, "docker is required for these tests"
        self.name = f"str-publish-soul-{uuid.uuid4().hex[:12]}"
        subprocess.run([DOCKER, "volume", "create", self.name],
                       check=True, capture_output=True)
        self.stub_bin = tmp_path / "stub-bin"
        self.stub_bin.mkdir(exist_ok=True)
        stub = self.stub_bin / "docker"
        stub.write_text(STUB.format(docker=DOCKER, image=IMAGE))
        stub.chmod(0o755)

    def remove(self) -> None:
        subprocess.run([DOCKER, "volume", "rm", "-f", self.name],
                       check=False, capture_output=True)

    def run(self, script: Path | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ,
               "PATH": f"{self.stub_bin}:{os.environ['PATH']}",
               "SCRATCH_VOL": self.name}
        return subprocess.run([str(script or PUBLISH)], env=env,
                              text=True, capture_output=True)

    def probe(self, shell: str) -> str:
        """Answer a question about the volume from inside a container."""
        return subprocess.run(
            [DOCKER, "run", "--rm", "-v", f"{self.name}:/tmp/h",
             "--entrypoint", "sh", IMAGE, "-c", shell],
            check=True, capture_output=True, text=True).stdout.strip()


def home(tmp_path: Path) -> Home:
    return Home(tmp_path)


def test_publishes_the_persona_verbatim_at_plow_inits_mode(tmp_path: Path) -> None:
    """Byte-identical to the tracked file, at the mode the boot hardening sets."""
    h = home(tmp_path)
    try:
        result = h.run()
        assert result.returncode == 0, result.stderr
        # sha and mode read together in one probe: two containers could not see
        # the same instant, and the mode is meaningless about a different file.
        got = h.probe('sha256sum /tmp/h/SOUL.md | cut -d" " -f1; stat -c %a /tmp/h/SOUL.md')
        digest, mode = got.splitlines()
        expected = subprocess.run(["sha256sum", str(PERSONA)], check=True,
                                  capture_output=True, text=True).stdout.split()[0]
        assert digest == expected
        # 0644 is what harden_home() sets at every container start, so publishing
        # at that mode makes the hardening a no-op rather than a change it undoes.
        assert mode == "644"
    finally:
        h.remove()


def test_carries_the_index_path_and_not_the_index(tmp_path: Path) -> None:
    """The regression this whole change exists to prevent.

    Pasting the vault index in is what forced a root-escalation-and-scheduling
    pipeline through a file the platform freezes. The persona sends the agent to
    the file instead; if the published SOUL ever grows the corpus itself, that
    pipeline is back.

    This is the sole behavioural owner of that point. A byte-equality check
    against runtime/SOUL.md (above) cannot catch a persona edit that drops the
    pointer while leaving everything else untouched -- equality would stay green
    on the edited file either way. Only an assertion against the published
    artifact's actual content, like this one, catches that. Do not delete this in
    favor of the byte-equality check.
    """
    h = home(tmp_path)
    try:
        assert h.run().returncode == 0
        body = h.probe("cat /tmp/h/SOUL.md")
        assert "index.md" in body
        assert "## Properties" not in body, "the vault index was composed into the SOUL"
    finally:
        h.remove()


def test_second_run_is_quiet_and_rewrites_nothing(tmp_path: Path) -> None:
    """Only the rare run that edits the persona should write.

    mtime read with %y, not %Y: seconds-granularity cannot tell a rewrite from a
    no-op when both runs land in the same second, which is the normal case.
    """
    h = home(tmp_path)
    try:
        assert h.run().returncode == 0
        before = h.probe("stat -c %y /tmp/h/SOUL.md")
        result = h.run()
        assert result.returncode == 0, result.stderr
        assert "already current" in result.stdout
        assert h.probe("stat -c %y /tmp/h/SOUL.md") == before, "rewrote an unchanged SOUL"
    finally:
        h.remove()


def test_refuses_an_empty_persona_and_leaves_the_live_soul_alone(tmp_path: Path) -> None:
    """An empty identity reads like a healthy deploy, so it must fail loudly.

    Exercised through a repo copy rather than by emptying the tracked file: the
    persona's own path is what the script derives from its location.
    """
    h = home(tmp_path)
    try:
        assert h.run().returncode == 0
        published = h.probe("cat /tmp/h/SOUL.md")

        fake_repo = tmp_path / "repo"
        (fake_repo / "scripts").mkdir(parents=True)
        (fake_repo / "runtime").mkdir()
        (fake_repo / "runtime" / "SOUL.md").write_text("")
        (fake_repo / "scripts" / "publish-soul").write_bytes(PUBLISH.read_bytes())
        (fake_repo / "scripts" / "publish-soul").chmod(0o755)

        assert h.run(script=fake_repo / "scripts" / "publish-soul").returncode != 0
        assert h.probe("cat /tmp/h/SOUL.md") == published, "clobbered the live SOUL"
    finally:
        h.remove()
