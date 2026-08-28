import asyncio
import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from seam.exceptions import SeamHttpApiError, SeamHttpInvalidInputError
from seam.resources.access_code import AccessCode
from seam.resources.action_attempt import ActionAttempt
from seam.resources.device import Device
from seam.resources.unmanaged_access_code import UnmanagedAccessCode
from seam.resources.seam_event import SeamEvent

_OMIT = object()

def lock(name, device_id, locked=True, online=True, battery_level=0.87, **fields):
    props = {}
    for key, value in (("locked", locked), ("online", online), ("battery_level", battery_level)):
        if value is not _OMIT:
            props[key] = value
    return Device.from_dict({"display_name": name, "device_id": device_id,
                             "properties": props, **fields})

LOCKS = [lock("Front Door", "dev_front", can_remotely_lock=True, can_remotely_unlock=True,
              can_program_online_access_codes=True, device_type="august_lock",
              warnings=[{"warning_code": "device_has_flaky_connection",
                         "message": "Reconnecting often"}]),
         lock("Side Door", "dev_side", locked=False),
         lock("Side Gate", "dev_side_gate", online=False, battery_level=None),
         lock("Garage", "dev_garage", locked=_OMIT, online=_OMIT, battery_level=_OMIT)]

def attempt(action_type="UNLOCK_DOOR", status="success", confirmed=True, error=None,
            action_attempt_id="aa_1"):
    return ActionAttempt.from_dict({
        "action_attempt_id": action_attempt_id, "action_type": action_type,
        "status": status, "error": error,
        "result": None if confirmed is None else {"was_confirmed_by_device": confirmed}})

def code(access_code_id="ac_1", name="Cleaner", value="1234", status="set",
         starts_at=None, ends_at=None, scheduled=True, **fields):
    return AccessCode.from_dict({
        "access_code_id": access_code_id, "name": name, "code": value, "status": status,
        "starts_at": starts_at, "ends_at": ends_at,
        "is_scheduled_on_device": scheduled, "device_id": "dev_front", **fields})

class FakeLocks:
    def __init__(self, locks):
        self.locks, self.calls, self.attempt = locks, [], None
    def list(self): return self.locks
    def _door(self, verb, device_id):
        self.calls.append((verb, device_id))
        return self.attempt
    def lock_door(self, device_id): return self._door("lock", device_id)
    def unlock_door(self, device_id): return self._door("unlock", device_id)

class FakeActionAttempts:
    def __init__(self):
        self.attempts, self.listed_for, self.waited = [attempt()], None, None
    def get(self, action_attempt_id, wait_for_action_attempt=None):
        self.waited = wait_for_action_attempt
        found = [a for a in self.attempts if a.action_attempt_id == action_attempt_id]
        if not found:
            raise RuntimeError(f"no action attempt {action_attempt_id}")
        return found[0]
    def list(self, device_id):
        self.listed_for = device_id
        return self.attempts

def event(event_type="lock.unlocked", occurred_at="2026-08-19T18:22:56.000Z",
          method="keycode", **fields):
    return SeamEvent.from_dict({"event_id": "ev_1", "device_id": "dev_front",
                                "event_type": event_type, "occurred_at": occurred_at,
                                "method": method, **fields})

def unmanaged_code(access_code_id, name, value, status="set", **fields):
    """Seam's unmanaged codes are a different type, not just a flagged AccessCode.

    UnmanagedAccessCode carries no is_scheduled_on_device and no
    is_external_modification_allowed, among others — so a fake that reuses
    AccessCode here reports a shape the real collection never returns, and any
    attribute the server reads off it passes in tests and raises on the door.
    """
    return UnmanagedAccessCode.from_dict({
        "access_code_id": access_code_id, "name": name, "code": value,
        "status": status, "is_managed": False, "device_id": "dev_front", **fields})

def wrong_namespace(which):
    """What Seam raises when an id is looked up in the other collection."""
    return SeamHttpApiError({"type": "access_code_not_found",
                             "message": f"This access code is {which}."}, 404, "req_1")

