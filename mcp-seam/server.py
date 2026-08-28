"""Ad-hoc Seam smart-lock control for Hermes.

Lock state and capability flags, lock/unlock with the device's own
confirmation, and full access-code read/write. Reservation coupling stays out
of scope: nothing here reads a booking or issues a code because a guest
arrived — see README.
"""

import os
from datetime import datetime, timedelta, timezone

from fastmcp import FastMCP
from seam import Seam
from seam.exceptions import SeamHttpApiError

mcp = FastMCP("seam")


def _client() -> Seam:
    key = os.environ.get("SEAM_API_KEY")
    if not key:
        raise RuntimeError("SEAM_API_KEY is not set in the Hermes environment")
    # The SDK's default action-attempt wait is 5s, too short for a Z-Wave/Zigbee
    # lock behind a hub — on overrun it raises rather than reporting the (likely
    # eventual) success, which is a false failure on a physical door.
    return Seam(api_key=key, wait_for_action_attempt={"timeout": 30.0})


def _resolve(locks, name):
    """Return the one lock `name` identifies, or raise. Exact match beats substring."""
    needle = name.strip().casefold()
    known = ", ".join(sorted(lock.display_name for lock in locks)) or "none"
    if not needle:
        raise ValueError(f"Name a lock. Known locks: {known}")
    exact = [lock for lock in locks if lock.display_name.casefold() == needle]
    matches = exact or [lock for lock in locks if needle in lock.display_name.casefold()]
    if not matches:
        raise ValueError(f"No lock matching {name!r}. Known locks: {known}")
    if len(matches) > 1:
        ambiguous = ", ".join(sorted(lock.display_name for lock in matches))
        raise ValueError(f"{name!r} matches several locks: {ambiguous}. Be more specific.")
    return matches[0]


# Only the flags that change what an operator can ask for on a door. Seam
# reports thermostat and simulation flags on the same object; naming them here
# would pad every listing with capabilities no tool in this server exercises.
_CAPABILITIES = (
    ("can_remotely_lock", "lock"),
    ("can_remotely_unlock", "unlock"),
    ("can_program_online_access_codes", "online codes"),
    ("can_program_offline_access_codes", "offline codes"),
    ("can_unlock_with_code", "unlock with code"),
)


def _capabilities(lock) -> list[str]:
    return [label for field, label in _CAPABILITIES if getattr(lock, field, None)]


def _issues(subject) -> str:
    """Errors and warnings on a lock or an access code, flattened.

    Silence here is itself a claim — that nothing is wrong — so it is stated
    rather than left as an absent line the reader has to notice.
    """
    notes = [f"error: {item.get('message', item)}" for item in (subject.errors or [])]
    notes += [f"warning: {item.get('message', item)}" for item in (subject.warnings or [])]
    return "\n".join(f"  {note}" for note in notes) if notes else "  no errors or warnings"


def _describe(lock) -> str:
    # Attribute access on DeepAttrDict auto-vivifies missing keys to {}, which
    # reads as a confident "unlocked" — .get() is what actually returns None.
    level, locked, online = (lock.properties.get(k) for k in ("battery_level", "locked", "online"))
    battery = "unknown" if level is None else f"{round(level * 100)}%"
    state = "unknown" if locked is None else ("locked" if locked else "unlocked")
    connectivity = "unknown" if online is None else ("online" if online else "OFFLINE")
    line = f"{lock.display_name}: {state}, {connectivity}, battery {battery}"
    able = _capabilities(lock)
    return f"{line}, can {', '.join(able)}" if able else line


@mcp.tool
def list_locks() -> str:
    """List every smart lock with its state, connectivity, battery, and capabilities."""
    locks = _client().locks.list()
    if not locks:
        return "No locks are connected to this Seam workspace."
    return "\n".join(_describe(lock) for lock in locks)


@mcp.tool
def get_lock(name: str) -> str:
    """Full detail for one lock: device id, model, capability flags, errors and warnings.

    `name` matches the lock's display name in Seam, e.g. "Front Door".
    """
    lock = _resolve(_client().locks.list(), name)
    return "\n".join([
        _describe(lock),
        f"  device id: {lock.device_id}",
        f"  model: {lock.device_type or 'unknown'}",
        _issues(lock),
    ])


def _attempt(attempt) -> str:
    """One line for an action attempt, stating what the device itself said.

    A hub can accept a command, report success, and never hear back from the
    lock — so "Seam succeeded" and "the door moved" are different claims, and
    only the first one is ever knowable here. All three confirmation states are
    named, including the absent one: silence is not a yes.
    """
    confirmed = (attempt.result or {}).get("was_confirmed_by_device")
    line = f"{attempt.action_type} {attempt.status}, " + (
        "confirmed by the device" if confirmed is True else
        "NOT confirmed by the device" if confirmed is False else
        "no device confirmation reported")
    if attempt.error:
        line += f" — {attempt.error.get('message', attempt.error)}"
    return line


