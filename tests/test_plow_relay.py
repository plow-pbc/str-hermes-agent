"""bin/plow_relay.py against a stand-in `latch` on PATH.

A real executable rather than a mock of subprocess: what matters is the argv the
CLI hands latch and what it does with each shape latch answers in. The answer
shapes are latch 0.1.0's, measured against the owner's Mac on 2026-10-29.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "bin"))
import plow_relay

# Records its argv, copies a successful `file get`'s fixture to the destination and
# `file put`'s source aside, then answers with the canned stdout, stderr and exit code.
FAKE = """#!/usr/bin/env python3
import json, os, shutil, sys
d = os.environ["FAKE_LATCH"]
with open(os.path.join(d, "calls"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
answer = json.load(open(os.path.join(d, "answer")))
if sys.argv[1:3] == ["file", "get"] and not answer["code"]:
    shutil.copy(os.path.join(d, "remote"), sys.argv[4])
if sys.argv[1:3] == ["file", "put"]:
    shutil.copy(sys.argv[3], os.path.join(d, "sent"))
sys.stdout.write(answer.get("stdout", "{}"))
sys.stderr.write(answer.get("stderr", ""))
sys.exit(answer.get("code", 0))
"""


@pytest.fixture
def latch(tmp_path, monkeypatch):
    (tmp_path / "latch").write_text(FAKE)
    (tmp_path / "latch").chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LATCH", str(tmp_path))

    def answer(stdout: dict | str = "{}", stderr: str = "", code: int = 0):
        stdout = stdout if isinstance(stdout, str) else json.dumps(stdout)
        (tmp_path / "answer").write_text(json.dumps({"stdout": stdout, "stderr": stderr, "code": code}))

    def calls():
        return [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]

    answer()
    return tmp_path, answer, calls


def test_read_prints_the_file(latch, capsys):
    tmp, answer, calls = latch
    (tmp / "remote").write_text("# Wiki Index\n")
    answer({"bytes": 13})
    assert plow_relay.main(["read", "~/Plow/wiki/index.md"]) == 0
    assert capsys.readouterr().out == "# Wiki Index\n"
    assert calls()[0][:3] == ["file", "get", "~/Plow/wiki/index.md"]


def test_write_sends_stdin_to_the_path(latch, monkeypatch):
    tmp, answer, calls = latch
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello\n"))
    assert plow_relay.main(["write", "~/Plow/wiki/x.md"]) == 0
    assert (tmp / "sent").read_text() == "hello\n"
    assert calls()[0][:2] == ["file", "put"] and calls()[0][-1] == "~/Plow/wiki/x.md"


def test_run_grants_what_it_names_and_exits_with_the_commands_code(latch, capsys):
    _, answer, calls = latch
    answer({"exit_code": 1, "signal": None, "stdout": "people/x.md: missing required field: tags\n",
            "stderr": "1 invalid\n"})
    assert plow_relay.main(["run", "--write", "~/Plow/wiki", "--write", "~/Plow/wiki.git", "--network",
                            "--", "wiki", "snapshot", "--push"]) == 1
    out = capsys.readouterr()
    assert (out.out, out.err) == ("people/x.md: missing required field: tags\n", "1 invalid\n")
    assert calls() == [["exec", "--write", "~/Plow/wiki", "--write", "~/Plow/wiki.git", "--network",
                        "--timeout", "600", "--", "wiki", "snapshot", "--push"]]


FAILURES = [
    ("latch's error line names its code",
     {"stderr": '{"error":{"code":"host_offline","message":"mba is not running","exit":3}}\n', "code": 3},
     ["read", "~/Plow/wiki/index.md"], "latch file: host_offline: mba is not running"),
    ("a command past its timeout is latch failing, not the command",
     {"stderr": '{"error":{"code":"timed_out","message":"stopped after 600s","exit":1}}\n', "code": 1},
     ["run", "--", "wiki", "index"], "latch exec: timed_out"),
    ("stderr that is not latch's JSON is passed on",
     {"stderr": "segfault\n", "code": 139}, ["run", "--", "wiki", "index"], "latch exec: segfault"),
    ("a command ended by a signal has no exit code",
     {"stdout": json.dumps({"exit_code": None, "signal": 9, "stdout": "", "stderr": ""})},
     ["run", "--", "wiki", "index"], "ended by signal 9"),
]


@pytest.mark.parametrize(("case", "reply", "argv", "message"), FAILURES, ids=[f[0] for f in FAILURES])
def test_latch_that_cannot_answer_exits_2_and_says_why(latch, capsys, case, reply, argv, message):
    _, answer, _ = latch
    answer(**reply)
    assert plow_relay.main(argv) == 2
    assert message in capsys.readouterr().err