class FakeUnmanagedAccessCodes:
    """Seam's second, disjoint access-code collection.

    Codes typed into the lock's own app land here and are absent from
    /access_codes/list entirely — a fake that answers both reads from one
    collection cannot reproduce the door this server actually talks to.
    """
    def __init__(self, codes, managed=None):
        self.codes, self.converted, self.listed_for = codes, None, None
        self.deleted, self.managed = None, managed
    def list(self, device_id):
        self.listed_for = device_id
        return self.codes
    def get(self, access_code_id):
        found = [c for c in self.codes if c.access_code_id == access_code_id]
        if not found:
            raise wrong_namespace("managed")
        return found[0]
    def delete(self, **kwargs): self.deleted = kwargs
    def convert_to_managed(self, **kwargs):
        self.converted = kwargs
        # Conversion moves the code between collections rather than copying it,
        # so a re-read aimed at the old one now raises — which is the mistake
        # worth catching here.
        moved = self.get(kwargs["access_code_id"])
        self.codes = [c for c in self.codes if c is not moved]
        self.managed.append(moved)

class FakeEvents:
    def __init__(self):
        self.events, self.query = [], None
    def list(self, **kwargs):
        # Seam rejects an unbounded event query outright, and a fake that
        # answers one lets a tool ship that cannot run against the real API.
        if kwargs.get("since") is None and kwargs.get("between") is None:
            raise SeamHttpInvalidInputError(
                {"type": "invalid_input", "message": "Must specify either since or between"},
                400, "req_1")
        self.query = kwargs
        # Seam answers newest-first and truncates at the newest end — verified
        # against the live workspace, and modelled here so a test cannot pass
        # against an ordering the API does not produce.
        newest_first = sorted(self.events, key=lambda e: e.occurred_at or "", reverse=True)
        return newest_first[:kwargs.get("limit")]

class FakeAccessCodes:
    def __init__(self):
        self.codes = [code(),
                      code("ac_2", "Guest — Chen", "9087", "setting",
                           starts_at="2026-08-07T15:00:00Z", ends_at="2026-08-10T18:00:00Z",
                           scheduled=False)]
        self.unmanaged = FakeUnmanagedAccessCodes(
            [unmanaged_code("ac_gianna", "Gianna Speicher", "778899"),
             unmanaged_code("ac_handyman", "Eddie Boudreau", "334455")],
            managed=self.codes)
        self.created = self.updated = self.deleted = self.listed_for = None
    def list(self, device_id):
        self.listed_for = device_id
        return self.codes
    def get(self, access_code_id):
        found = [c for c in self.codes if c.access_code_id == access_code_id]
        if not found:
            raise wrong_namespace("unmanaged")
        return found[0]
    def create(self, **kwargs):
        self.created = kwargs
        return code("ac_new", kwargs.get("name"), kwargs.get("code") or "5150",
                    starts_at=kwargs.get("starts_at"), ends_at=kwargs.get("ends_at"))
    def update(self, **kwargs): self.updated = kwargs
    def delete(self, **kwargs): self.deleted = kwargs

class FakeSeam:
    instance = None
    def __init__(self, api_key=None, wait_for_action_attempt=None):
        self.api_key, self.wait_for_action_attempt = api_key, wait_for_action_attempt
        self.locks, self.action_attempts, self.access_codes, self.events = (
            FakeLocks(LOCKS), FakeActionAttempts(), FakeAccessCodes(), FakeEvents())
        FakeSeam.instance = self

spec = importlib.util.spec_from_file_location(
    "seam_server", Path(__file__).resolve().parents[1] / "mcp-seam/server.py")
assert spec and spec.loader
server = importlib.util.module_from_spec(spec)
sys.modules["seam_server"] = server
spec.loader.exec_module(server)

@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("SEAM_API_KEY", "seam_test_key")
    monkeypatch.setattr(server, "Seam", FakeSeam)
    FakeSeam.instance = None

@pytest.fixture
def fake_client(monkeypatch):
    """A Seam whose routes a test can arrange before the tool reaches them."""
    fake = FakeSeam()
    monkeypatch.setattr(server, "_client", lambda: fake)
    return fake

