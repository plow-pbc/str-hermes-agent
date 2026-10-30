"""A kanban task as the cross-thread ledger for one multi-step job.

The cron poller keeps one task per guest stay; agent sessions in different chat
threads cannot share memory, so each appends a one-line comment to that task and
the poller reads them back. Kanban is only a ledger here: nothing is ever
assigned (no --assignee), so no dispatcher picks the task up.

Grammar, first token is the verb; anything else is free text and ignored:
  WOKE <step>              driver, when it woke a session for the step
  SENT <step>              woken session, step done
  SKIPPED <step>: <why>    woken session, step deliberately not done
  CLEAN <who>              cleaner's "done" or an owner's "she's done" (last wins)
  HOLD <who>               owners ask the driver to stop
Stdlib only: runs on the image's python.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

HERMES = "/opt/hermes/bin/hermes"


@dataclass(frozen=True)
class Ledger:
    woke: dict[str, datetime]
    done: frozenset[str]
    clean_by: str | None
    hold: bool


def parse(comments: list[dict]) -> Ledger:
    """Fold kanban comment rows (`body`, epoch-seconds `created_at`) into a Ledger.

    The first WOKE for a step wins: the wake time is when the clock started.
    """
    woke: dict[str, datetime] = {}
    done: set[str] = set()
    clean_by = None
    hold = False
    for c in comments:
        verb, _, rest = (c.get("body") or "").strip().partition(" ")
        rest = rest.strip()
        if verb == "WOKE" and rest:
            woke.setdefault(rest, datetime.fromtimestamp(c["created_at"], timezone.utc))
        elif verb == "SENT" and rest:
            done.add(rest)
        elif verb == "SKIPPED" and rest:
            done.add(rest.split(":", 1)[0].strip())
        elif verb == "CLEAN" and rest:
            clean_by = rest
        elif verb == "HOLD" and rest:
            hold = True
    return Ledger(woke, frozenset(done), clean_by, hold)


def _run_hermes(argv: list[str]) -> str:
    return subprocess.run(
        [HERMES, "kanban", *argv], check=True, capture_output=True, text=True
    ).stdout


class Board:
    def __init__(self, run: Callable[[list[str]], str] = _run_hermes):
        self._run = run

    def ensure(self, key: str, title: str, body: str) -> str:
        """Create the task, or return the existing one: the key makes it idempotent."""
        out = self._run(["create", title, "--idempotency-key", key, "--body", body, "--json"])
        return json.loads(out)["id"]

    def by_key(self, prefix: str = "notify-when-house-is-ready ") -> dict[str, tuple[str, str]]:
        """{key: (id, status)} for every task whose title starts with `prefix`,
        archived included, and an archived task wins its key: `create
        --idempotency-key` skips archived tasks, so a key ever archived must be
        known in order never to be created, and woken, again.

        `list --json` does not expose the idempotency key, so titles carry it as
        their second token: `<prefix><key> ...`.
        """
        out: dict[str, tuple[str, str]] = {}
        for t in json.loads(self._run(["list", "--archived", "--json"])):
            key = t["title"].split()[1] if t["title"].startswith(prefix) else None
            if key and out.get(key, ("", ""))[1] != "archived":
                out[key] = (t["id"], t["status"])
        return out

    def ledger(self, task_id: str) -> tuple[str, Ledger]:
        shown = json.loads(self._run(["show", task_id, "--json"]))
        return shown["task"]["status"], parse(shown["comments"])

    def note(self, task_id: str, text: str) -> None:
        self._run(["comment", task_id, text])

    def block(self, task_id: str, reason: str) -> None:
        self._run(["block", task_id, reason])

    def complete(self, task_id: str) -> None:
        self._run(["complete", task_id])

    def archive(self, task_id: str) -> None:
        self._run(["archive", task_id])
