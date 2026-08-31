"""What bin/checkin-watch.py decides and says.

Pure-logic tests: verdicts over a fake Seam event feed, check-in time
resolution, ops.toml validation, and the rendered prompt. The REST wiring is
Task 2's test; the live APIs are never touched here.
"""
from __future__ import annotations

import datetime
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

def test_verdict_activity_most_recent_when_out_of_order():
    # Ensure activity verdict returns the event with the latest timestamp, not list position
    kind, ev = watch.verdict(
        [unlock(at="2026-08-31T17:12:00.000Z"), unlock("other", at="2026-08-31T17:15:00.000Z")],
        set()
    )
    assert kind == "activity" and ev["occurred_at"] == "2026-08-31T17:15:00.000Z"

@pytest.mark.parametrize(("r", "expected"), [
    ({"check_in_details": {"arrival_at": {"hour": 15, "minute": 30}}}, (15, 30)),
    ({}, (16, 0)),                                        # no details at all
    ({"check_in_details": {}}, (16, 0)),                  # details, no arrival
    ({"check_in_details": {"arrival_at": None}}, (16, 0)) # explicit null (the doc's shape)
])
def test_checkin_time(r, expected):
    assert watch.checkin_time(r, prop()) == expected

@pytest.mark.parametrize("bad_ops_text,expected_match", [
    (OPS.replace('seam_device_id = "dev-1"\n', ""), "seam_device_id"),
    (OPS.replace("16:00", "4pm"), "default_checkin_time"),
    (OPS.replace("16:00", "16:99"), "out of range"),
    (OPS.replace("16:00", "24:00"), "out of range"),
    (OPS.replace('cleaner_access_code_ids = ["code-1"]', 'cleaner_access_code_ids = []'), "cleaner_access_code_ids"),
    (OPS.replace('cleaner_access_code_ids = ["code-1"]', 'cleaner_access_code_ids = "code-1"'), "cleaner_access_code_ids"),
    ("this is not valid toml {{{", "ops.toml"),
    ("[other]\nkey = 'value'", "properties"),
], ids=[
    "missing_seam_device_id",
    "bad_default_checkin_time",
    "minute_out_of_range",
    "hour_out_of_range",
    "empty_access_code_ids",
    "string_access_code_ids",
    "bad_toml",
    "missing_properties_section",
])
def test_load_ops_refuses_invalid_input(bad_ops_text, expected_match):
    with pytest.raises(SystemExit, match=expected_match):
        watch.load_ops_text(bad_ops_text)

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


UTC_NOON_PT = datetime.datetime(2026, 8, 31, 19, 0, tzinfo=datetime.timezone.utc)


def fake_apis(monkeypatch, *, checkins, checkouts=(), events=(), online=True,
              hostex_error=None):
    def hostex_get(path, token, **params):
        if hostex_error:
            raise hostex_error
        if "start_check_in_date" in params:
            assert params["start_check_in_date"] == "2026-08-31"  # PT date, not UTC's
            return {"data": {"reservations": list(checkins)}}
        return {"data": {"reservations": list(checkouts)}}
    def seam_post(path, key, payload):
        if path == "/devices/get":
            return {"device": {"properties": {"online": online}}}
        assert payload["since"].startswith("2026-08-31T08:00:00")
        return {"events": list(events)}
    monkeypatch.setattr(watch, "hostex_get", hostex_get)
    monkeypatch.setattr(watch, "seam_post", seam_post)


RESERVATION = {"guest_name": "Pat", "number_of_guests": 2,
               "check_in_details": {"arrival_at": None}}


@pytest.mark.parametrize("fake_apis_kwargs,expected", [
    ({"checkins": []}, watch.SILENT),
    ({"checkins": [RESERVATION], "events": [unlock("code-1")]}, ("STARTED", "Pat")),
    ({"checkins": [RESERVATION], "online": False}, "LOCK OFFLINE"),
])
def test_run_verdicts(monkeypatch, fake_apis_kwargs, expected):
    fake_apis(monkeypatch, **fake_apis_kwargs)
    out = watch.run("t", "s", [prop()], UTC_NOON_PT)
    if isinstance(expected, tuple):
        for e in expected:
            assert e in out
    else:
        assert expected in out


def test_every_check_failing_exits_nonzero(monkeypatch):
    fake_apis(monkeypatch, checkins=[RESERVATION], hostex_error=RuntimeError("api down"))
    with pytest.raises(SystemExit, match="every check failed"):
        watch.run("t", "s", [prop()], UTC_NOON_PT)


def test_one_failure_beside_a_healthy_block_is_reported_not_fatal(monkeypatch):
    ops = [prop(), prop() | {"title": "Second Property", "hostex_property_id": 2}]
    calls = iter([None, RuntimeError("api down")])
    real = {"data": {"reservations": [RESERVATION]}}
    def hostex_get(path, token, **params):
        err = next(calls, None) if "start_check_in_date" in params else None
        if err:
            raise err
        return real if "start_check_in_date" in params else {"data": {"reservations": []}}
    monkeypatch.setattr(watch, "hostex_get", hostex_get)
    monkeypatch.setattr(watch, "seam_post",
                        lambda path, key, payload:
                        {"device": {"properties": {"online": True}}}
                        if path == "/devices/get" else {"events": []})
    out = watch.run("t", "s", ops, UTC_NOON_PT)
    assert "NOT STARTED" in out and "CHECK FAILED" in out and "Second Property" in out
