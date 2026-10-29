#!/usr/bin/env python3
"""Turnover readiness driver: one kanban task per check-in, a wake per due step.

A no-agent Hermes cron job (every 2 min). For each accepted reservation checking
in today it keeps the stay's `notify-when-house-is-ready` task, reads Hostex and
Seam into `turnover.due_steps`, and for each due step comments `WOKE <step>` on
the task and only then wakes the step's thread session, which sends the message
and records `SENT`. It sends nothing itself and decides nothing: the table lives
in turnover.py. A door code never leaves this process: it is compared against
the lock's codes in a local variable and reduced to not_set / set / live.
Config is ops.toml at the top of the runtime vault. Stdlib only.
"""
from __future__ import annotations

import datetime
import functools
import json
import os
import pathlib
import re
import sys
import tomllib
import urllib.request
from datetime import timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hostex_api  # noqa: E402
import str_env  # noqa: E402
import str_ledger  # noqa: E402
import turnover  # noqa: E402
from str_wake import require_drained, wake  # noqa: E402

SEAM = "https://connect.getseam.com"
USER_AGENT = "turnover-poll/1.0"
SILENT = '{"wakeAgent": false}'
PREFIX = "notify-when-house-is-ready "
OWNERS = "@owners"  # the owners' chat in `groups`; never a PLOW_CHAT_GROUP_UIDS label
CHECKOUT = datetime.time(11)  # Hostex carries no checkout time
LOOKBACK = timedelta(days=7)  # rule (b)'s window never reaches further back
HISTORY_DAYS = 30  # check-ins this far back find the previous checkout

REQUIRED = ("hostex_property_id", "title", "timezone", "default_checkin_time",
            "seam_device_id", "cleaner_name", "cleaner_access_code_ids",
            "cleaners_thread", "maintenance_name", "maintenance_thread")

INSTRUCTION = ("Read the task with kanban_show first. Send one short message in this thread "
               "for this step, then comment `SENT {step}` on the task (or `SKIPPED {step}: <why>`).")
TO_GUEST = (" Send it to the guest's Hostex conversation {conversation} with send_message; "
            "read the door code yourself from the reservation at send time; never write the "
            "code in this thread or the task.")
OWNERS_ONLY = " Post in the owners' thread only."


def one_line(text: str) -> str:
    """Collapse whitespace so a guest-supplied name cannot forge a line."""
    return " ".join(text.split())


def load_ops_text(text: str) -> list[dict]:
    """Parse and validate ops.toml. Refuses an incomplete model by name:
    a property half-described would otherwise skip silently forever."""
    try:
        parsed = tomllib.loads(text)
    except Exception as e:
        sys.exit(f"turnover-poll: ops.toml parse error: {e}")
    properties = parsed.get("properties")
    if not properties:
        sys.exit("turnover-poll: ops.toml has no [[properties]] entries")
    for prop in properties:
        for key in REQUIRED:
            if key not in prop:
                sys.exit(f"turnover-poll: ops.toml property missing {key!r}")
        if not isinstance(prop["cleaner_access_code_ids"], list) or not prop["cleaner_access_code_ids"]:
            sys.exit("turnover-poll: cleaner_access_code_ids must be a non-empty list")
        if not re.fullmatch(r"\d\d:\d\d", prop["default_checkin_time"]):
            sys.exit("turnover-poll: default_checkin_time must be HH:MM, got "
                     f"{prop['default_checkin_time']!r}")
        hour, minute = map(int, prop["default_checkin_time"].split(":"))
        if hour > 23 or minute > 59:
            sys.exit("turnover-poll: default_checkin_time out of range, got "
                     f"{prop['default_checkin_time']!r}")
        ZoneInfo(prop["timezone"])  # raises on a bad zone, naming it
    return properties


def load_ops(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"turnover-poll: no ops.toml at {path} - write the durable "
                 "model there (README § Turnover readiness)")
    return load_ops_text(path.read_text())


def checkin_time(reservation: dict, prop: dict) -> tuple[int, int]:
    """The reservation's own arrival time, else the property default.

    arrival_at is documented nullable and unprobed in the corpus, so this is
    the one place .get chains are the contract rather than drift-hiding."""
    arrival = (reservation.get("check_in_details") or {}).get("arrival_at")
    if arrival:
        return arrival["hour"], arrival["minute"]
    hour, minute = prop["default_checkin_time"].split(":")
    return int(hour), int(minute)