def test_missing_api_key_is_reported_not_silently_ignored(monkeypatch):
    monkeypatch.delenv("SEAM_API_KEY")
    with pytest.raises(RuntimeError, match="SEAM_API_KEY"):
        server._client()

def test_client_extends_the_action_attempt_wait_past_the_sdk_default():
    client = server._client()
    assert client.wait_for_action_attempt == {"timeout": 30.0}

def test_status_listing_reports_state_connectivity_battery_and_capabilities():
    reported = server.list_locks()
    assert "Front Door: locked, online, battery 87%, can lock, unlock, online codes" in reported
    assert "Side Door: unlocked, online, battery 87%" in reported
    assert "Side Gate: locked, OFFLINE, battery unknown" in reported
    # A lock whose flags Seam did not report is not described as capable of anything.
    assert "Garage: unknown, unknown, battery unknown" in reported
    assert "Garage: unknown, unknown, battery unknown, can" not in reported

def test_empty_workspace_says_so_rather_than_returning_nothing(monkeypatch):
    monkeypatch.setattr(server, "_client", lambda: SimpleNamespace(locks=FakeLocks([])))
    assert "No locks" in server.list_locks()

@pytest.mark.parametrize("name,expected", [
    ("Front Door", "dev_front"),      # exact
    ("front door", "dev_front"),      # case-insensitive exact
    ("front", "dev_front"),           # unique substring
    ("Side Gate", "dev_side_gate"),   # exact wins where substring is ambiguous
])
def test_locks_resolve_by_display_name(name, expected):
    assert server._resolve(LOCKS, name).device_id == expected

@pytest.mark.parametrize("locks,name,message", [
    (LOCKS, "carport", "No lock matching"),  # nothing matches
    (LOCKS, "Side", "matches several"),      # matches two locks
    ([], "carport", "Known locks: none"),    # nothing to match against at all
    ([lock("Only Lock", "dev_only")], "", "Name a lock"),  # blank name must not match by default
    ([lock("Only Lock", "dev_only")], "   ", "Name a lock"),  # whitespace-only is still blank
])
def test_unresolvable_names_raise_instead_of_guessing(locks, name, message):
    with pytest.raises(ValueError, match=message):
        server._resolve(locks, name)

@pytest.mark.parametrize("tool,verb,name,door,device_id,confirmed,label", [
    (server.lock_door, "lock", "front", "Front Door", "dev_front", True,
     "confirmed by the device"),
    (server.unlock_door, "unlock", "Side Door", "Side Door", "dev_side", True,
     "confirmed by the device"),
    (server.unlock_door, "unlock", "Side Door", "Side Door", "dev_side", False,
     "NOT confirmed by the device"),
    # Absent is its own state. Silence is not a yes — and it is what a lock
    # Seam does not track confirmation for reports on every single action.
    (server.unlock_door, "unlock", "Side Door", "Side Door", "dev_side", None,
     "no device confirmation reported"),
])
def test_door_actions_report_the_attempt_without_claiming_the_bolt_moved(
        fake_client, tool, verb, name, door, device_id, confirmed, label):
    fake_client.locks.attempt = attempt(action_type=f"{verb.upper()}_DOOR", confirmed=confirmed)
    # Whole reply, not a substring: what this asserts is the ABSENCE of a claim
    # about a physical bolt, in every confirmation state, and a substring match
    # would pass with "is now unlocked" sitting right beside it.
    assert tool(name) == f"{door}: {verb.upper()}_DOOR success, {label} (aa_1)"
    assert fake_client.locks.calls == [(verb, device_id)]


def test_an_action_attempt_can_be_re_checked_by_id():
    assert "UNLOCK_DOOR success" in server.get_action_attempt("aa_1")


def test_re_checking_an_attempt_reports_a_failure_rather_than_re_raising_it(fake_client):
    """The two states worth a re-check are pending and failed.

    The client-wide 30s `wait_for_action_attempt` would block on the first and
    raise on the second, so this tool has to opt out of it — otherwise it
    cannot report either of the cases it exists for.
    """
    fake_client.action_attempts.attempts = [attempt(status="error", confirmed=None,
                                                    error={"message": "device disconnected"})]
    assert "device disconnected" in server.get_action_attempt("aa_1")
    assert fake_client.action_attempts.waited is False


