"""The Plow chat API for str's scripts and its send-after MCP server.

`env` carries PLOW_API_BASE and PLOW_AGENT_TOKEN: a script merges setup.env
over os.environ (str_env), the MCP server gets them from Hermes' config.
"""
from __future__ import annotations

import itertools
import json
import urllib.request

import hostex_api

# Compare-and-send reads this far back for the caller's message: a thread that
# moved further than that since the caller read it is all news to them anyway.
LOOKBACK = 20


def _request(env, path: str, payload: dict | None = None) -> urllib.request.Request:
    return urllib.request.Request(
        f"{env['PLOW_API_BASE']}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {env['PLOW_AGENT_TOKEN']}", "User-Agent": "str/1.0",
                 "Accept": "application/json", "Content-Type": "application/json"})


def messages(env, chat: str, limit: int = 100) -> list[dict]:
    """The newest `limit` messages in a chat, newest first, as its members see them."""
    return hostex_api.open_json(_request(env, f"/v1/chats/{chat}/messages?limit={limit}"))["data"]


def newest_uid(env, chat: str) -> str:
    """The uid a job hands its send as `after`: the thread as the job saw it."""
    return next((m["uid"] for m in messages(env, chat, 1)), "")


def send_after(env, chat: str, body: str, after: str) -> dict:
    """Send `body` only if `after` is still the newest message in `chat`.
    Otherwise send nothing and return what arrived since, oldest first, so the
    caller reads it and decides again. Not atomic: the API takes no
    precondition, so a message landing between the read and the send still
    races it."""
    page = messages(env, chat, LOOKBACK)
    if (page[0]["uid"] if page else "") != after:
        newer = itertools.takewhile(lambda m: m["uid"] != after, page)
        return {"sent": False, "newer": [
            {k: m.get(k) for k in ("uid", "direction", "sender", "body", "created_at")}
            for m in reversed(list(newer))]}
    # A write, so OPENER directly: open_json retries, and a retried send could land twice.
    with hostex_api.OPENER.open(_request(env, f"/v1/chats/{chat}/messages", {"body": body}),
                                timeout=hostex_api.TIMEOUT_S) as response:
        sent = json.loads(response.read().decode())
    # A repeat hold answers 200 with the earlier message's uid; nothing went out.
    if sent.get("held"):
        return {"sent": False, "held": sent["reason"]}
    return {"sent": True, "message_id": sent["uid"]}
