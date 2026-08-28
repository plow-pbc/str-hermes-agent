#!/usr/bin/env python3
"""Send a real iMessage to Hermes from this Mac and read the reply.

The whole path, in the direction a real message travels: handset, Apple, Plow,
the websocket, the serving gateway, the agent, its tools, and the reply back.
Every other check here starts a fresh process or reads a log, so this is the one
that covers the line and the pairing.

Not the carrier: the send names `service type = iMessage`, so it rides Apple's
data path and never touches SMS.

Runs on the Mac, not on wakeup. Reading needs no AppleScript: chat.db is a plain
sqlite file. Sending does, because chat.db is a record and not a send queue — a
row written there sends nothing, since Apple's daemon does the sending and does
not read this database for instructions.

Reads only the Hermes thread. Everything else in that database is personal.
"""
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

# The line Hermes answers on — the number this Mac texts to reach the agent. Not
# the Plow number, which is a different channel on the same account. Mac-side
# only, never checked in: a public tree must not name a live line.
LINE = os.environ.get("HERMES_LINE")
CHAT_DB = Path.home() / "Library/Messages/chat.db"
# 360s, because the first turn after a restart can pay a full context
# compaction before its first model call — a 240k-token session took 227.7s
# to answer, and the 180s this used to allow reported that healthy path as a
# dead line. No default is safe: the same log carries a 3426.8s turn. The
# timeout message below is what covers the tail, by naming the log lines that
# place the failure — and by saying that neither of them is a pass.
TIMEOUT = float(os.environ.get("HANDSET_TIMEOUT", "360"))
# Apple's message timestamps count nanoseconds from 2001-01-01.
APPLE_EPOCH = 978307200


def _body(text, blob):
    """The message body, from `text` when present and the typedstream when not.

    macOS leaves `text` NULL on modern versions and stores the body in
    `attributedBody`: a typedstream where the string follows an NSString marker
    and a length that is one byte, or 0x81 followed by a little-endian uint16.
    A reader that trusts `text` reports no reply against a healthy system.
    """
    if text:
        return text
    if not blob:
        return ""
    start = blob.find(b"NSString")
    if start == -1:
        return ""
    plus = blob.find(b"\x84\x01+", start)
    if plus == -1:
        return ""
    at = plus + 3
    length = blob[at]
    at += 1
    if length == 0x81:
        length = int.from_bytes(blob[at:at + 2], "little")
        at += 2
    return blob[at:at + length].decode("utf-8", errors="replace")


def _reply_after(rows, nonce):
    """The first inbound message carrying our nonce, newest first, or None.

    `is_from_me` is the whole discrimination: our own send contains the nonce
    too — it is the message that asked for it — so a scan matching only the
    nonce reads our own prompt back and passes without the agent having answered.
    """
    for _date, is_from_me, text, blob in rows:
        if is_from_me:
            continue
        body = _body(text, blob).strip()
        if nonce in body:
            return body
    return None


def _applescript(line, message):
    """argv, never interpolation — a quote in the text must not become script."""
    return ["osascript", "-e", """
        on run argv
          set theLine to item 1 of argv
          set theBody to item 2 of argv
          tell application "Messages"
            set theTarget to buddy theLine of (1st service whose service type = iMessage)
            send theBody to theTarget
          end tell
        end run""", line, message]


def _rows(since):
    """Recent messages in the Hermes thread only. Read-only, and nothing else."""
    with sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True) as db:
        return db.execute(
            "select m.date, m.is_from_me, m.text, m.attributedBody from message m "
            "join handle h on m.handle_id = h.ROWID "
            "where h.id = ? and m.date > ? order by m.date desc limit 40",
            (LINE, since)).fetchall()


def main():
    if not LINE:
        print("set HERMES_LINE to the number Hermes answers on (E.164)", file=sys.stderr)
        return 1
    if not CHAT_DB.exists():
        print(f"no Messages database at {CHAT_DB} — this runs on the Mac, not on wakeup",
              file=sys.stderr)
        return 1
    nonce = uuid.uuid4().hex[:8]
    text = " ".join(sys.argv[1:]) or "Reply with PONG"
    since = int((time.time() - APPLE_EPOCH) * 1_000_000_000)
    # stderr passes through: a missing Messages automation approval is the most
    # likely failure here, and osascript names it. Capturing would leave the
    # operator a bare non-zero exit for the one symptom the skill triages on.
    subprocess.run(
        _applescript(LINE, f"{text}\n\n(Include {nonce} verbatim in your reply.)"),
        check=True)
    print(f"sent to {LINE}, waiting for {nonce}", flush=True)
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(5)
        reply = _reply_after(_rows(since), nonce)
        if reply:
            print(f"REPLY: {reply}")
            return 0
    print(f"TIMEOUT after {TIMEOUT:.0f}s — no reply carrying {nonce}. The message left this "
          "Mac. Before suspecting the line, place the failure: grep "
          "~/.hermes/logs/gateway.log on wakeup for an 'inbound message' line naming this "
          "chat after the send. Absent, it never arrived and the line and the pairing are "
          "where to look. Present, it did, and the fault is downstream of every leg this "
          "probe covers — re-run with a larger HANDSET_TIMEOUT before concluding which "
          "one, since a slow turn and a lost reply look identical from here. Neither log "
          "line is a pass; only a reply carrying the nonce is.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