def test_recent_attempts_can_be_listed_for_one_door():
    assert "UNLOCK_DOOR" in server.list_action_attempts("front")
    assert FakeSeam.instance.action_attempts.listed_for == "dev_front"


def test_a_door_with_no_recent_attempts_says_so(fake_client):
    fake_client.action_attempts.attempts = []
    assert "No action attempts" in server.list_action_attempts("front")


def test_lock_detail_reports_the_device_id_model_and_what_is_wrong_with_it():
    detail = server.get_lock("front")
    assert "dev_front" in detail
    assert "august_lock" in detail
    assert "Reconnecting often" in detail


def test_lock_detail_says_so_when_there_is_nothing_wrong():
    assert "no errors or warnings" in server.get_lock("Side Door")


@pytest.mark.parametrize("tool", [
    server.get_lock, server.list_access_codes, server.list_action_attempts,
])
def test_every_lock_scoped_tool_refuses_an_ambiguous_name(tool):
    with pytest.raises(ValueError, match="matches several"):
        tool("Side")


def test_listing_codes_reports_the_pin_its_window_and_whether_it_reached_the_lock():
    reported = server.list_access_codes("front")
    assert FakeSeam.instance.access_codes.listed_for == "dev_front"
    assert "Cleaner" in reported and "1234" in reported
    assert "2026-08-07T15:00:00Z → 2026-08-10T18:00:00Z" in reported
    # Both states pinned: "not yet on the device" contains "on the device", so
    # only asserting the negative leaves the positive branch free to vanish.
    assert "status: set, on the device" in reported
    assert "status: setting, not yet on the device" in reported
    # A code with no window is permanent, not expired.
    assert "always, no expiry" in reported
    # The id every mutating tool takes, so it must be in the read that precedes them.
    assert "ac_2" in reported


def test_a_working_code_is_not_described_as_missing_from_the_lock(fake_client):
    """Seam leaves is_scheduled_on_device null on codes it does not track there.

    The operator's live front-door code is exactly that shape — status `set`, scheduling
    unreported — so a two-state read describes the code that opens their door as
    "not yet on the device".
    """
    fake_client.access_codes.codes = [code(scheduled=None)]
    # The one code is the whole workspace here: the assertion below is an
    # absence, and any other code in the listing could supply the phrase.
    fake_client.access_codes.unmanaged.codes = []
    reported = server.list_access_codes("front")
    assert "status: set" in reported
    assert "on the device" not in reported


def test_a_lock_with_no_codes_says_so(fake_client):
    fake_client.access_codes.codes = []
    fake_client.access_codes.unmanaged.codes = []
    assert "No access codes" in server.list_access_codes("front")


def test_one_code_can_be_fetched_by_id():
    assert "1234" in server.get_access_code("ac_1")


def test_a_code_is_created_on_the_named_door_and_the_result_is_reported_back():
    reported = server.create_access_code("front", "Guest — Ruiz",
                                         starts_at="2026-09-01T15:00:00Z",
                                         ends_at="2026-09-04T18:00:00Z")
    created = FakeSeam.instance.access_codes.created
    assert created["device_id"] == "dev_front"
    assert created["name"] == "Guest — Ruiz"
    assert created["starts_at"] == "2026-09-01T15:00:00Z"
    assert created["ends_at"] == "2026-09-04T18:00:00Z"
    # Seam picks a PIN when none is given; the operator has to be told which.
    assert "5150" in reported
    # Seam accepted it — the door has not, and will not have by the time this
    # returns (a real create comes back at status "setting").
    assert "Front Door accepted" not in reported


def test_a_requested_pin_is_passed_through_rather_than_silently_replaced():
    server.create_access_code("front", "Handyman", code="4821")
    assert FakeSeam.instance.access_codes.created["code"] == "4821"


