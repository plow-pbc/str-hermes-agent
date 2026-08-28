"""What `scripts/enable-wiki-nightly.sh` asks the scheduler for.

The script's whole job is one `cron create`, so the invocation is what these
pin. It is worth pinning because the shape that shipped in the README was
accepted by no one: `--script` alone is refused, so the documented command
created nothing while reading like it had, and `hermes cron list` on the live
host carried no `wiki-nightly` for it.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENABLE = ROOT / "scripts" / "enable-wiki-nightly.sh"
NAME = "wiki-nightly"
SCRIPT = "nightly.sh"
EXISTING_JOB = f"  Name:      {NAME}"

# Same shape as the poller's fake: record the argv the gateway would receive
# rather than re-implementing the scheduler, NUL-delimited so an argument
# carrying a space cannot compare equal to two arguments.
#
FAKE_AGENT_MGR = """#!/usr/bin/env bash
case "$*" in
  *"cron list"*)    [ -s "$JOBS" ] && cat "$JOBS"
                    exit ${PRE_LIST_OK:-0} ;;
  *"cron create"*)  echo cron >> "$CALLS"
                    printf '%s\\0' "$@" > "$CRON_ARGV"
                    prev=
                    for a in "$@"; do
                      [ "$prev" = "--name" ] && echo "  Name:      $a"
                      prev=$a
                    done
                    exit ${CREATE_OK:-0} ;;
esac
exit 0
"""


def enable(tmp_path, *, create_ok=0, pre_list_ok=0, jobs_seed=""):
    calls, jobs, argv = tmp_path / "calls", tmp_path / "jobs", tmp_path / "argv"
    jobs.write_text(jobs_seed)
    # agent-mgr, not docker: the enable scripts reach the container
    # through it now, so that is the boundary the fake stands at. The
    # case globs below match on "$*", so the extra `compose str` words
    # pass straight through.
    fake = tmp_path / "agent-mgr"
    fake.write_text(FAKE_AGENT_MGR)
    fake.chmod(0o755)
    env = {**os.environ,
           "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "CALLS": str(calls), "JOBS": str(jobs), "CRON_ARGV": str(argv),
           "CREATE_OK": str(create_ok), "PRE_LIST_OK": str(pre_list_ok)}
    run = subprocess.run(["bash", str(ENABLE)], capture_output=True, env=env, text=True)
    return (run,
            calls.read_text().split() if calls.exists() else [],
            argv.read_text() if argv.exists() else "")


def test_the_job_asked_for_runs_the_script_without_an_agent_turn(tmp_path):
    """The invocation, whole-token, because that is all this script controls.

    `--no-agent` is the defect fix: without it the CLI refuses `--script` on
    its own, which is why the README's command registered nothing. It is also
    what keeps `nightly.sh`'s stdout — vault text distilled from guest mail —
    out of an agent's instruction channel (#44), so a regression that dropped
    it would be a boundary change, not a cosmetic one.
    """
    run, calls, argv = enable(tmp_path)

    assert run.returncode == 0
    assert calls == ["cron"]
    tokens = argv.split("\0")[:-1]
    assert tokens[tokens.index("create") + 1] == "0 3 * * *"
    assert tokens[tokens.index("--name") + 1] == NAME
    assert tokens[tokens.index("--script") + 1] == SCRIPT
    assert "--no-agent" in tokens
    # `cron create` echoes what it made, and that echo is the operator's only
    # confirmation — so the call must not be redirected or captured.
    assert any(line.split() == ["Name:", NAME] for line in run.stdout.splitlines())


@pytest.mark.parametrize(
    ("kwargs", "creates", "says"),
    [
        pytest.param({"jobs_seed": EXISTING_JOB}, False, "already exists",
                     id="refuses-a-second-job"),
        pytest.param({"pre_list_ok": 1}, False, None,
                     id="unreadable-listing-creates-nothing"),
        pytest.param({"create_ok": 1}, True, None,
                     id="a-refused-create-fails-the-run"),
    ],
)
def test_the_run_stops_rather_than_leaving_the_chain_half_registered(
    tmp_path, kwargs, creates, says
):
    """Every failure path exits non-zero, and the two that precede the create
    do not reach it.

    A second job matters because the chain ingests into the vault and rebuilds
    SOUL.md — two runs race the same pages and the same SOUL. An unreadable
    listing is the same case with the answer missing: `set -e` has to stop on
    the docker error rather than read it as "no job" and stack one.
    """
    run, calls, _ = enable(tmp_path, **kwargs)

    assert run.returncode != 0
    assert (calls == ["cron"]) is creates
    if says:
        assert says in run.stdout + run.stderr
