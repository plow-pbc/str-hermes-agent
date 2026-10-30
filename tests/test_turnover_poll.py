"""What bin/turnover-poll.py does on a tick: which tasks it keeps, what it wakes
and where, in what order, and that a door code never leaves the process.

Hostex, Seam and the kanban board are fakes; the wake inbox is a real tmp dir.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
_spec = importlib.util.spec_from_file_location("turnover_poll", ROOT / "bin" / "turnover-poll.py")
poll = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(poll)
str_ledger = sys.modules["str_ledger"]

TZ = ZoneInfo("America/Los_Angeles")
TODAY = "2026-11-03"
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
maintenance_name = "Max"
maintenance_thread = "Maintenance"
"""
GROUPS = {poll.OWNERS: "cht_owners", "Cleaners": "cht_cleaners", "Maintenance": "cht_maint"}


def prop():
    return poll.load_ops_text(OPS)[0]


def local(hhmm, day=TODAY):
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=TZ)


def reservation(stay_code="S1", status="accepted", check_in=TODAY, check_out="2026-11-05",
                lock_code="Cabin:4821", guest="Pat Example"):
    return {"reservation_code": f"R-{stay_code}", "stay_code": stay_code, "status": status,
            "check_in_date": check_in, "check_out_date": check_out, "guest_name": guest,
            "conversation_id": f"conv-{stay_code}",
            "check_in_details": {"arrival_at": None, "lock_code": lock_code}}


DEPARTING = reservation("S0", check_in="2026-10-31", check_out=TODAY, lock_code=None)


class Board:
    """The kanban board as the driver sees it, recording every write into `log`."""

    def __init__(self, log, now):
        self.log, self.now, self.tasks = log, now, {}

    def ensure(self, key, title, body):
        live = [t for t in self.tasks.values() if t["key"] == key and t["status"] != "archived"]
        if live:
            return live[0]["id"]
        task_id = f"t_{len(self.tasks) + 1}"
        self.tasks[task_id] = {"id": task_id, "key": key, "title": title, "body": body,
                               "status": "ready", "comments": []}
        return task_id

    def ledger(self, task_id):
        t = self.tasks[task_id]
        return t["status"], str_ledger.parse(t["comments"])

    def note(self, task_id, text):
        self.log.append(("note", text))
        self.tasks[task_id]["comments"].append({"body": text, "created_at": self.now.timestamp()})

    def by_key(self, prefix):  # an archived task wins its key, as in str_ledger
        rows = sorted(self.tasks.values(), key=lambda t: t["status"] == "archived")
        return {t["key"]: (t["id"], t["status"]) for t in rows if t["title"].startswith(prefix)}

    def block(self, task_id, reason):
        self.tasks[task_id]["status"] = "blocked"

    def complete(self, task_id):
        self.tasks[task_id]["status"] = "done"

    def archive(self, task_id):
        self.tasks[task_id]["status"] = "archived"


def hostex_fake(reservations=(DEPARTING, reservation()), cleaning="pending", fail=False):
    def get(path, **params):
        if fail:
            raise RuntimeError("hostex down")
        if path == "/reservations":
            assert params["status"] == "accepted" and params["end_check_in_date"] == TODAY
            return {"data": {"reservations": list(reservations)}}
        assert path == "/tasks"
        return {"data": {"tasks": [{"id": 1, "type": "cleaning", "status": cleaning,
                                    "stay_code": "S0", "staff_name": "Jane",
                                    "expected_date": TODAY}]}}
    return get


def seam_fake(events=(), online=True, lock_codes=("4821",)):
    def post(path, payload):
        assert payload["device_id"] == "dev-1"
        if path == "/events/list":
            return {"events": list(events)}
        if path == "/devices/get":
            return {"device": {"properties": {"online": online}}}
        assert path == "/access_codes/list"
        return {"access_codes": [{"access_code_id": f"ac-{i}", "name": "x", "code": c}
                                 for i, c in enumerate(lock_codes)]}
    return post