@pytest.mark.parametrize("field,value", [
    ("starts_at", "2026-09-01 15:00"),        # no offset
    ("ends_at", "2026-09-04T18:00:00"),       # naive — the timezone would be a guess
    ("starts_at", "next Friday"),             # not a timestamp at all
])
def test_an_ambiguous_time_is_refused_rather_than_assigned_a_timezone(field, value):
    with pytest.raises(ValueError, match="ISO 8601"):
        server.create_access_code("front", "Guest", **{field: value})
    assert FakeSeam.instance.access_codes.created is None


@pytest.mark.parametrize("value", ["2026-09-01T15:00:00Z", "2026-09-01T08:00:00-07:00"])
def test_an_unambiguous_time_is_accepted_in_either_spelling(value):
    reported = server.create_access_code("front", "Guest", starts_at=value)
    assert FakeSeam.instance.access_codes.created["starts_at"] == value
    # A code dated at one end only has no expiry — not an expiry of "None".
    assert f"{value} → no expiry" in reported


def test_creating_on_an_ambiguous_door_writes_nothing():
    with pytest.raises(ValueError, match="matches several"):
        server.create_access_code("Side", "Guest")
    assert FakeSeam.instance.access_codes.created is None


def test_extending_a_code_sends_only_the_fields_that_were_named():
    server.update_access_code("ac_1", ends_at="2026-09-09T18:00:00Z")
    assert FakeSeam.instance.access_codes.updated == {
        "access_code_id": "ac_1", "ends_at": "2026-09-09T18:00:00Z"}


def test_an_update_that_names_nothing_is_refused_rather_than_sent_as_a_no_op():
    with pytest.raises(ValueError, match="nothing to change"):
        server.update_access_code("ac_1")
    # Refused before a client was even built, so Seam was never reached.
    assert FakeSeam.instance is None


def test_deleting_a_code_names_the_code_it_removed():
    reported = server.delete_access_code("ac_1")
    assert FakeSeam.instance.access_codes.deleted == {"access_code_id": "ac_1"}
    assert "Cleaner" in reported

def test_unlock_refuses_an_ambiguous_door_without_touching_any_lock():
    with pytest.raises(ValueError, match="matches several"):
        server.unlock_door("Side")
    assert FakeSeam.instance.locks.calls == []

def test_a_failed_action_attempt_propagates_rather_than_reporting_success(fake_client, monkeypatch):
    def explode(device_id):
        raise RuntimeError("action attempt failed: device unreachable")
    monkeypatch.setattr(fake_client.locks, "unlock_door", explode)
    with pytest.raises(RuntimeError, match="device unreachable"):
        server.unlock_door("front")

def test_every_registered_tool_is_on_the_allowlist_the_gateway_reads():
    """Registration is only half the contract.

    `tools.include` under `mcp_servers.seam` in the operator's config.yaml is
    what decides which of these reach the agent, and it is read once at gateway
    start. A tool added here and forgotten there is a capability that ships,
    passes its tests, and is unreachable in chat — which is exactly the failure
    that is invisible until someone asks the agent to do the thing.
    """
    config = (Path(__file__).resolve().parents[1] / "runtime/config.yaml").read_text()
    # Sliced to the seam block: `include:` appears under hostex as well.
    seam_block = config.split("\n  seam:\n")[1]
    listed = re.search(r"include: \[(.*?)\]", seam_block, re.S).group(1)
    allowlisted = {name.strip() for name in listed.split(",")}
    registered = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert allowlisted == registered


def test_listing_codes_includes_the_unmanaged_ones_the_door_actually_holds(fake_client):
    """The bug this file exists to pin: every code on the operator's door is unmanaged.

    /access_codes/list returns only the managed collection, so on the real lock
    it came back empty and the tool answered "No access codes are set" about a
    door holding twenty live PINs.
    """
    reported = server.list_access_codes("front")
    assert "Gianna Speicher" in reported
    assert "Eddie Boudreau" in reported
    assert "Cleaner" in reported
    assert fake_client.access_codes.unmanaged.listed_for == "dev_front"


def test_a_door_whose_codes_are_all_unmanaged_is_never_reported_as_having_none(fake_client):
    fake_client.access_codes.codes = []
    reported = server.list_access_codes("front")
    assert "No access codes" not in reported
    assert "Gianna Speicher" in reported