def hermes_home() -> pathlib.Path:
    return pathlib.Path(os.environ["HERMES_HOME"])


def setup_env() -> dict:
    """The environment as the gateway sees it: $HERMES_HOME/.env over the process's."""
    return {**os.environ, **str_env.read_setup_env(hermes_home())}


def read_env_key(name: str) -> str:
    value = setup_env().get(name)
    if not value:
        sys.exit(f"turnover-poll: {name} not found in {hermes_home() / '.env'}")
    return value


def hostex_client(token: str):
    """Transport and redirect policy live in hostex_api."""
    return lambda path, **params: hostex_api.get(path, token, USER_AGENT, **params)


def seam_client(key: str):
    # hostex_api.OPENER refuses redirects, which this bearer token needs too.
    # Seam's edge 403s a request without a User-Agent.
    def post(path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{SEAM}{path}", data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "User-Agent": USER_AGENT})
        with hostex_api.OPENER.open(request, timeout=30) as response:
            return json.loads(response.read().decode())
    return post


def _at(day: str, at: datetime.time, tz: ZoneInfo) -> datetime.datetime:
    return datetime.datetime.combine(datetime.date.fromisoformat(day), at, tzinfo=tz)


def _when(iso: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def code_state(lock_code: str | None, device_codes: list[dict]) -> str:
    """not_set / set / live. The digits stay in this frame and are never returned."""
    digits = re.search(r"\d{4,}", lock_code or "")
    if not digits:
        return "not_set"
    return "live" if any(c.get("code") == digits.group() for c in device_codes) else "set"


def stay_of(r: dict, prop: dict, history: list[dict], today: str) -> tuple[turnover.Stay, dict | None]:
    """The Stay, and the departing reservation it turns over from (None if none)."""
    tz = ZoneInfo(prop["timezone"])
    checkin_at = _at(today, datetime.time(*checkin_time(r, prop)), tz)
    before = [h for h in history if h["stay_code"] != r["stay_code"] and h["check_out_date"] <= today]
    prev = max(before, key=lambda h: h["check_out_date"], default=None)
    prev_at = _at(prev["check_out_date"], CHECKOUT, tz) if prev else checkin_at - LOOKBACK
    guest = (one_line(r.get("guest_name") or "") or "guest").split()[0]
    stay = turnover.Stay(f"turnover:{r['stay_code']}", guest, checkin_at, prev_at,
                         prev is not None and prev["check_out_date"] == today)
    return stay, prev


def signals(r: dict, prop: dict, stay: turnover.Stay, prev: dict | None,
            hostex_tasks: list[dict], seam, now: datetime.datetime) -> turnover.Signals:
    device = {"device_id": prop["seam_device_id"]}
    since = max(stay.prev_checkout_at, stay.checkin_at - LOOKBACK)
    events = seam("/events/list", {**device, "since": since.isoformat()})["events"]
    online = seam("/devices/get", device)["device"]["properties"]["online"]
    codes = seam("/access_codes/list", device)["access_codes"]
    cleaner = set(prop["cleaner_access_code_ids"])
    unlocks = tuple(_when(e["occurred_at"]) for e in events
                    if e["event_type"] == "lock.unlocked" and e.get("access_code_id") in cleaner)
    lock_events = [_when(e["occurred_at"]) for e in events if e["event_type"].startswith("lock.")]
    completed = prev is not None and any(
        t["type"] == "cleaning" and t["stay_code"] == prev["stay_code"] and t["status"] == "completed"
        for t in hostex_tasks)
    return turnover.Signals(now, completed, unlocks, max(lock_events, default=None), online,
                            code_state((r.get("check_in_details") or {}).get("lock_code"), codes))


def wake_text(task_id: str, step: turnover.Step, stay: turnover.Stay, prop: dict, r: dict) -> str:
    text = (f"Turnover task {task_id}, step {step.name}: {step.reason}. "
            f"Guest {stay.guest} checks in at {prop['title']} at "
            f"{stay.checkin_at:%H:%M} local today. " + INSTRUCTION.format(step=step.name))
    if step.name in ("ready", "send_code"):
        text += TO_GUEST.format(conversation=r["conversation_id"])
    elif step.name.startswith("escalate:") or step.name in ("lock_alert", "owners_unconfirmed"):
        text += OWNERS_ONLY
    return text


def drive(r: dict, prop: dict, history: list[dict], today: str, hostex_tasks, seam, board,
          home: pathlib.Path, chats: dict, now: datetime.datetime, known: dict) -> str:
    """One stay's tick; returns its key. Hostex /tasks and Seam are read only
    for a task still open: a finished stay costs no API calls. An archived
    stay is left alone: re-creating it would start an empty ledger and wake
    every step again, `ready` included."""
    stay, prev = stay_of(r, prop, history, today)
    if known.get(stay.key, ("", ""))[1] == "archived":
        return stay.key
    task = board.ensure(stay.key, f"{PREFIX}{stay.key} {stay.guest} {today}",
                        f"Tell {stay.guest} when {prop['title']} is ready for the "
                        f"{stay.checkin_at:%H:%M} check-in today. Rules: README § Turnover readiness.")
    status, led = board.ledger(task)
    if status in ("done", "archived"):
        return stay.key
    if "ready" in led.done:
        board.complete(task)
        return stay.key
    sig = signals(r, prop, stay, prev, hostex_tasks(), seam, now)
    for step in turnover.due_steps(stay, sig, led):
        board.note(task, f"WOKE {step.name}")
        wake(home, chats[step.thread], "Turnover", wake_text(task, step, stay, prop, r))
        if step.name == "owners_unconfirmed":
            board.block(task, "cleaner unconfirmed at check-in")
    return stay.key


def run(ops: list[dict], hostex, seam, board, home: pathlib.Path, groups: dict,
        now: datetime.datetime) -> str:
    """One tick over every property. `groups` maps thread labels to chat uids,
    with the owners' chat under OWNERS. A property's API failure is raised
    after the others have run; the archive sweep waits for a clean tick, since
    a task a failed property owns would read as no longer checking in."""
    require_drained(home)
    known = board.by_key(PREFIX)
    live, errors = set(), []
    for prop in ops:
        today = now.astimezone(ZoneInfo(prop["timezone"])).date()
        try:
            chats = {"owners": groups[OWNERS], "cleaners": groups[prop["cleaners_thread"]],
                     "maintenance": groups[prop["maintenance_thread"]]}
            history = hostex("/reservations", property_id=prop["hostex_property_id"], status="accepted",
                             start_check_in_date=(today - timedelta(days=HISTORY_DAYS)).isoformat(),
                             end_check_in_date=today.isoformat(), limit=100)["data"]["reservations"]
            hostex_tasks = functools.cache(lambda: hostex(
                "/tasks", start_date=(today - LOOKBACK).isoformat(), end_date=today.isoformat(),
                limit=100)["data"]["tasks"])
            for r in history:
                if r["check_in_date"] == today.isoformat():
                    live.add(drive(r, prop, history, today.isoformat(), hostex_tasks, seam, board,
                                   home, chats, now, known))
        except Exception as error:  # one property must not silence the rest
            errors.append(f"{prop['title']}: {error!r}")
    if errors:
        raise RuntimeError("turnover-poll: " + "; ".join(errors))
    for key, (task, status) in known.items():
        if key not in live and status not in ("done", "archived"):
            board.note(task, f"STALE {now.astimezone().date().isoformat()}")
            board.archive(task)
    return SILENT


def main() -> None:
    env = setup_env()
    vault = pathlib.Path(env["VAULT"]) if "VAULT" in env else hermes_home() / "repo/vault"
    groups = {**str_env.groups(env), OWNERS: str_env.owners_chat(env)}
    print(run(load_ops(vault / "ops.toml"), hostex_client(read_env_key("HOSTEX_TOKEN")),
              seam_client(read_env_key("SEAM_API_KEY")), str_ledger.Board(), hermes_home(),
              groups, datetime.datetime.now(datetime.timezone.utc)), flush=True)


if __name__ == "__main__":
    main()