def _act(verb: str, name: str) -> str:
    client = _client()
    lock = _resolve(client.locks.list(), name)
    attempt = getattr(client.locks, f"{verb}_door")(device_id=lock.device_id)
    # No "the door is now unlocked" in any branch. That sentence is the one an
    # owner acts on, and it is a claim about a physical bolt that this API does
    # not make — the operator's August lock reports was_confirmed_by_device=False on
    # every unlock, including the ones that opened the door. Report the attempt
    # and let list_locks answer where the bolt is.
    return f"{lock.display_name}: {_attempt(attempt)} ({attempt.action_attempt_id})"


@mcp.tool
def lock_door(name: str) -> str:
    """Lock a door. `name` matches the lock's display name in Seam, e.g. "Front Door"."""
    return _act("lock", name)


@mcp.tool
def unlock_door(name: str) -> str:
    """Unlock a door. `name` matches the lock's display name in Seam, e.g. "Front Door"."""
    return _act("unlock", name)


@mcp.tool
def get_action_attempt(action_attempt_id: str) -> str:
    """Re-check one lock/unlock attempt by id — status, and whether the device confirmed it."""
    # wait_for_action_attempt=False overrides the client-wide 30s poll. The two
    # states worth re-checking an attempt for are "still pending" and "failed",
    # and polling would block on the first and raise on the second — leaving
    # this tool unable to report either of the cases it exists for.
    return _attempt(_client().action_attempts.get(
        action_attempt_id=action_attempt_id, wait_for_action_attempt=False))


@mcp.tool
def list_action_attempts(lock: str) -> str:
    """Recent lock/unlock attempts for one door. `lock` matches its display name in Seam."""
    client = _client()
    device = _resolve(client.locks.list(), lock)
    attempts = client.action_attempts.list(device_id=device.device_id)
    if not attempts:
        return f"No action attempts recorded for {device.display_name}."
    return "\n".join(f"{a.action_attempt_id}: {_attempt(a)}" for a in attempts)


def _get_code(client, access_code_id):
    """One code by id, from whichever of Seam's two collections holds it.

    They are disjoint and each 404s on the other's ids, so finding a code means
    asking both rather than betting on one.
    """
    try:
        return client.access_codes.get(access_code_id=access_code_id)
    except SeamHttpApiError as wrong_collection:
        if wrong_collection.code != "access_code_not_found":
            raise
        return client.access_codes.unmanaged.get(access_code_id=access_code_id)


def _all_codes(client, device_id):
    """Every code on a door, from both of Seam's disjoint collections.

    Seam splits access codes into ones it manages and ones that merely exist on
    the lock, and /access_codes/list returns only the first. A door whose PINs
    were all typed into the manufacturer's app therefore reads as having none —
    which is what the operator's front door, holding twenty live codes, reported.
    """
    return sorted([*client.access_codes.list(device_id=device_id),
                   *client.access_codes.unmanaged.list(device_id=device_id)],
                  key=lambda code: (code.name or "").casefold())


_LOCK_EVENTS = ("lock.locked", "lock.unlocked")

# One owner for each half of the naming contract. Four review rounds went to
# sweeping these same two sentences across runtime strings, docstrings, and the
# README, and every round left a site behind — the last one a promise the code
# beside it declined to make. A contract restated in six places drifts by
# construction, so the surfaces compose these rather than writing their own,
# and the prose that cannot interpolate them points at the tool that owns it
# instead of paraphrasing.
_NAMES_ONLY_MANAGED = "on this lock only codes Seam manages have had their entries named"
_NOT_RETROACTIVE = "unlocks already recorded stay unattributed"


def _describe_event(event, names) -> str:
    """One line for a lock event, naming a person only where Seam did.

    Seam reports `method` on every lock event but attaches an access_code_id
    only when the device says which code was entered. On this August lock it
    has only ever done so for codes Seam manages, so a keypad entry on an
    unmanaged one arrives with no id at all — worth naming, because a bare
    "unknown code" reads as a lock that cannot report who came in and sends its
    reader off after a hardware limitation that does not exist.
    """
    verb = "unlocked" if event.event_type == "lock.unlocked" else "locked"
    method = event.method or "unreported method"
    line = f"{event.occurred_at}  {verb} — {method}"
    if method != "keycode":
        return line
    if event.access_code_id is None:
        return f"{line}, unknown code — Seam did not report which code was used"
    # "currently named", because the lock is being read now and the event
    # happened then. Codes get renamed and reused between stays, so a bare name
    # here would pin an old entry on whoever holds the code today — inventing
    # exactly the false attribution this tool exists to avoid. A code deleted
    # since resolves to nothing, and its id is still the truth about which code
    # it was.
    name = names.get(event.access_code_id)
    held = f"code currently named {name}" if name else f"code {event.access_code_id}"
    return f"{line}, {held}"