@pytest.fixture
def tick(tmp_path, monkeypatch):
    """Run ticks against one board and inbox; returns (run, board, log, wakes)."""
    log = []
    board = Board(log, local("14:00"))
    real_wake = poll.wake

    def recording_wake(home, chat, sender, text):
        log.append(("wake", chat))
        return real_wake(home, chat, sender, text)
    monkeypatch.setattr(poll, "wake", recording_wake)

    def run(at="14:00", hostex=None, seam=None, ops=None):
        board.now = local(at)
        for w in (tmp_path / "plow_chat_wakes").glob("*.json"):
            w.unlink()  # the plugin drained it
        return poll.run(ops or [prop()], hostex or hostex_fake(), seam or seam_fake(), board,
                        tmp_path, GROUPS, local(at).astimezone(timezone.utc))

    def wakes():
        return [json.loads(w.read_text()) for w in sorted((tmp_path / "plow_chat_wakes").glob("*.json"))]
    return run, board, log, wakes


def test_creates_one_task_per_stay_and_wakes_cleaners_at_1400(tick):
    run, board, _, wakes = tick
    assert run("13:58") == poll.SILENT
    assert wakes() == []
    run("14:00")
    (woken,) = wakes()
    assert (woken["chat"], woken["from"]) == ("cht_cleaners", "Turnover")
    assert "cleaner_coming" in woken["text"] and "Example Property" in woken["text"]
    assert "16:00" in woken["text"]
    assert "Pat" not in woken["text"]  # a vendor thread never learns the guest's name
    assert "kanban_show" in woken["text"] and "SENT cleaner_coming" in woken["text"]
    run("14:02")
    assert wakes() == []  # at most once
    (task,) = board.tasks.values()
    assert task["key"] == "turnover:S1"
    assert task["title"] == f"notify-when-house-is-ready turnover:S1 Pat {TODAY}"


def test_note_precedes_wake(tick):
    run, _, log, _ = tick
    run("14:00")
    assert log == [("note", "WOKE cleaner_coming"), ("wake", "cht_cleaners")]


def test_cancelled_stay_archives_task(tick):
    run, board, _, wakes = tick
    run("10:00")
    run("10:02", hostex=hostex_fake(reservations=(DEPARTING,)))
    (task,) = board.tasks.values()
    assert task["status"] == "archived" and task["comments"][-1]["body"].startswith("STALE ")
    assert wakes() == []


def test_an_archived_stay_is_never_recreated_or_woken_again(tick):
    """Hermes' idempotency key skips archived tasks, so ensure would start an
    empty ledger and re-wake every step, `ready` (a guest message) included."""
    run, board, log, wakes = tick
    clean = hostex_fake(cleaning="completed")
    run("14:00", hostex=clean)                            # ready woken
    (task,) = board.tasks.values()
    board.archive(task["id"])                             # an owner stops it, or a STALE sweep
    log.clear()
    run("14:02", hostex=clean)
    assert list(board.tasks) == [task["id"]] and wakes() == [] and log == []


def test_a_missing_thread_label_fails_its_property_after_the_others_ran(tick):
    run, board, _, wakes = tick
    relabelled = {**prop(), "title": "Relabelled Property", "cleaners_thread": "Gone"}
    with pytest.raises(RuntimeError, match="Relabelled Property.*Gone"):
        run("14:00", ops=[relabelled, prop()])
    assert [w["chat"] for w in wakes()] == ["cht_cleaners"]


