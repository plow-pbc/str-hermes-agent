"""Wake the owners' chat through the plow_chat plugin's wake inbox.

Shared by every no-agent poller (hostex-poll, turnover-poll) so the file
contract and the "nothing is listening" alarm live in one place.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

INBOX = "plow_chat_wakes"
# The plugin drains its wake inbox every few seconds; a wake still here after
# this long means nothing is reading it, and what it carries is unheard.
WAKE_STALE_SECONDS = 600


def wake(home: pathlib.Path, chat: str, sender: str, text: str) -> pathlib.Path:
    """Write `text` as a turn for `chat` into the plugin's wake inbox, renamed
    into place so the plugin never reads it half-written. Returns the final path."""
    inbox = home / INBOX
    inbox.mkdir(exist_ok=True)
    name = f"{sender.lower()}-{time.time_ns()}"
    tmp = inbox / f"{name}.tmp"
    tmp.write_text(json.dumps({"chat": chat, "from": sender, "text": text}))
    final = inbox / f"{name}.json"
    os.replace(tmp, final)
    return final


def require_drained(home: pathlib.Path, now: float | None = None) -> None:
    """Raise — which the scheduler reports to the operator — while an earlier
    wake sits unread (stale `.json`: nothing is serving the chat) or was refused
    by the plugin (`.rejected`: it will never be read). Either way every later
    event would be recorded as announced and never heard."""
    inbox = home / INBOX
    now = time.time() if now is None else now
    bad = [w.name for w in inbox.glob("*.json") if now - w.stat().st_mtime > WAKE_STALE_SECONDS]
    bad += [w.name for w in inbox.glob("*.rejected")]
    if bad:
        raise RuntimeError(f"undrained wakes in {inbox}: {sorted(bad)}")
