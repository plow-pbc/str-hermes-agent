"""The kanban ledger: grammar parsing and the thin CLI wrapper (no live hermes)."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("str_ledger", ROOT / "bin" / "str_ledger.py")
str_ledger = importlib.util.module_from_spec(_spec)
sys.modules["str_ledger"] = str_ledger  # dataclasses resolves annotations via sys.modules
_spec.loader.exec_module(str_ledger)

T0 = 1788200000


def comments(bodies):
    return [{"author": "user", "body": b, "created_at": T0 + i} for i, b in enumerate(bodies)]


def test_parse_grammar():
    led = str_ledger.parse(comments(["WOKE cleaner_coming", "SENT cleaner_coming",
                                     "WOKE ready", "CLEAN Jane", "hello there"]))
    assert set(led.woke) == {"cleaner_coming", "ready"}
    assert led.done == {"cleaner_coming"} and led.clean_by == "Jane" and not led.hold
    assert led.woke["ready"] == datetime.fromtimestamp(T0 + 2, timezone.utc)


@pytest.mark.parametrize("bodies,done,clean_by,hold", [
    (["SKIPPED ready: guest cancelled"], {"ready"}, None, False),
    (["CLEAN Jane", "CLEAN Maria Lopez"], set(), "Maria Lopez", False),
    (["HOLD Sam and Mary"], set(), None, True),
    (["woke x", "SENT", "CLEAN", "free text"], set(), None, False),
])
def test_parse_verbs(bodies, done, clean_by, hold):
    led = str_ledger.parse(comments(bodies))
    assert (led.done, led.clean_by, led.hold) == (done, clean_by, hold)


def test_ensure_passes_idempotency_key_and_no_assignee():
    calls = []
    board = str_ledger.Board(run=lambda argv: calls.append(argv) or '{"id": "t_1"}')
    assert board.ensure("turnover:S1", "notify-when-house-is-ready S1", "b") == "t_1"
    assert "--idempotency-key" in calls[0] and "turnover:S1" in calls[0]
    assert "--assignee" not in calls[0]
    assert "notify-when-house-is-ready S1" in calls[0]


def test_ledger_reads_show_json():
    fixture = (ROOT / "tests" / "fixtures" / "kanban_show.json").read_text()
    board = str_ledger.Board(run=lambda argv: fixture)
    status, led = board.ledger("t_1")
    assert status == "ready" and led.done == {"cleaner_coming"} and led.clean_by == "Jane"
    assert json.loads(fixture)["task"]["id"] == "t_1"


def test_open_tasks_keys_live_tasks_by_the_titles_second_token():
    rows = [{"id": "t_1", "title": "notify-when-house-is-ready turnover:S1 Pat 2026-11-03", "status": "ready"},
            {"id": "t_2", "title": "notify-when-house-is-ready turnover:S2 Ann 2026-11-02", "status": "blocked"},
            {"id": "t_3", "title": "notify-when-house-is-ready turnover:S3 Bo 2026-11-01", "status": "done"},
            {"id": "t_4", "title": "notify-when-house-is-ready turnover:S4 Cy 2026-11-01", "status": "archived"},
            {"id": "t_5", "title": "buy towels", "status": "ready"}]
    board = str_ledger.Board(run=lambda argv: json.dumps(rows))
    assert board.open_tasks() == {"turnover:S1": "t_1", "turnover:S2": "t_2"}