def test_a_failing_property_raises_after_the_others_and_archives_nothing(tick):
    run, board, _, wakes = tick
    run("10:00")                                          # Example Property's S1 task
    other = {**prop(), "title": "Second Property", "hostex_property_id": 2}
    answers = iter([hostex_fake(fail=True), hostex_fake(reservations=(DEPARTING, reservation("S2")))])
    with pytest.raises(RuntimeError, match="Example Property"):
        run("14:00", ops=[prop(), other],
            hostex=lambda path, **p: (next(answers) if path == "/reservations" else hostex_fake())(path, **p))
    assert {t["key"]: t["status"] for t in board.tasks.values()} == {
        "turnover:S1": "ready", "turnover:S2": "ready"}  # S1 unseen this tick, yet kept
    assert [w["chat"] for w in wakes()] == ["cht_cleaners"]  # the second property still ran


@pytest.mark.parametrize("lock_code,online,codes,expected", [
    ("Cabin:Code not set yet", True, ("4821",), [("cht_owners", "lock_alert"), ("cht_maint", "breaker_ask")]),
    ("Cabin:4821", False, ("4821",), [("cht_owners", "lock_alert"), ("cht_maint", "breaker_ask")]),
    ("Cabin:4821", True, ("1111",), []),                 # set, not yet live: no alarm
    ("Cabin:4821", True, ("4821",), []),
], ids=["code-not-set", "lock-offline", "code-set-not-live", "all-well"])
def test_morning_lock_check_reaches_owners_and_maintenance(tick, lock_code, online, codes, expected):
    run, _, _, wakes = tick
    run("06:00", hostex=hostex_fake(reservations=(DEPARTING, reservation(lock_code=lock_code))),
        seam=seam_fake(online=online, lock_codes=codes))
    assert [(w["chat"], w["text"].split("step ")[1].split(":")[0]) for w in wakes()] == expected


def test_no_code_digits_ever_in_wake_or_note(tick):
    run, board, log, wakes = tick
    clean = hostex_fake(cleaning="completed")
    run("06:00", seam=seam_fake(online=False))           # lock_alert, breaker_ask
    run("08:00", hostex=clean)                           # send_code, ready
    texts = [w["text"] for w in wakes()]
    assert any("send_code" in t for t in texts) and any("step ready" in t for t in texts)
    ready = next(t for t in texts if "step ready" in t)
    assert "conv-S1" in ready and "send_message" in ready
    notes = [c["body"] for t in board.tasks.values() for c in t["comments"]] + [t["body"] for t in board.tasks.values()]
    assert not any("4821" in s for s in texts + notes + [str(e) for e in log])


def test_completes_after_sent_ready(tick):
    run, board, _, wakes = tick
    run("14:00", hostex=hostex_fake(cleaning="completed"))
    (task,) = board.tasks.values()
    assert [c["body"] for c in task["comments"]] == ["WOKE ready"]
    board.note(task["id"], "SENT ready")
    run("14:02", hostex=hostex_fake(cleaning="completed"))
    assert task["status"] == "done" and wakes() == []
    run("14:04", seam=lambda path, payload: pytest.fail("a finished stay reads no Seam"))
    assert task["status"] == "done"


def test_owners_unconfirmed_blocks_the_task(tick):
    run, board, _, wakes = tick
    seam = seam_fake(events=[{"event_type": "lock.unlocked", "access_code_id": "code-1",
                              "occurred_at": "2026-11-03T20:00:00Z"}])  # 12:00 local
    run("16:00", seam=seam)                               # cleaner_done_ask
    run("16:30", seam=seam)
    (task,) = board.tasks.values()
    assert task["status"] == "blocked"
    woken = wakes()
    owners = next(w["text"] for w in woken if "owners_unconfirmed" in w["text"])
    assert "Post in the owners' thread only" in owners and "Pat" in owners
    assert not any("Pat" in w["text"] for w in woken if w["chat"] == "cht_cleaners")


def test_an_undrained_inbox_stops_the_tick(tick, tmp_path):
    run, board, _, _ = tick
    (tmp_path / "plow_chat_wakes").mkdir()
    (tmp_path / "plow_chat_wakes" / "x.rejected").write_text("{}")
    with pytest.raises(RuntimeError, match="undrained"):
        run("14:00")
    assert board.tasks == {}