@mcp.tool
def list_lock_events(lock: str, since: str | None = None, limit: int = 20) -> str:
    """Recent locks and unlocks on one door — when, by what method, and whose code.

    This is the door's own history: keypad entries and hand-turned unlocks
    included. list_action_attempts is the narrower thing — only what this API
    itself asked for. `lock` matches the display name in Seam, e.g. "Front
    Door"; `since` is ISO 8601 with a timezone, defaulting to 30 days back.
    Newest first.

    A keypad entry is named only when Seam reports which code was used. On this
    lock that has happened only for codes Seam manages, so
    convert_access_code_to_managed is what turns it on for an existing code —
    and it is not retroactive.
    """
    client = _client()
    device = _resolve(client.locks.list(), lock)
    # Seam rejects an event query that bounds neither end, and the question
    # this tool exists for ("who came in lately") rarely arrives with a date.
    window = _when("since", since) or (
        datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    events = client.events.list(device_id=device.device_id, event_types=list(_LOCK_EVENTS),
                                since=window, limit=limit)

    if not events:
        # The window is part of the finding: a door quiet for 31 days is not a
        # door that keeps no history, and the bare sentence cannot tell them apart.
        return f"No lock events for {device.display_name} since {window}."
    # Only pay for the code listing when an event actually cites one — on a door
    # whose codes are all unmanaged that is never, which is the common case.
    names = ({code.access_code_id: code.name or "unnamed"
              for code in _all_codes(client, device.device_id)}
             if any(event.access_code_id for event in events) else {})
    # Seam answers newest-first and truncates at the newest end — verified
    # against the live workspace: a March-to-August window at limit 3 returns
    # the three most recent unlocks, not the three oldest.
    return "\n".join(_describe_event(event, names) for event in events)


def _describe_code(code) -> str:
    window = (f"{code.starts_at or 'now'} → {code.ends_at or 'no expiry'}"
              if code.starts_at or code.ends_at else "always, no expiry")
    # Three states, not two. Seam leaves the field null on a code it is not
    # tracking to a device — including a live, working one — so a falsy test
    # reports a code that opens the door as "not yet on the device".
    # getattr, not attribute access: an unmanaged code is a different type that
    # carries no scheduling field at all, and its absence means the same thing
    # the null does — Seam is not tracking this code to the device.
    on_device = {True: ", on the device", False: ", not yet on the device"}.get(
        getattr(code, "is_scheduled_on_device", None), "")
    # Not trivia: on this lock, only codes Seam manages have had their entries
    # named — an unmanaged one opens the door and leaves "unknown code" behind
    # in the event feed. Whoever is deciding whether the audit trail will name
    # this person needs that on the same line as the PIN. Null is its own state
    # again — an unstated flag is not a denial.
    managed = "" if code.is_managed is not False else f", unmanaged — {_NAMES_ONLY_MANAGED}"
    return "\n".join([
        f"{code.name or 'unnamed'} ({code.access_code_id})",
        f"  code: {code.code or 'not yet assigned'}",
        f"  status: {code.status}{on_device}{managed}",
        f"  window: {window}",
        _issues(code),
    ])


@mcp.tool
def list_access_codes(lock: str) -> str:
    """Every access code on one door: PIN, name, status, and the window it is good for.

    `lock` matches the lock's display name in Seam, e.g. "Front Door". Covers
    codes Seam manages and codes that only exist on the lock. The access code
    ids reported here are what get_access_code, delete_access_code, and
    convert_access_code_to_managed take. An unmanaged code has to be converted
    before update_access_code can change it — Seam reads an unmanaged code but
    its update endpoint owns the managed collection only.
    """
    client = _client()
    device = _resolve(client.locks.list(), lock)
    codes = _all_codes(client, device.device_id)
    if not codes:
        return f"No access codes are set on {device.display_name}."
    return "\n".join(_describe_code(code) for code in codes)


@mcp.tool
def get_access_code(access_code_id: str) -> str:
    """One access code by id — PIN, status, window, and any errors or warnings."""
    return _describe_code(_get_code(_client(), access_code_id))


def _when(label: str, value):
    """Accept only a timestamp whose timezone is stated. Guessing one moves a door code."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise ValueError(
            f"{label}={value!r} is not an ISO 8601 timestamp with a timezone. "
            'Use e.g. "2026-09-01T15:00:00Z" or "2026-09-01T08:00:00-07:00".')
    return value


@mcp.tool
def create_access_code(lock: str, name: str, code: str | None = None,
                       starts_at: str | None = None, ends_at: str | None = None) -> str:
    """Program a new access code on a door.

    `lock` is the lock's display name, e.g. "Front Door". `name` is who the code
    is for — it is what a later reader sees, so name the person or the stay.
    `code` is the PIN; leave it out and Seam picks one the device accepts.
    `starts_at`/`ends_at` are ISO 8601 with a timezone (e.g. "2026-09-01T15:00:00Z");
    leave both out for a code that never expires.
    """
    client = _client()
    device = _resolve(client.locks.list(), lock)
    created = client.access_codes.create(
        device_id=device.device_id, name=name, code=code,
        starts_at=_when("starts_at", starts_at), ends_at=_when("ends_at", ends_at))
    # "Accepted", not "programmed". Seam returns as soon as the control plane
    # takes the code — a real create comes back at status "setting"/"unset",
    # still on its way to the lock. The status line below is the live answer.
    return f"Seam accepted this code for {device.display_name}:\n{_describe_code(created)}"


@mcp.tool
def update_access_code(access_code_id: str, name: str | None = None, code: str | None = None,
                       starts_at: str | None = None, ends_at: str | None = None) -> str:
    """Change an existing access code — its PIN, its name, or its start/end window.

    `access_code_id` comes from list_access_codes. Only the fields you name are
    changed; the rest are left alone. Timestamps are ISO 8601 with a timezone.
    """
    changes = {"name": name, "code": code,
               "starts_at": _when("starts_at", starts_at), "ends_at": _when("ends_at", ends_at)}
    changes = {field: value for field, value in changes.items() if value is not None}
    if not changes:
        raise ValueError("nothing to change — name at least one of name, code, starts_at, ends_at")
    client = _client()
    # Read before write. Seam's update endpoint owns the managed collection
    # only, and on this door every code is unmanaged — so the common path here
    # is a refusal, and it should name the way out instead of a bare 404 landing
    # after the change was already attempted.
    code = _get_code(client, access_code_id)
    if code.is_managed is False:
        raise ValueError(
            f"{code.name or 'That code'} is unmanaged, so Seam can report its PIN and "
            "window but not change them. convert_access_code_to_managed takes it over first.")
    client.access_codes.update(access_code_id=access_code_id, **changes)
    return f"Change accepted:\n{_describe_code(client.access_codes.get(access_code_id=access_code_id))}"


@mcp.tool
def convert_access_code_to_managed(access_code_id: str) -> str:
    """Bring a code that already exists on the lock under Seam's management.

    Takes over a code created in the lock's own app, so that Seam manages it —
    which is what list_lock_events' description says naming has depended on
    here. It keeps the PIN, the name, and the code's place on the device, so
    nobody loses access over it, and it changes nothing about entries already
    recorded. `access_code_id` comes from list_access_codes.
    """
    client = _client()
    # External modification stays allowed: these are codes an owner types into
    # the manufacturer's app and has every reason to keep editing there. A code
    # Seam refuses to let them change is a worse door than an unnamed unlock.
    client.access_codes.unmanaged.convert_to_managed(
        access_code_id=access_code_id, is_external_modification_allowed=True)
    # Seam cannot re-derive which code opened the door for an event it already
    # wrote down as unknown, and the person asking is usually asking about the
    # past — so the limit is stated before they go looking for it.
    return (f"Converted. Entries by this code should be named from here on; "
            f"{_NOT_RETROACTIVE}.\n"
            f"{_describe_code(client.access_codes.get(access_code_id=access_code_id))}")


@mcp.tool
def delete_access_code(access_code_id: str) -> str:
    """Remove an access code from its door. `access_code_id` comes from list_access_codes."""
    client = _client()
    # Read first: the confirmation has to name what was removed, and after the
    # delete there is nothing left to name it with. It also settles which
    # collection to delete from — the two do not accept each other's ids.
    doomed = _get_code(client, access_code_id)
    if doomed.is_managed is False:
        # The unmanaged collection has its own delete — seam 1.209.0 exposes
        # access_codes.unmanaged.delete(*, access_code_id: str) — so removing an
        # app-created code needs no conversion first.
        client.access_codes.unmanaged.delete(access_code_id=access_code_id)
    else:
        client.access_codes.delete(access_code_id=access_code_id)
    return (f"Removal accepted for {doomed.name or 'unnamed'} ({access_code_id}). "
            "Seam has taken it; list_access_codes says when the lock has.")


if __name__ == "__main__":
    mcp.run()
