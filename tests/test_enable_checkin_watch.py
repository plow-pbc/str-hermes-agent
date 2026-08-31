"""What `scripts/enable-checkin-watch.sh` asks the scheduler for.

Mirrors `test_enable_wiki_nightly.py`'s recording-fake shape, extended to
answer the extra `exec` calls this script makes: the `$HERMES_HOME` printf,
the ops.toml gate, `cron list`, and the two dotenv `sed` reads that resolve
the owners' group name to its chat uid (same resolution as
`enable-hostex-inbound.sh`).
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENABLE = ROOT / "scripts" / "enable-checkin-watch.sh"
NAME = "checkin-watch"
SCRIPT = "checkin-watch.py"
EXISTING_JOB = f"  Name:      {NAME}"

# Same shape as the wiki-nightly and hostex-inbound fakes: record the argv the
# gateway would receive rather than re-implementing the scheduler, NUL-
# delimited so an argument carrying a space cannot compare equal to two
# arguments. The dotenv reads are served inline (group name "Owners", uid
# mapping "cht_x=Owners") rather than from a real file, since the fake
# intercepts the whole `agent-mgr ... exec ... sh -c '...'` argv before any
# shell inside a container would see it.
FAKE_AGENT_MGR = """#!/usr/bin/env bash
case "$*" in
  *'printf %s "$HERMES_HOME"'*) printf '%s' "$STATE" ;;
  *"test -f"*)      exit ${OPS_TOML_EXIT:-0} ;;
  *"cron list"*)    [ -s "$JOBS" ] && cat "$JOBS"
                    exit ${PRE_LIST_OK:-0} ;;
  *"PLOW_CHAT_APPROVAL_GROUP="*) echo "Owners" ;;
  *"PLOW_CHAT_GROUP_UIDS="*)     echo "cht_x=Owners" ;;
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


def enable(tmp_path, *, ops_present=True, jobs_seed="", create_ok=0):
    calls, jobs, argv = tmp_path / "calls", tmp_path / "jobs", tmp_path / "argv"
    jobs.write_text(jobs_seed)
    # agent-mgr, not docker: the enable scripts reach the container through
    # it now, so that is the boundary the fake stands at. The case globs
    # below match on "$*", so the extra `compose str` words pass through.
    fake = tmp_path / "agent-mgr"
    fake.write_text(FAKE_AGENT_MGR)
    fake.chmod(0o755)
    env = {**os.environ,
           "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "STATE": str(tmp_path / "hermes-home"),
           "CALLS": str(calls), "JOBS": str(jobs), "CRON_ARGV": str(argv),
           "OPS_TOML_EXIT": "0" if ops_present else "1",
           "CREATE_OK": str(create_ok)}
    run = subprocess.run(["bash", str(ENABLE)], capture_output=True, env=env, text=True)
    return (run,
            calls.read_text().split() if calls.exists() else [],
            argv.read_text() if argv.exists() else "")


def test_creates_the_daily_job_delivering_to_the_owners_group(tmp_path):
    run, calls, argv = enable(tmp_path)

    assert run.returncode == 0
    assert calls == ["cron"]
    tokens = argv.split("\0")[:-1]
    assert tokens[tokens.index("create") + 1] == "0 12 * * *"
    assert tokens[tokens.index("--name") + 1] == NAME
    assert tokens[tokens.index("--script") + 1] == SCRIPT
    assert tokens[tokens.index("--deliver") + 1] == "plow_chat:cht_x"


def test_refuses_a_second_job(tmp_path):
    run, calls, argv = enable(tmp_path, jobs_seed=EXISTING_JOB)

    assert run.returncode != 0
    assert "cron" not in calls


def test_refuses_without_ops_toml(tmp_path):
    run, calls, argv = enable(tmp_path, ops_present=False)

    assert run.returncode != 0
    assert "cron" not in calls


@pytest.mark.parametrize(
    ("kwargs", "says"),
    [
        pytest.param({"jobs_seed": EXISTING_JOB}, "already exists",
                     id="second-job-message"),
        pytest.param({"ops_present": False}, "ops.toml",
                     id="missing-ops-toml-message"),
        pytest.param({"create_ok": 1}, None, id="a-refused-create-fails-the-run"),
    ],
)
def test_the_run_stops_rather_than_creating_a_half_wired_job(tmp_path, kwargs, says):
    run, calls, _ = enable(tmp_path, **kwargs)

    assert run.returncode != 0
    if says:
        assert says in run.stdout + run.stderr