PINNED = {"dispatch_in_gateway": False, "default_assignee": "", "auto_subscribe_on_create": False,
          "max_in_progress": None}


@pytest.mark.parametrize("drift", [
    {"dispatch_in_gateway": True},
    {"default_assignee": "worker"},
    {"auto_subscribe_on_create": True},
    {"dispatch_in_gateway": None},  # an image that stopped reporting the key
])
def test_refuses_to_run_while_kanban_could_act_on_its_tasks(drift):
    """The ledger tasks are unassigned and the gateway would otherwise own them:
    a drifted pin is a tick that hands the stay to a detached worker or
    subscribes a thread to board wakes, so the driver refuses by name."""
    poll.require_kanban_pinned(lambda: json.dumps(PINNED))
    key = next(iter(drift))
    with pytest.raises(RuntimeError, match=key):
        poll.require_kanban_pinned(lambda: json.dumps({**PINNED, **drift}))


@pytest.mark.parametrize("lock_code,codes,expected", [
    (None, ["4821"], "not_set"),
    ("Cabin:Code not set yet", ["4821"], "not_set"),
    ("Cabin:4821", ["1111"], "set"),
    ("Cabin:4821", ["1111", "4821"], "live"),
])
def test_code_state(lock_code, codes, expected):
    assert poll.code_state(lock_code, [{"code": c} for c in codes]) == expected


def test_previous_checkout_bounds_the_stay():
    stay, prev = poll.stay_of(reservation(), prop(), [DEPARTING, reservation()], TODAY)
    assert (prev["stay_code"], stay.prev_checkout_at, stay.same_day) == ("S0", local("11:00"), True)
    stay, prev = poll.stay_of(reservation(), prop(), [reservation()], TODAY)
    assert prev is None and not stay.same_day
    assert stay.prev_checkout_at == local("16:00", "2026-10-27")  # check-in less 7 days


@pytest.mark.parametrize(("r", "expected"), [
    ({"check_in_details": {"arrival_at": {"hour": 15, "minute": 30}}}, (15, 30)),
    ({}, (16, 0)),                                        # no details at all
    ({"check_in_details": {}}, (16, 0)),                  # details, no arrival
    ({"check_in_details": {"arrival_at": None}}, (16, 0)) # explicit null (the doc's shape)
])
def test_checkin_time(r, expected):
    assert poll.checkin_time(r, prop()) == expected


@pytest.mark.parametrize("bad_ops_text,expected_match", [
    (OPS.replace('seam_device_id = "dev-1"\n', ""), "seam_device_id"),
    (OPS.replace('maintenance_thread = "Maintenance"\n', ""), "maintenance_thread"),
    (OPS.replace('maintenance_name = "Max"\n', ""), "maintenance_name"),
    (OPS.replace("16:00", "4pm"), "default_checkin_time"),
    (OPS.replace("16:00", "16:99"), "out of range"),
    (OPS.replace("16:00", "24:00"), "out of range"),
    (OPS.replace('cleaner_access_code_ids = ["code-1"]', 'cleaner_access_code_ids = []'), "cleaner_access_code_ids"),
    (OPS.replace('cleaner_access_code_ids = ["code-1"]', 'cleaner_access_code_ids = "code-1"'), "cleaner_access_code_ids"),
    ("this is not valid toml {{{", "ops.toml"),
    ("[other]\nkey = 'value'", "properties"),
], ids=["missing_seam_device_id", "missing_maintenance_thread", "missing_maintenance_name",
        "bad_default_checkin_time", "minute_out_of_range", "hour_out_of_range",
        "empty_access_code_ids", "string_access_code_ids", "bad_toml", "missing_properties_section"])
def test_load_ops_refuses_invalid_input(bad_ops_text, expected_match):
    with pytest.raises(SystemExit, match=expected_match):
        poll.load_ops_text(bad_ops_text)
