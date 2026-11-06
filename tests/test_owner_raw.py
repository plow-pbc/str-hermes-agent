"""What `bin/owner-raw` stages: the owners' own messages, one file per chat-day.

Run against a stubbed Plow message API served newest-first through real
starting_after paging, so the cursor is exercised rather than mocked away.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DIRECT, GROUP = "cht_direct", "cht_group"


def load():
    loader = importlib.machinery.SourceFileLoader("owner_raw", str(REPO / "bin" / "owner-raw"))
    spec = importlib.util.spec_from_loader("owner_raw", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def msg(uid, stamp, direction, body, who=None):
    return {"uid": uid, "created_at": stamp, "direction": direction, "body": body,
            "sender": {"relationship": "self" if direction == "outbound" else who}}


@pytest.fixture
def owner_raw(tmp_path, monkeypatch):
    # A day is the owners' local day; pin the zone the process computes it in.
    zone = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PLOW_API_BASE", "https://plow.test")
    monkeypatch.setenv("PLOW_AGENT_TOKEN", "t")
    monkeypatch.setenv("PLOW_HOME_CHANNEL", DIRECT)
    monkeypatch.setenv("PLOW_CHAT_GROUP_UIDS", f"{GROUP}=Owners")
    monkeypatch.setenv("PLOW_CHAT_APPROVAL_GROUP", "Owners")
    vault = tmp_path / "vault"
    (vault / "_raw").mkdir(parents=True)
    chats: dict[str, list[dict]] = {DIRECT: [], GROUP: []}
    served: list[str] = []

    def fake_open_json(request):
        url = urllib.parse.urlparse(request.full_url)
        chat = url.path.split("/")[3]
        query = dict(urllib.parse.parse_qsl(url.query))
        newest_first = sorted(chats[chat], key=lambda m: m["created_at"], reverse=True)
        start = 0
        if "starting_after" in query:
            start = [m["uid"] for m in newest_first].index(query["starting_after"]) + 1
        page = newest_first[start:start + int(query["limit"])]
        served.extend(m["uid"] for m in page)
        return {"data": page, "has_more": start + len(page) < len(newest_first)}

    def run():
        mod = load()
        mod.PAGE = 2
        monkeypatch.setattr(mod.hostex_api, "open_json", fake_open_json)
        monkeypatch.setattr(sys, "argv", ["owner-raw", "--vault", str(vault)])
        served.clear()
        mod.main()
        return {f.name: f.read_text() for f in sorted((vault / "_raw" / "owner").glob("*.md"))}, list(served)

    yield run, chats, vault
    if zone is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = zone
    time.tzset()


def test_one_file_per_day_holding_an_owner_message_each_beside_what_it_answers(owner_raw):
    run, chats, _ = owner_raw
    chats[GROUP] += [msg("m1", "2026-10-29T18:00:00Z", "outbound", "DRAFT: sure, stay till 1!"),
                     msg("m2", "2026-10-29T18:05:00Z", "inbound", "No late checkouts as a general rule."),
                     msg("m3", "2026-10-29T18:06:00Z", "inbound", "Agreed.", who="abby"),
                     msg("m4", "2026-10-30T09:00:00Z", "outbound", "Wiki digest: nothing changed")]
    files, _ = run()
    assert list(files) == ["owners-2026-10-29.md"]
    text = files["owners-2026-10-29.md"]
    assert "capture_source: owner" in text
    assert f"sources: [https://plow.test/v1/chats/{GROUP}/messages#2026-10-29]" in text
    assert ("**str** (context): DRAFT: sure, stay till 1!\n\n"
            "**owner** (18:05): No late checkouts as a general rule.\n\n"
            "**abby** (18:06): Agreed.\n") in text


def test_phone_numbers_codes_and_credentials_are_never_staged(owner_raw):
    run, chats, _ = owner_raw
    chats[DIRECT].append(msg("m1", "2026-08-12T18:00:00Z", "inbound",
                             "Call Eddie at +1 425-555-0142. The garage code is 7717, use card 0878. "
                             "Token: Bearer plow_TiqCd2beAv8N9TswfCW1bpSqNkn9 and plow_abcdefghijklmnopqrstu"))
    text = run()[0]["direct-2026-08-12.md"]
    for value in ("555-0142", "7717", "0878", "plow_Tiq", "plow_abc"):
        assert value not in text
    assert "[phone]" in text and "code is [number]" in text and "card [number]" in text


def test_a_later_run_fetches_back_only_to_the_newest_staged_day_and_rewrites_only_what_changed(owner_raw):
    run, chats, _ = owner_raw
    chats[DIRECT] += [msg("o", "2026-09-01T10:00:00Z", "inbound", "Hello?"),
                      msg("a", "2026-10-01T10:00:00Z", "inbound", "10th Ave is long-term."),
                      msg("b", "2026-10-02T10:00:00Z", "inbound", "Use the rental card."),
                      msg("c", "2026-10-03T10:00:00Z", "inbound", "Gianna comes at 11.")]
    first, _ = run()
    assert list(first) == ["direct-2026-09-01.md", "direct-2026-10-01.md", "direct-2026-10-02.md",
                           "direct-2026-10-03.md"]

    chats[DIRECT].append(msg("d", "2026-10-03T12:00:00Z", "inbound", "Correction: at 11 on a same-day turn."))
    second, served = run()
    assert "o" not in served
    assert second["direct-2026-10-01.md"] == first["direct-2026-10-01.md"]
    assert "Correction" in second["direct-2026-10-03.md"]


def test_an_archived_day_counts_as_staged_and_an_unchanged_one_is_not_rewritten(owner_raw):
    run, chats, vault = owner_raw
    chats[DIRECT].append(msg("a", "2026-10-01T10:00:00Z", "inbound", "10th Ave is long-term."))
    run()
    archived = vault / "_raw" / "owner" / "_archived"
    archived.mkdir()
    (vault / "_raw" / "owner" / "direct-2026-10-01.md").rename(archived / "direct-2026-10-01.md")
    files, served = run()
    assert files == {} and served == ["a"]