def test_an_unmanaged_code_is_marked_because_seam_will_not_name_it_in_events(fake_client):
    reported = server.list_access_codes("front")
    assert "Gianna Speicher" in reported
    assert f"unmanaged — {server._NAMES_ONLY_MANAGED}" in reported
    # The managed code carries no such warning, and a code whose managed-ness
    # Seam did not report is not accused of being unmanaged either.
    assert reported.count(f"unmanaged — {server._NAMES_ONLY_MANAGED}") == 2


def test_getting_an_unmanaged_code_by_id_falls_back_instead_of_raising(fake_client):
    assert "Gianna Speicher" in server.get_access_code("ac_gianna")


def test_an_unmanaged_code_is_described_without_the_fields_its_type_lacks(fake_client):
    """UnmanagedAccessCode has no is_scheduled_on_device at all.

    Reading it as though it were an AccessCode raises AttributeError on the
    real door while every fixture built from the wrong type passes.
    """
    reported = server.get_access_code("ac_gianna")
    assert "status: set" in reported
    assert "on the device" not in reported


def test_getting_a_managed_code_by_id_still_works_without_a_second_call(fake_client):
    assert "Cleaner" in server.get_access_code("ac_1")


def test_an_id_in_neither_collection_raises_rather_than_reporting_an_empty_code(fake_client):
    with pytest.raises(SeamHttpApiError):
        server.get_access_code("ac_nonexistent")


def test_a_failure_that_is_not_a_namespace_miss_surfaces_instead_of_falling_back(
        fake_client, monkeypatch):
    """An expired key or an outage is the answer, not a reason to try the other collection.

    Falling through reports whatever the second call says, so a credential
    problem arrives wearing the second endpoint's error message.
    """
    def expired(access_code_id):
        raise SeamHttpApiError({"type": "unauthorized", "message": "bad key"}, 401, "req_1")
    monkeypatch.setattr(fake_client.access_codes, "get", expired)
    with pytest.raises(SeamHttpApiError, match="bad key"):
        server.get_access_code("ac_gianna")


# One arrange/act, six contracts. Two carry more than their assertion: a bare
# "unknown code" reads as a lock that cannot report who entered, when what it
# means is that Seam was not told which code — which is fixable; and a resolved
# name is
# rendered as *currently* named, because codes get renamed and reused between
# stays, and the lock is read now about an entry that happened then.
@pytest.mark.parametrize("fields,present,absent", [
    ({}, ("keycode", "unknown code", "Seam did not report which code was used"), ()),
    ({"access_code_id": "ac_1"}, ("code currently named Cleaner",), ("unknown code",)),
    # Both halves of _all_codes, deliberately: every code on the real door is
    # unmanaged, so a name lookup narrowed to the managed collection would
    # render each keypad entry as a bare id — green suite, useless output.
    ({"access_code_id": "ac_gianna"},
     ("code currently named Gianna Speicher",), ("unknown code",)),
    ({"method": "manual"}, ("manual",), ("code",)),
    ({"access_code_id": "ac_long_gone"}, ("code ac_long_gone",), ("unknown code",)),
    ({"event_type": "lock.locked", "method": "manual"}, ("locked",), ()),
])
def test_lock_event_descriptions(fake_client, fields, present, absent):
    fake_client.events.events = [event(**fields)]
    reported = server.list_lock_events("front")
    assert all(text in reported for text in present)
    assert all(text not in reported for text in absent)


def test_events_are_scoped_to_the_named_door_and_to_lock_events(fake_client):
    fake_client.events.events = [event()]
    server.list_lock_events("front", since="2026-08-01T00:00:00Z", limit=5)
    query = fake_client.events.query
    assert query["device_id"] == "dev_front"
    assert set(query["event_types"]) == {"lock.locked", "lock.unlocked"}
    assert query["since"] == "2026-08-01T00:00:00Z"
    assert query["limit"] == 5


def test_events_are_reported_newest_first(fake_client):
    """End to end through a fake that orders the way Seam was observed to.

    Asserted on the reply rather than on a sort call, so it stays true of the
    tool's contract however the ordering is arrived at.
    """
    fake_client.events.events = [event(occurred_at="2026-08-19T09:00:00.000Z", method="manual"),
                                 event(occurred_at="2026-08-19T18:00:00.000Z", method="manual")]
    assert server.list_lock_events("front").index("2026-08-19T18:00:00.000Z") == 0


