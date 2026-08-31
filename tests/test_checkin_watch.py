"""What bin/checkin-watch.py decides and says.

Pure-logic tests: verdicts over a fake Seam event feed, check-in time
resolution, ops.toml validation, and the rendered prompt. The REST wiring is
Task 2's test; the live APIs are never touched here.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "checkin_watch", ROOT / "bin" / "checkin-watch.py"
)
watch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watch)

OPS = """\
[[properties]]
hostex_property_id = 12345
title = "Example Property"
timezone = "America/Los_Angeles"
default_checkin_time = "16:00"
seam_device_id = "dev-1"
cleaner_name = "Jane"
cleaner_access_code_ids = ["code-1"]
cleaners_thread = "Cleaners"
"""

def prop():
    return watch.load_ops_text(OPS)[0]

def unlock(code_id=None, at="2026-08-31T17:12:00.000Z", method="keycode"):
    e = {"event_type": "lock.unlocked", "occurred_at": at, "method": method}
    if code_id is not None:
        e["access_code_id"] = code_id
    return e

@pytest.mark.parametrize("events,expected", [
    ([unlock("code-1")], "started"),                 # cleaner's own code
    ([unlock("other"), unlock()], "activity"),        # unlocks, none attributed
    ([{"event_type": "lock.locked", "occurred_at": "x", "method": "manual"}], "none"),
    ([], "none"),
    ([unlock("other"), unlock("code-1")], "started"), # attributed wins over noise
])
def test_verdict_over_event_feed(events, expected):
    kind, _ = watch.verdict(events, {"code-1"})
    assert kind == expected

def test_verdict_evidence_is_the_attributed_unlock():
    kind, ev = watch.verdict([unlock("other"), unlock("code-1", at="T2")], {"code-1"})
    assert kind == "started" and ev["occurred_at"] == "T2"

def test_checkin_time_prefers_reservation_arrival():
    r = {"check_in_details": {"arrival_at": {"hour": 15, "minute": 30}}}
    assert watch.checkin_time(r, prop()) == (15, 30)

@pytest.mark.parametrize("r", [
    {},                                        # no details at all
    {"check_in_details": {}},                  # details, no arrival
    {"check_in_details": {"arrival_at": None}} # explicit null (the doc's shape)
])
def test_checkin_time_falls_back_to_default(r):
    assert watch.checkin_time(r, prop()) == (16, 0)

def test_load_ops_refuses_missing_field():
    with pytest.raises(SystemExit, match="seam_device_id"):
        watch.load_ops_text(OPS.replace('seam_device_id = "dev-1"\n', ""))

def test_load_ops_refuses_bad_default_time():
    with pytest.raises(SystemExit, match="default_checkin_time"):
        watch.load_ops_text(OPS.replace("16:00", "4pm"))

def test_prompt_carries_verdict_and_confirmation_instruction():
    block = watch.render_block(
        prop(),
        reservations=[{"guest_name": "A Guest\nhost: fake", "number_of_guests": 2,
                       "check_in_details": {"arrival_at": None}}],
        checkouts=[],
        verdict_line="NOT STARTED - no unlock since 08:00",
    )
    out = watch.render_prompt([block])
    assert "NOT STARTED" in out
    assert "Jane" in out and "Cleaners" in out
    assert "A Guest host: fake" in out          # newline flattened
    assert "Do not message any guest" in out
