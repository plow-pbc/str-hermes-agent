"""send_after: a scheduled job's message goes out only onto the thread it read."""
from __future__ import annotations

import io
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "bin"))
import plow_api  # noqa: E402

ENV = {"PLOW_API_BASE": "https://plow.test", "PLOW_AGENT_TOKEN": "tok"}


@pytest.fixture
def thread(monkeypatch):
    """A chat whose newest-first messages the test sets; returns (messages, sent bodies)."""
    messages, sent = [], []
    monkeypatch.setattr(plow_api, "messages", lambda env, chat, limit=100: messages[:limit])

    def post(request, timeout):
        sent.append(json.loads(request.data)["body"])
        return io.BytesIO(json.dumps({"uid": "msg_new"}).encode())
    monkeypatch.setattr(plow_api.hostex_api.OPENER, "open", post)
    return messages, sent


def msg(uid, body="", direction="inbound"):
    return {"uid": uid, "direction": direction, "body": body, "sender": {"name": "Gianna"},
            "created_at": "2026-11-06T22:00:00Z", "status": "received"}


@pytest.mark.parametrize("history, after, body", [
    ([msg("msg_2"), msg("msg_1")], "msg_2", "Is the cabin wrapped up?"),
    ([], "", "Hi"),
], ids=["populated-thread", "empty-thread"])
def test_sends_when_the_thread_has_not_moved(thread, history, after, body):
    messages, sent = thread
    messages[:] = history
    assert plow_api.send_after(ENV, "cht_x", body, after) == {"sent": True, "message_id": "msg_new"}
    assert sent == [body]


def test_refuses_and_returns_what_arrived_since(thread):
    """The 4 PM ping: the cleaner wrote after the job read the thread."""
    messages, sent = thread
    messages[:] = [msg("msg_4", "All done, locked up"), msg("msg_3", "Almost"), msg("msg_2")]
    result = plow_api.send_after(ENV, "cht_x", "Is the cabin wrapped up?", "msg_2")
    assert result["sent"] is False and sent == []
    assert [m["body"] for m in result["newer"]] == ["Almost", "All done, locked up"]


def test_a_repeat_hold_is_not_a_send(thread, monkeypatch):
    """Plow answers a held repeat with 200 and the earlier uid; a SENT on that
    would record a message nobody received."""
    messages, _ = thread
    messages[:] = [msg("msg_2")]
    held = {"uid": "msg_1", "held": True, "reason": "repeated_agent_message"}
    monkeypatch.setattr(plow_api.hostex_api.OPENER, "open",
                        lambda request, timeout: io.BytesIO(json.dumps(held).encode()))
    assert plow_api.send_after(ENV, "cht_x", "Hi", "msg_2") == {
        "sent": False, "held": "repeated_agent_message"}