def test_the_limit_keeps_the_newest_events_rather_than_the_oldest(fake_client):
    fake_client.events.events = [event(occurred_at="2026-08-01T09:00:00.000Z", method="manual"),
                                 event(occurred_at="2026-08-19T18:00:00.000Z", method="manual")]
    reported = server.list_lock_events("front", limit=1)
    assert "2026-08-19T18:00:00.000Z" in reported
    assert "2026-08-01T09:00:00.000Z" not in reported


def test_a_since_without_a_timezone_is_refused_rather_than_guessed(fake_client):
    with pytest.raises(ValueError, match="ISO 8601"):
        server.list_lock_events("front", since="2026-08-01")


def test_a_window_is_supplied_when_the_caller_names_none(fake_client):
    """Seam refuses an event query with neither `since` nor `between`.

    The tool is asked "who unlocked the door lately" far more often than it is
    handed a date, so the unbounded call is the common one and it has to work.
    """
    fake_client.events.events = [event()]
    server.list_lock_events("front")
    assert fake_client.events.query["since"].endswith("+00:00")


def test_a_quiet_door_is_reported_as_quiet_for_the_window_not_for_all_time(fake_client):
    """The query is bounded, so the absence it found is bounded too.

    "No lock events recorded" over a 30-day default reads as a lock that keeps
    no history at all — the same misread the unknown-code line exists to stop.
    """
    fake_client.events.events = []
    reported = server.list_lock_events("front", since="2026-08-01T00:00:00Z")
    assert "No lock events" in reported
    assert "2026-08-01T00:00:00Z" in reported


def test_the_code_listing_is_not_fetched_when_no_event_cites_one(fake_client):
    """The common case is a door whose codes are all unmanaged.

    Every event then arrives with no access_code_id, and there is nothing for a
    name lookup to resolve — so it is not paid for.
    """
    fake_client.events.events = [event(), event(method="manual")]
    server.list_lock_events("front")
    assert fake_client.access_codes.unmanaged.listed_for is None


def test_conversion_moves_the_code_and_states_both_limits(fake_client):
    """One state transition, one reply, one owner.

    Seam cannot re-derive which code opened the door for a past event, so an
    operator converting a code to answer "when did Gianna last come in" still
    sees "unknown code" over the whole history. And the forward half is a hedge,
    not a guarantee — it rests on what this lock has been observed to do, which
    the rest of the file declines to treat as Seam's contract, and it is issued
    after a change the operator cannot undo.
    """
    reply = server.convert_access_code_to_managed("ac_gianna")
    assert fake_client.access_codes.unmanaged.converted == {
        "access_code_id": "ac_gianna", "is_external_modification_allowed": True}
    assert "Gianna Speicher" in reply
    # Against the module's own constant, so the sentence cannot drift away from
    # the one place that owns it without this failing.
    assert server._NOT_RETROACTIVE in reply
    assert "should be named" in reply
    assert "will name" not in reply


def test_deleting_an_unmanaged_code_reaches_the_collection_that_holds_it(fake_client):
    """Every code on the real door is unmanaged, so this is the ordinary case.

    The managed endpoint 404s on these ids, which made the tool unable to
    remove any code the door actually had.
    """
    reply = server.delete_access_code("ac_gianna")
    assert fake_client.access_codes.unmanaged.deleted == {"access_code_id": "ac_gianna"}
    assert fake_client.access_codes.deleted is None
    assert "Gianna Speicher" in reply


def test_updating_an_unmanaged_code_is_refused_before_anything_is_written(fake_client):
    """Seam's update endpoint owns the managed collection only.

    Letting the call through wrote nothing and then failed on the re-read,
    which reads as "the change landed but could not be confirmed".
    """
    with pytest.raises(ValueError, match="convert_access_code_to_managed"):
        server.update_access_code("ac_gianna", ends_at="2026-09-09T18:00:00Z")
    assert fake_client.access_codes.updated is None
