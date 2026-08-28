"""The deploy gate, against real git state.

Behavioural rather than structural: what matters is which trees it lets
through. The vault used to be the runtime's writable area inside the
checkout, and nothing committed it, so it was exempt. It now lives outside
the checkout entirely — there's no exemption left, and any dirty path, by
any name, stops the deploy.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "check-deploy-clean.sh"


def scratch_checkout(tmp_path: Path) -> Path:
    """A repo shaped like a deployed one: one commit, no vault in it."""
    (tmp_path / "outside.md").write_text("# Outside\n")
    # hooksPath off: this machine sets core.hooksPath globally, so the
    # fixture's seed commit runs the operator's hooks and fails on whatever
    # state they are in — which reads as eight failures in this file rather
    # than as a broken hook.
    run = lambda *a: subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "core.hooksPath=/dev/null", *a],
        check=True, capture_output=True)
    run("init", "-q")
    run("add", "-A")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed")
    return tmp_path


def gate(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(GATE)], cwd=cwd, capture_output=True, text=True)


@pytest.mark.parametrize("dirty", [
    "vault/log.md",        # was exempt; the checkout has no vault now
    "outside.md",
    "bin/dropped.py",     # ./bin is bind-mounted as the cron script dir
])
def test_any_dirty_path_stops_the_deploy(tmp_path, dirty):
    """The exemption existed because the runtime wrote into the checkout.

    It no longer does — the vault is outside the checkout entirely — so a dirty
    path here means someone edited production directly, whatever its name.
    """
    repo = scratch_checkout(tmp_path)
    target = repo / dirty
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# edited in production\n")
    result = gate(repo)
    assert result.returncode == 1
    assert "STOP" in result.stdout
    assert "DEPLOYABLE" not in result.stdout


def test_a_clean_checkout_is_deployable(tmp_path):
    result = gate(scratch_checkout(tmp_path))
    assert result.returncode == 0
    assert "DEPLOYABLE" in result.stdout


def test_it_names_what_stopped_it(tmp_path):
    repo = scratch_checkout(tmp_path)
    (repo / "outside.md").write_text("edited\n")
    result = gate(repo)
    output = result.stdout + result.stderr
    assert "outside.md" in output
    assert "STOP" in output


def test_outside_a_repository_is_a_stop(tmp_path):
    """Fail closed: git unable to answer is not permission to deploy."""
    assert gate(tmp_path).returncode != 0


def test_git_status_failing_is_a_stop_even_though_rev_parse_survives_it(tmp_path):
    """rev-parse --show-toplevel doesn't read the index; git status does.

    A corrupt index is exactly the case that used to slip through: the
    process substitution feeding the compare loop swallowed git status's
    exit code, so zero lines read as zero changes and printed DEPLOYABLE
    on a tree nobody actually inspected.
    """
    repo = scratch_checkout(tmp_path)
    (repo / ".git" / "index").write_bytes(b"not an index")
    result = gate(repo)
    assert result.returncode != 0
    assert "DEPLOYABLE" not in result.stdout
