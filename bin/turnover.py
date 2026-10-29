"""Which turnover-readiness step is due next: the whole decision table, pure.

The cron driver builds a Stay and Signals (Hostex, Seam), reads the stay's
Ledger, and wakes one session per returned Step in that step's own thread.
Nothing here does I/O or reads a clock: `sig.now` is the only "now", and every
comparison is between aware datetimes, so property-local and UTC mix safely.
Local clock thresholds (06:00, 14:00) are taken on the check-in date in the
check-in's own timezone. Reasons never carry a door code.
Stdlib only: runs on the image's python.
"""
from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import str_ledger  # noqa: E402

CLEAN_AFTER = timedelta(hours=12)
QUIET_AFTER = timedelta(minutes=45)
UNCONFIRMED_AFTER = timedelta(minutes=30)
ESCALATE_AFTER = timedelta(minutes=10)


@dataclass(frozen=True)
class Stay:
    key: str
    guest: str
    checkin_at: datetime
    prev_checkout_at: datetime | None
    same_day: bool


@dataclass(frozen=True)
class Signals:
    now: datetime
    hostex_task_completed: bool
    cleaner_unlocks: tuple[datetime, ...]
    last_lock_event_at: datetime | None
    lock_online: bool
    code: Literal["not_set", "set", "live"]


@dataclass(frozen=True)
class Step:
    name: str
    thread: Literal["owners", "cleaners", "maintenance"]
    reason: str


def is_clean(stay: Stay, sig: Signals, led: str_ledger.Ledger) -> str | None:
    """Which rule made the house clean: hostex, lock+12h, reply:<who>; else None."""
    if sig.hostex_task_completed:
        return "hostex"
    since = [u for u in sig.cleaner_unlocks
             if stay.prev_checkout_at is None or u > stay.prev_checkout_at]
    if since and sig.now - max(since) > CLEAN_AFTER:
        return "lock+12h"
    if led.clean_by:
        return f"reply:{led.clean_by}"
    return None


def _local(stay: Stay, at: time) -> datetime:
    return datetime.combine(stay.checkin_at.date(), at, tzinfo=stay.checkin_at.tzinfo)


def due_steps(stay: Stay, sig: Signals, led: str_ledger.Ledger) -> list[Step]:
    """Steps to wake now, in table order, `escalate:*` last; none already woken."""
    now = sig.now
    clean = is_clean(stay, sig, led) is not None
    lock_down = sig.code == "not_set" or not sig.lock_online
    morning = now >= _local(stay, time(6))
    done_ask = led.woke.get("cleaner_done_ask")
    cands: list[tuple[bool, Step]] = [
        (morning and lock_down, Step("lock_alert", "owners",
            "06:00 passed and the door code is not set or the lock is offline")),
        (morning and lock_down, Step("breaker_ask", "maintenance",
            "the lock is offline or has no code: ask them to reset the breaker")),
        ("lock_alert" in led.woke and sig.code == "live" and sig.lock_online,
         Step("send_code", "owners", "the guest's code is now live on the lock")),
        (now >= _local(stay, time(14)) and not sig.cleaner_unlocks and not clean,
         Step("cleaner_coming", "cleaners", "14:00 and no cleaner entry since checkout")),
        (stay.same_day and bool(sig.cleaner_unlocks) and not clean
         and sig.last_lock_event_at is not None
         and now - sig.last_lock_event_at >= QUIET_AFTER and now < stay.checkin_at,
         Step("cleaner_quiet_ask", "cleaners",
              "the lock has been quiet 45 minutes after the cleaner's visit")),
        (now >= stay.checkin_at and not clean,
         Step("cleaner_done_ask", "cleaners", "check-in time and the house is not confirmed clean")),
        (done_ask is not None and now - done_ask >= UNCONFIRMED_AFTER and not clean,
         Step("owners_unconfirmed", "owners",
              "the cleaner has not confirmed 30 minutes after the check-in-time ask")),
        (clean and sig.code == "live" and sig.lock_online,
         Step("ready", "owners", "the house is clean and the guest's code is live on an online lock")),
    ]
    steps = [s for ok, s in cands if ok and s.name not in led.woke]
    if led.hold:
        steps = []
    for name, at in sorted(led.woke.items(), key=lambda kv: kv[1]):
        if (not name.startswith("escalate:") and now - at >= ESCALATE_AFTER
                and name not in led.done and f"escalate:{name}" not in led.woke):
            steps.append(Step(f"escalate:{name}", "owners",
                              f"{name} was woken 10 minutes ago and nothing was recorded"))
    return steps
