import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "handset_message", Path(__file__).resolve().parents[1] / "bin/handset-message.py")
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules["handset_message"] = mod
spec.loader.exec_module(mod)


def typedstream(text):
    """The shape macOS actually stores, captured from a real row in this thread."""
    payload = text.encode()
    if len(payload) < 0x81:
        length = bytes([len(payload)])
    else:
        length = b"\x81" + len(payload).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12"
            b"NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84"
            b"\x08NSString\x01\x94\x84\x01+" + length + payload)


@pytest.mark.parametrize("text,blob,expected", [
    # Modern macOS leaves `text` NULL and puts the body in the typedstream. A
    # reader that trusts `text` reports no reply against a healthy system —
    # which is how a false timeout already shipped once here.
    (None, typedstream("PONG 5eaad4"), "PONG 5eaad4"),
    # Past 0x80 bytes the length becomes 0x81 plus a little-endian uint16, so a
    # single-byte read truncates every long answer — the tool-question shape.
    (None, typedstream("x" * 300), "x" * 300),
    ("already plain", None, "already plain"),          # older rows still carry text
    (None, None, ""),                                  # attachment-only row
    (None, b"no marker here", ""),
])
def test_the_body_is_read_from_wherever_macos_put_it(text, blob, expected):
    assert mod._body(text, blob) == expected


@pytest.mark.parametrize("rows,expected", [
    ([(3, 0, "PONG 5f3a", None), (2, 0, "Reminder: cleaner arrives at 11.", None),
      (1, 1, "ping 5f3a", None)], "PONG 5f3a"),
    # Our own send carries the nonce — it is the message that asked for it — so
    # without the is_from_me check the probe answers itself and passes while the
    # agent never replied.
    ([(1, 1, "ping 5f3a\n\n(Include 5f3a verbatim in your reply.)", None)], None),
    # Adjacency is not correlation: a cron delivery landing mid-turn is inbound
    # and newer, and answers a different question.
    ([(2, 0, "Wiki digest: 29 new pages", None), (1, 1, "ping 5f3a", None)], None),
    # The real reply arrives as a typedstream, not as `text`.
    ([(2, 0, None, typedstream("PONG 5f3a")), (1, 1, "ping 5f3a", None)], "PONG 5f3a"),
])
def test_only_an_inbound_message_carrying_our_nonce_counts_as_the_reply(rows, expected):
    assert mod._reply_after(rows, "5f3a") == expected


def test_the_message_is_passed_as_an_argument_not_spliced_into_source():
    """A quote or backslash in the text must not be able to become script."""
    message = 'quote " and \\ backslash'
    command = mod._applescript("+15551234567", message)
    assert command[-2:] == ["+15551234567", message]
    assert message not in "".join(command[:-2])


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """A stand-in chat.db: the Hermes thread and one unrelated one.

    Frozen on purpose. This is throwaway schema standing in for Apple's, and it
    invites a spelling opinion every time someone reads it — what it protects is
    the handle-join scoping in `_rows`, which has not moved since it was
    written. Change it when that scoping changes, not otherwise.
    """
    db = tmp_path / "chat.db"
    with sqlite3.connect(db) as con:
        con.executescript(
            "create table handle (ROWID integer primary key, id text);"
            "create table message (date int, is_from_me int, text text,"
            " attributedBody blob, handle_id int);"
            "insert into message values (10, 0, 'from hermes', null, 1),"
            " (11, 0, 'private conversation', null, 2);")
        con.executemany("insert into handle values (?, ?)",
                        [(1, "+15551234567"), (2, "+15550000000")])
    monkeypatch.setattr(mod, "CHAT_DB", db)
    monkeypatch.setattr(mod, "LINE", "+15551234567")
    return db


def test_the_query_returns_only_the_hermes_thread(seeded_db):
    """This database holds personal messages; the probe reads one thread."""
    assert [row[2] for row in mod._rows(0)] == ["from hermes"]


def test_the_thread_is_opened_read_only(seeded_db, monkeypatch):
    """Dropping mode=ro would leave a writable handle on personal messages.

    Asserted on the URI the module builds rather than on a refused write —
    sqlite only objects at write time, and `_rows` exposes no connection to
    write through.
    """
    opened = []
    connect = sqlite3.connect
    # `dsn`, not `uri`: _rows passes uri=True, which would collide with a
    # parameter of that name and raise before the assertion ever ran.
    monkeypatch.setattr(mod.sqlite3, "connect",
                        lambda dsn, **kw: (opened.append(dsn), connect(dsn, **kw))[1])
    mod._rows(0)
    assert "mode=ro" in opened[0]
