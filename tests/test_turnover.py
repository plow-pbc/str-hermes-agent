"""The turnover decision table: which step is due for a given stay, signals and ledger."""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _n in ("str_ledger", "turnover"):
    _spec = importlib.util.spec_from_file_location(_n, ROOT / "bin" / f"{_n}.py")
    _m = importlib.util.module_from_spec(_spec)
    sys.modules[_n] = _m  # dataclasses resolves annotations via sys.modules
    _spec.loader.exec_module(_m)
str_ledger, turnover = sys.modules["str_ledger"], sys.modules["turnover"]

TZ = ZoneInfo("America/Los_Angeles")
MIN = timedelta(minutes=1)


def at(hhmm, day="2026-11-03"):
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=TZ)


def aware(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


T0 = at("14:00")


def stay(prev_checkout="2026-11-03T11:00", checkin="2026-11-03T16:00", same_day=True):
    return turnover.Stay("k", "Guest", aware(checkin),
                         aware(prev_checkout) if prev_checkout else None, same_day)


def sig(now="2026-11-03T15:00", unlocks=(), last=None, online=True, code="live", hostex=False):
    n = now if isinstance(now, datetime) else aware(now)
    return turnover.Signals(n, hostex, tuple(aware(u) for u in unlocks),
                            aware(last) if last else None, online, code)


def ledger(woke=None, done=(), clean_by=None, hold=False):
    return str_ledger.Ledger(woke or {}, frozenset(done), clean_by, hold)


EMPTY = ledger()
CLEAN_LEDGER = ledger(clean_by="Jane")
S = stay()


def names(steps):
    return [s.name for s in steps]


def due(s, g, led):
    return names(turnover.due_steps(s, g, led))


@pytest.mark.parametrize("stay_, sig_, led, expected", [
    (S, sig("2026-11-03T06:00", code="not_set", unlocks=["2026-11-03T05:00"]), EMPTY,
     ["lock_alert", "breaker_ask"]),
    (S, sig("2026-11-03T05:59", code="not_set", unlocks=["2026-11-03T05:00"]), EMPTY, []),
    (S, sig("2026-11-03T07:00", online=False, unlocks=["2026-11-03T05:00"]), EMPTY,
     ["lock_alert", "breaker_ask"]),
    (S, sig("2026-11-03T08:00", unlocks=["2026-11-03T05:00"]), EMPTY, []),
    (S, sig("2026-11-03T08:00", unlocks=["2026-11-03T05:00"]),
     ledger(woke={"lock_alert": T0, "breaker_ask": T0}, done={"lock_alert", "breaker_ask"}),
     ["send_code"]),
    (S, sig("2026-11-03T08:00", code="set", unlocks=["2026-11-03T05:00"]),
     ledger(woke={"lock_alert": T0}, done={"lock_alert"}), []),
    (S, sig("2026-11-03T14:00"), EMPTY, ["cleaner_coming"]),
    (S, sig("2026-11-03T13:59"), EMPTY, []),
    (S, sig("2026-11-03T14:00", unlocks=["2026-11-03T13:00"]), EMPTY, []),
    (S, sig("2026-11-03T14:00"), CLEAN_LEDGER, ["ready"]),
    (S, sig("2026-11-03T14:00", hostex=True), EMPTY, ["ready"]),
    (S, sig("2026-11-03T14:00", code="not_set", hostex=True), EMPTY,
     ["lock_alert", "breaker_ask"]),
    (S, sig("2026-11-03T14:46", unlocks=["2026-11-03T13:00"], last="2026-11-03T14:01"), EMPTY,
     ["cleaner_quiet_ask"]),
    (S, sig("2026-11-03T14:44", unlocks=["2026-11-03T13:00"], last="2026-11-03T14:00"), EMPTY, []),
    (stay(same_day=False), sig("2026-11-03T14:46", unlocks=["2026-11-03T13:00"],
                               last="2026-11-03T14:01"), EMPTY, []),
    (S, sig("2026-11-03T16:00", unlocks=["2026-11-03T13:00"], last="2026-11-03T14:01"), EMPTY,
     ["cleaner_done_ask"]),
    (S, sig("2026-11-03T15:59", unlocks=["2026-11-03T13:00"], last="2026-11-03T14:01"),
     CLEAN_LEDGER, ["ready"]),
    (S, sig("2026-11-03T16:30", unlocks=["2026-11-03T13:00"]),
     ledger(woke={"cleaner_done_ask": at("16:00")}, done={"cleaner_done_ask"}),
     ["owners_unconfirmed"]),
    (S, sig("2026-11-03T16:29", unlocks=["2026-11-03T13:00"]),
     ledger(woke={"cleaner_done_ask": at("16:00")}, done={"cleaner_done_ask"}), []),
    (S, sig("2026-11-03T16:30", unlocks=["2026-11-03T13:00"]),
     ledger(woke={"cleaner_done_ask": at("16:00"), "owners_unconfirmed": at("16:30")},
            done={"cleaner_done_ask", "owners_unconfirmed"}), []),
    (S, sig("2026-11-03T15:00"), ledger(woke={"ready": T0}, clean_by="Jane", done={"ready"}), []),
    (S, sig("2026-11-03T14:10", code="set"), CLEAN_LEDGER, []),
    (S, sig("2026-11-03T14:10", online=False), CLEAN_LEDGER, ["lock_alert", "breaker_ask"]),
    (S, sig(T0 + 10 * MIN, code="live", unlocks=["2026-11-03T13:00"]),
     ledger(woke={"cleaner_quiet_ask": T0}), ["escalate:cleaner_quiet_ask"]),
    (S, sig(T0 + 9 * MIN, unlocks=["2026-11-03T13:00"]),
     ledger(woke={"cleaner_quiet_ask": T0}), []),
    (S, sig(T0 + 20 * MIN, unlocks=["2026-11-03T13:00"]),
     ledger(woke={"cleaner_quiet_ask": T0}, done={"cleaner_quiet_ask"}), []),
])
def test_due_steps_table(stay_, sig_, led, expected):
    assert due(stay_, sig_, led) == expected


def test_rule_b_needs_twelve_hours():
    s = stay(prev_checkout="2026-11-01T11:00", checkin="2026-11-03T16:00")
    assert turnover.is_clean(s, sig(now="2026-11-02T00:59", unlocks=["2026-11-01T13:00"]), EMPTY) is None
    assert turnover.is_clean(s, sig(now="2026-11-02T01:01", unlocks=["2026-11-01T13:00"]), EMPTY) == "lock+12h"


def test_rule_b_ignores_unlocks_before_checkout():
    s = stay(prev_checkout="2026-11-01T11:00")
    assert turnover.is_clean(s, sig(now="2026-11-03T12:00", unlocks=["2026-11-01T10:00"]), EMPTY) is None


def test_is_clean_reports_rule():
    assert turnover.is_clean(S, sig(hostex=True), EMPTY) == "hostex"
    assert turnover.is_clean(S, sig(), CLEAN_LEDGER) == "reply:Jane"


def test_ready_waits_for_lock_online():
    assert "ready" not in due(S, sig(code="live", online=False), CLEAN_LEDGER)


def test_hold_suppresses_everything_but_escalation():
    led = ledger(woke={"cleaner_coming": T0}, hold=True)
    assert due(S, sig(now=T0 + 11 * MIN, code="live"), led) == ["escalate:cleaner_coming"]


def test_woke_without_sent_escalates_once():
    led = ledger(woke={"ready": T0, "escalate:ready": T0 + 10 * MIN})
    assert "escalate:ready" not in due(S, sig(now=T0 + 30 * MIN), led)


def test_ready_can_fire_before_checkin_time():  # readiness IS the early check-in
    assert "ready" in due(S, sig(now=at("12:40"), code="live"), CLEAN_LEDGER)


def test_ledger_utc_times_compare_with_local_times():
    woke = {"cleaner_coming": datetime(2026, 11, 3, 22, 0, tzinfo=timezone.utc)}  # 14:00 PST
    assert due(S, sig("2026-11-03T14:10", unlocks=["2026-11-03T13:00"]), ledger(woke=woke)) == [
        "escalate:cleaner_coming"]


def test_escalations_come_last_in_wake_order():
    led = ledger(woke={"send_code": T0 + MIN, "lock_alert": T0})
    got = due(S, sig(now=T0 + 20 * MIN, code="not_set", unlocks=["2026-11-03T13:00"]), led)
    assert got == ["breaker_ask", "escalate:lock_alert", "escalate:send_code"]
