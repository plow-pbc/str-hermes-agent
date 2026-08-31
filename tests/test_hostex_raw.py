"""What `bin/hostex-raw` guarantees about the raw cache.

The cache is the only progress state, so every guarantee here is about what is
on disk after a run: which conversations were fetched, which were left alone,
and which a bad response must never be allowed to damage. Pure logic over a
stubbed Hostex, so it costs nothing to pin.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

TEXT = [{"content": "The sauna takes an hour.", "sender_role": "host", "created_at": "t"}]
EMPTY = [{"content": "", "sender_role": "host", "created_at": "t"}]


def load():
    loader = importlib.machinery.SourceFileLoader("hostex_raw", str(REPO / "bin" / "hostex-raw"))
    spec = importlib.util.spec_from_loader("hostex_raw", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def conv(cid, stamp, title="Lake House"):
    return {"id": cid, "property_title": title, "last_message_at": stamp}


def run(monkeypatch, vault, convs, messages, page=100, detail=None):
    """Run main() against a stubbed Hostex.

    `messages` maps conversation id to the message list the detail endpoint
    returns; `detail` overrides the whole envelope for schema-break cases. `convs` is served through real offset/limit paging, so paging is
    exercised by every test rather than mocked away. Returns
    (exit_code, detail_ids_fetched, exit_message).
    """
    mod = load()
    monkeypatch.setattr(mod, "PAGE", page)
    fetched: list[str] = []

    def fake_api_get(path, tok, **params):
        if path == "/conversations":
            offset = int(params.get("offset", 0))
            limit = int(params.get("limit", page))
            return {"data": {"conversations": convs[offset : offset + limit]}}
        cid = path.rsplit("/", 1)[-1]
        fetched.append(cid)
        # `detail` lets a test return a whole envelope, so a renamed or missing
        # field can be exercised. Without it the stub always supplies the key
        # the script indexes, and the fail-loud path is unreachable from here.
        return detail(cid) if detail else {"data": {"messages": messages[cid]}}

    monkeypatch.setattr(mod, "api_get", fake_api_get)
    monkeypatch.setattr(mod, "read_token", lambda: "t")
    monkeypatch.setattr(sys, "argv", ["hostex-raw", "--vault", str(vault)])

    code, message = 0, ""
    try:
        mod.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        message = "" if isinstance(exc.code, int) else str(exc.code)
    return code, fetched, message


def raw(vault, cid):
    path = vault / "_raw" / "hostex" / f"{cid}.md"
    return path.read_text() if path.exists() else None


def front(text, key):
    prefix = f"{key}: "
    for line in text.splitlines()[1:]:
        if line == "---":
            break
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


# The two layouts wiki-ingest actually produces. One live run left ten
# conversations under the first and twenty under the second, so code that names
# one of them treats the other twenty as never-fetched and re-fetches the lot.
ARCHIVES = {
    "archived-sibling": ("_raw", "_archived", "hostex"),
    "archived-nested": ("_raw", "hostex", "_archived"),
}


def archive(vault, cid, layout="archived-sibling"):
    """Move a cached conversation where wiki-ingest puts it after distilling.

    A move, not a copy. When this copied, every caller had to remember to
    unlink the original — and forgetting left an un-archived copy that still
    satisfies the cache, so the archive path went unexercised while the test
    still passed.
    """
    d = vault.joinpath(*ARCHIVES[layout])
    d.mkdir(parents=True, exist_ok=True)
    moved = d / f"{cid}.md"
    (vault / "_raw" / "hostex" / f"{cid}.md").replace(moved)
    return moved


def test_bootstrap_writes_every_attributable_conversation(monkeypatch, tmp_path):
    code, fetched, _ = run(
        monkeypatch, tmp_path,
        [conv("a", "2026-07-01T00:00:00+00:00"), conv("b", "2026-07-02T00:00:00+00:00")],
        {"a": TEXT, "b": TEXT},
    )
    assert code == 0
    assert sorted(fetched) == ["a", "b"]
    assert "The sauna takes an hour." in raw(tmp_path, "a")
    assert "The sauna takes an hour." in raw(tmp_path, "b")
    # Who said it and when, not just what — the fields the extractor reads to
    # attribute a fact. Nothing else in the suite pins what render() emits, so a
    # render that dropped them, or wrote `**None** (None)`, would pass.
    assert "**host** (t):" in raw(tmp_path, "a")
    # The cache is the cursor; a watermark file reintroduces what this deleted.
    # A cold run is exactly where one would be written, so this is that check.
    assert not (tmp_path / "_meta").exists()


@pytest.mark.parametrize(
    "where", ["raw", "archived-sibling", "archived-nested"]
)
def test_unchanged_stamp_costs_no_detail_call(monkeypatch, tmp_path, where):
    """Archived counts as cached: wiki-ingest moves a conversation once it has
    pages, and reading only `_raw/hostex/` would re-fetch the whole corpus."""
    convs = [conv("a", "2026-07-01T00:00:00+00:00")]
    run(monkeypatch, tmp_path, convs, {"a": TEXT})
    if where != "raw":
        archive(tmp_path, "a", layout=where)

    code, fetched, _ = run(monkeypatch, tmp_path, convs, {"a": TEXT})
    assert code == 0
    assert fetched == []


# "archived" counts as cached — wiki-ingest moves a conversation once it has
# pages — and the update must land back in `_raw/hostex/`, since ingest only
# picks up what is not archived.
@pytest.mark.parametrize(
    ("where", "body_changed"),
    [
        ("raw", True),
        ("archived-sibling", True),
        ("archived-nested", True),
        ("raw", False),
    ],
    ids=[
        "new-reply",
        "new-reply-while-archived",
        "new-reply-while-nested-archived",
        "stamp-moved-alone",
    ],
)
def test_a_moved_stamp_is_recorded_and_the_hash_tracks_the_body(
    monkeypatch, tmp_path, where, body_changed
):
    """The stamp gates fetching, the hash gates ingesting, and they differ.

    A stamp that moves must be recorded either way or the conversation is
    re-fetched every run forever. The hash must move only when the body does,
    or ingest re-processes a page whose source is identical.
    """
    run(monkeypatch, tmp_path, [conv("a", "2026-07-01T00:00:00+00:00")], {"a": TEXT})
    before = front(raw(tmp_path, "a"), "payload_hash")
    if where != "raw":
        archive(tmp_path, "a", layout=where)
    reply = {"content": "Also the pantry light flickers.", "sender_role": "guest", "created_at": "t"}
    messages = TEXT + [reply] if body_changed else TEXT

    code, fetched, _ = run(
        monkeypatch, tmp_path, [conv("a", "2026-07-09T00:00:00+00:00")], {"a": messages}
    )

    assert code == 0
    assert fetched == ["a"], "a moved stamp must cost a detail call either way"
    if body_changed:
        assert "pantry light" in raw(tmp_path, "a")
        assert front(raw(tmp_path, "a"), "payload_hash") != before
        assert front(raw(tmp_path, "a"), "last_message_at") == "2026-07-09T00:00:00+00:00"
    else:
        # Not written at all. The bytes of the raw file are an input to
        # ingest-all's already-processed check, so a rewrite carrying only a new
        # stamp would re-queue an unchanged conversation for an ingest turn that
        # can append its facts to the page twice. The price is that the cached
        # stamp stays behind and this conversation is re-fetched every run until
        # a real message arrives.
        assert front(raw(tmp_path, "a"), "payload_hash") == before
        assert front(raw(tmp_path, "a"), "last_message_at") == "2026-07-01T00:00:00+00:00"


def test_deleted_raw_file_is_refetched(monkeypatch, tmp_path):
    """Deleting a file IS the recovery procedure — there is nothing else."""
    convs = [conv("a", "2026-07-01T00:00:00+00:00")]
    run(monkeypatch, tmp_path, convs, {"a": TEXT})
    (tmp_path / "_raw" / "hostex" / "a.md").unlink()

    code, fetched, _ = run(monkeypatch, tmp_path, convs, {"a": TEXT})
    assert code == 0
    assert fetched == ["a"]
    assert "The sauna takes an hour." in raw(tmp_path, "a")


def test_a_blip_beside_a_good_fetch_is_absorbed_and_retried(monkeypatch, tmp_path):
    """The run saw text, so the response shape is fine and this is a blip.

    Nothing is written and the stamp is left where it was, so the next run sees
    a mismatch and re-tries on its own — no watermark to reset, no envelope to
    clean up, and the nightly chain keeps going.
    """
    both = [conv("a", "2026-07-01T00:00:00+00:00"), conv("b", "2026-07-01T00:00:00+00:00")]
    run(monkeypatch, tmp_path, both, {"a": TEXT, "b": TEXT})

    moved = [conv("a", "2026-07-09T00:00:00+00:00"), conv("b", "2026-07-09T00:00:00+00:00")]
    code, _, _ = run(monkeypatch, tmp_path, moved, {"a": EMPTY, "b": TEXT})

    assert code == 0
    assert "The sauna takes an hour." in raw(tmp_path, "a")
    assert front(raw(tmp_path, "a"), "last_message_at") == "2026-07-01T00:00:00+00:00"

    # The stamp moved because a message arrived, so the retry carries it. b is
    # fetched again too and that is the design — its stamp moved while its
    # payload did not, so nothing was written for it and its cached stamp still
    # trails the listing. What matters here is that a, held back by the blip,
    # is retried and settles.
    arrived = TEXT + [{"content": "And the gate code changed.", "sender_role": "host", "created_at": "t"}]
    _, fetched, _ = run(monkeypatch, tmp_path, moved, {"a": arrived, "b": TEXT})
    assert "a" in fetched, "the held stamp must make the next run retry"
    assert "gate code changed" in raw(tmp_path, "a")
    assert front(raw(tmp_path, "a"), "last_message_at") == "2026-07-09T00:00:00+00:00"


def test_a_cold_cache_break_writes_no_envelopes(monkeypatch, tmp_path, capsys):
    """The failure this script shipped, and the rule that now makes it unreachable.

    235 well-formed files holding 3,865 empty messages once passed every check,
    because the checks asserted on file existence and frontmatter. Refusing to
    write a textless conversation is what stops that, and it is what this pins:
    a first run where nothing carries text leaves NOTHING behind, so there is no
    envelope to carry a stamp, match on the re-run, and strand a conversation.

    It exits 0 and says so on stderr rather than going fatal. That is the
    deliberate gap: no cached conversation lost text, so nothing impossible has
    happened, and the only way to call this a break is to count — which fails on
    a quiet night full of stuck blanks (see the stuck-blank test). Nothing is
    written either way, so the cost of being wrong here is a re-fetch.
    """
    convs = [conv(cid, "2026-07-01T00:00:00+00:00") for cid in "abcd"]

    code, fetched, _ = run(monkeypatch, tmp_path, convs, {cid: EMPTY for cid in "abcd"})

    assert code == 0
    assert sorted(fetched) == ["a", "b", "c", "d"]
    for cid in "abcd":
        assert raw(tmp_path, cid) is None, f"{cid} was left as an empty envelope"
    assert "declined to write 4 conversation(s)" in capsys.readouterr().err, (
        "the run must say on stderr that it staged nothing"
    )


def test_every_page_is_read_and_the_newest_sighting_wins(monkeypatch, tmp_path):
    """Two invariants of the sweep, neither of which a single page can show.

    The listing is only *almost* newest-first, so a conversation can sit behind
    already-cached ones and must still be read. And the list mutates while we
    page, so the same id can appear twice with different stamps.
    """
    convs = [
        conv("a", "2026-07-09T00:00:00+00:00"),
        conv("b", "2026-07-08T00:00:00+00:00"),
        # Page 2: 'a' again, seen earlier with an older stamp, plus a straggler
        # that sorts out of order the way the real corpus's tail does.
        conv("a", "2026-07-01T00:00:00+00:00"),
        conv("c", "2026-07-07T00:00:00+00:00"),
    ]
    code, fetched, _ = run(monkeypatch, tmp_path, convs, {k: TEXT for k in "abc"}, page=2)

    assert code == 0
    assert sorted(fetched) == ["a", "b", "c"], "a later page was not read"
    assert front(raw(tmp_path, "a"), "last_message_at") == "2026-07-09T00:00:00+00:00"


def test_property_less_conversations_are_skipped_not_fatal(monkeypatch, tmp_path):
    """131 of 366 have no property. Exiting on them would abort every run."""
    code, fetched, _ = run(
        monkeypatch, tmp_path,
        [conv("a", "2026-07-01T00:00:00+00:00", title=""),
         conv("b", "2026-07-02T00:00:00+00:00")],
        {"b": TEXT},
    )
    assert code == 0
    assert fetched == ["b"]
    assert raw(tmp_path, "a") is None


def test_a_schema_break_leaves_nothing_a_re_run_cannot_recover(monkeypatch, tmp_path):
    """The fatal message promises "fix the field and re-run". Hold it to that.

    'b' is uncached when the break hits, so there is no cached text to protect
    it — the case that once wrote an empty envelope carrying the current stamp,
    which then matched on the re-run, counted as unchanged, and was never
    fetched again. Silent, permanent, and unnameable.
    """
    run(monkeypatch, tmp_path, [conv("a", "2026-07-01T00:00:00+00:00")], {"a": TEXT})

    broken = [conv("a", "2026-07-09T00:00:00+00:00"), conv("b", "2026-07-09T00:00:00+00:00")]
    code, _, msg = run(monkeypatch, tmp_path, broken, {"a": EMPTY, "b": EMPTY})
    assert code != 0
    assert raw(tmp_path, "b") is None, "a textless envelope reached disk"
    assert "Nothing was written" in msg

    # Now do exactly what the message says.
    code, fetched, _ = run(monkeypatch, tmp_path, broken, {"a": TEXT, "b": TEXT})
    assert code == 0
    assert sorted(fetched) == ["a", "b"], "the re-run skipped a conversation"
    assert "The sauna takes an hour." in raw(tmp_path, "a")
    assert "The sauna takes an hour." in raw(tmp_path, "b")


def test_stuck_blank_conversations_never_trip_the_break_guard(monkeypatch, tmp_path):
    """Blanks are never written, so they never settle — and they pile up.

    Each one is re-fetched every run until a guest types, so any threshold on
    how MANY came back blank is eventually met by nothing but stuck blanks on a
    quiet night, taking the chain down indefinitely. The signal has to be which
    conversations went blank, not how many.
    """
    corpus = [conv(cid, "2026-07-01T00:00:00+00:00") for cid in "abcde"]
    run(monkeypatch, tmp_path, corpus, {cid: TEXT for cid in "abcde"})

    # Two photo-only openers arrive on different nights and never settle.
    corpus += [conv("y", "2026-07-08T00:00:00+00:00"), conv("z", "2026-07-09T00:00:00+00:00")]
    blanks = {"y": EMPTY, "z": []}

    for night in range(3):
        code, fetched, _ = run(monkeypatch, tmp_path, corpus, {**{c: TEXT for c in "abcde"}, **blanks})
        assert code == 0, f"night {night}: two stuck blanks must not read as a break"
        assert sorted(fetched) == ["y", "z"], "only the unsettled pair should cost a call"
        assert raw(tmp_path, "y") is None and raw(tmp_path, "z") is None

    # And a stuck blank arrives under its own steam once there is something to
    # say — nothing had to remember it was pending.
    _, fetched, _ = run(
        monkeypatch, tmp_path, corpus, {**{c: TEXT for c in "abcde"}, "y": EMPTY, "z": TEXT}
    )
    assert fetched == ["y", "z"]
    assert "The sauna takes an hour." in raw(tmp_path, "z")


@pytest.mark.parametrize("blank", [EMPTY, []], ids=["empty-content", "no-messages"])
def test_a_single_cached_conversation_going_blank_is_fatal(monkeypatch, tmp_path, blank):
    """Text does not leave a conversation. One doing so is the response shape.

    Against the raw cache only. Where the cache lives is the archive resolver's
    contract, not this one, and the stamp tables above already exercise both
    layouts through it.
    """
    corpus = [conv(cid, "2026-07-01T00:00:00+00:00") for cid in "abcde"]
    run(monkeypatch, tmp_path, corpus, {cid: TEXT for cid in "abcde"})

    held = tmp_path / "_raw" / "hostex" / "a.md"
    corpus[0] = conv("a", "2026-07-09T00:00:00+00:00")
    code, fetched, msg = run(monkeypatch, tmp_path, corpus, {**{c: TEXT for c in "abcde"}, "a": blank})

    assert code != 0, "a cached conversation losing its text is not a quiet night"
    assert fetched == ["a"]
    assert "had text cached already" in msg
    assert "'a'" in msg, "the message must name which conversation lost its text"
    assert held.exists(), "the cached text must survive"
    assert "The sauna takes an hour." in held.read_text()


@pytest.mark.parametrize("cid", ["../../escape", "a/b", "..", "."], ids=["traversal", "slash", "dotdot", "dot"])
def test_an_id_that_is_not_a_filename_never_reaches_the_filesystem(monkeypatch, tmp_path, cid):
    """The id becomes a filename and it comes from the API, not from argv.

    Every id in the real corpus is an opaque token, so this has never fired —
    but a write that escapes the vault is what review-priority.md still blocks
    a merge on, and the same untrusted value is already quoted before it
    reaches a shell. Refusing it is a report, not a fallback.
    """
    vault = tmp_path / "vault"
    corpus = [conv(cid, "2026-07-01T00:00:00+00:00"), conv("safe", "2026-07-01T00:00:00+00:00")]

    code, fetched, _ = run(monkeypatch, vault, corpus, {"safe": TEXT})

    assert code == 0, "one bad id must not abort the run"
    assert fetched == ["safe"], "the unsafe id must not even be fetched"
    assert "The sauna takes an hour." in raw(vault, "safe")
    assert not list(tmp_path.rglob("escape*")), "a write escaped the vault"
    assert sorted(p.name for p in (vault / "_raw" / "hostex").iterdir()) == ["safe.md"]


@pytest.mark.parametrize(
    ("titles", "ids", "n_skipped", "n_unsafe"),
    [
        (["", ""], ["a", "b"], 2, 0),
        (["Lake House"], ["../../escape"], 0, 1),
        (["", "Lake House", "Lake House"], ["a", "../../escape", "b/c"], 1, 2),
    ],
    ids=["property-moved", "ids-moved", "mixed-break"],
)
def test_a_corpus_that_can_stage_nothing_is_fatal(
    monkeypatch, tmp_path, titles, ids, n_skipped, n_unsafe
):
    """Two filters suppress `attributed`, so the exit reports both counts.

    Every row uses asymmetric counts on purpose. `property_title` appears in
    every one of these exits ("0 carried no `property_title`"), so asserting the
    field name alone passes on the wrong break — and a symmetric fixture passes
    even if the two counts are transposed in the message. The mixed row is the
    one that needs it most: it is the shape where neither filter accounts for
    the corpus, which is why the message reports counts instead of naming a
    field.
    """
    corpus = [
        conv(cid, f"2026-07-0{i + 1}T00:00:00+00:00", title=title)
        for i, (cid, title) in enumerate(zip(ids, titles, strict=True))
    ]

    code, fetched, msg = run(monkeypatch, tmp_path, corpus, {})

    assert code != 0, "a corpus that staged nothing at all must not exit 0"
    assert fetched == [], "nothing attributable means nothing costs a detail call"
    assert f"{n_skipped} carried no `property_title`" in msg
    assert f"{n_unsafe} had an id that is not a safe filename" in msg
    assert "Nothing was written" in msg
    assert not list(tmp_path.rglob("*.md")), "a corpus that staged nothing wrote something"


def test_a_falsy_sender_or_stamp_is_data_not_a_rename(monkeypatch, tmp_path):
    """Indexing catches the absent key; the default catches the falsy value.

    Both halves are needed and they fail differently. Without the index a rename
    is silently written as "unknown"; without the default the value stringifies
    into the body, the hash is taken over that, and the file settles — so the
    degraded body is what every later run treats as processed.

    The empty string is both the shape this repo has measured (`content` arrives
    empty on 2 of 235 openers) and the one that discriminates: narrowing the
    default to `is not None` renders `**** ()` and settles here, while a `None`
    row would stay green.
    """
    messages = [{"content": "The sauna takes an hour.", "sender_role": "", "created_at": ""}]

    code, _, _ = run(monkeypatch, tmp_path, [conv("a", "2026-07-01T00:00:00+00:00")], {"a": messages})

    assert code == 0, "a falsy sender is ordinary data, not a schema break"
    assert "**unknown** ():" in raw(tmp_path, "a")


def test_a_renamed_property_title_raises_rather_than_skipping_the_corpus(monkeypatch, tmp_path):
    """Absent key and empty value are different failures and must not look alike.

    An empty `property_title` is the steady state — 131 of 366 — and is skipped.
    The key being gone is the field renamed, and `.get` would turn that into 366
    ordinary skips: a run that stages nothing, exits 0 on a warm cache because
    `lost_attribution` only fires when the corpus is fully unattributable, and
    reads as a quiet night. Indexed, so it raises and names the field.
    """
    listing = [{"id": "a", "last_message_at": "2026-07-01T00:00:00+00:00"}]

    with pytest.raises(KeyError, match="property_title"):
        run(monkeypatch, tmp_path, listing, {})

    assert not list(tmp_path.rglob("*.md"))


def test_an_empty_corpus_is_a_quiet_night_not_a_break(monkeypatch, tmp_path):
    """The negative row of the guard above, and the reason it tests the corpus.

    `attributed == 0` is true of an empty listing too — a token scoped to no
    properties, an emptied account, a legitimate zero-page sweep. Without the
    `bool(conversations)` term those exit non-zero naming a schema break, and
    the nightly chain goes red on a night nothing happened.
    """
    code, fetched, msg = run(monkeypatch, tmp_path, [], {})

    assert code == 0, "an empty corpus is not a schema break"
    assert fetched == []
    assert msg == ""


@pytest.mark.parametrize(
    ("envelope", "missing"),
    [
        pytest.param({"data": {"msgs": TEXT}}, "messages", id="messages-renamed"),
        pytest.param({"data": {}}, "messages", id="messages-absent"),
        pytest.param(
            {"data": {"messages": [{"sender_role": "host", "created_at": "t"}]}},
            "content",
            id="content-absent",
        ),
        pytest.param(
            {"data": {"messages": [{"content": "hi", "created_at": "t"}]}},
            "sender_role",
            id="sender-role-absent",
        ),
        pytest.param(
            {"data": {"messages": [{"content": "hi", "sender_role": "host"}]}},
            "created_at",
            id="created-at-absent",
        ),
    ],
)
def test_a_renamed_required_field_aborts_immediately(monkeypatch, tmp_path, envelope, missing):
    """The hole a `.get` fallback leaves open, on the shape nothing else covers.

    With a fallback, a rename reads as an ordinary empty response, and the guard
    cannot see it when the affected conversation is new and uncached:
    `blank_cached` stays empty, so the run exits 0 having staged nothing.
    Indexing makes it raise on the first fetch instead.
    """
    # Two cached and unchanged is the precondition, and it is load-bearing for
    # the assertions below: the abort has to be shown not to touch cached text,
    # which needs cached text to exist.
    settled = [conv(cid, "2026-07-01T00:00:00+00:00") for cid in "ab"]
    code, fetched, _ = run(monkeypatch, tmp_path, settled, {c: TEXT for c in "ab"})
    assert code == 0 and fetched == ["a", "b"]
    assert raw(tmp_path, "a") and raw(tmp_path, "b")

    arriving = settled + [conv("c", "2026-07-09T00:00:00+00:00")]
    with pytest.raises(KeyError, match=missing):
        run(monkeypatch, tmp_path, arriving, {}, detail=lambda cid: envelope)

    # The abort's central promise: the conversation it died on did not reach
    # disk, and the cached ones are untouched.
    assert raw(tmp_path, "c") is None, "a partial fetch must not reach disk"
    for cid in "ab":
        cached = raw(tmp_path, cid)
        assert cached is not None, f"{cid} lost its cached file"
        assert "The sauna takes an hour." in cached, f"{cid} lost its cached text"


def test_the_script_is_executable():
    """Every other test loads the file through SourceFileLoader, which ignores
    the mode — so the suite passes on a file the README's `./bin/hostex-raw` and
    bin/nightly.sh cannot run. The bit was dropped once already, green
    throughout."""
    assert os.access(REPO / "bin" / "hostex-raw", os.X_OK), (
        "bin/hostex-raw has lost its executable bit; `./bin/hostex-raw` and the "
        "nightly chain both invoke it directly"
    )


def test_a_file_written_before_the_rename_is_refetched_once(monkeypatch, tmp_path):
    """The stamp check returns before anything is rendered.

    So a cached file written when the field was called `content_hash` would
    keep that name until a guest happened to reply — and while it does, the
    ingest agent can copy that line into the manifest, where a whole-file
    digest belongs, and the conversation re-ingests every night. Renaming the
    field only helps once the file carries the new name.

    Self-clearing: one extra fetch, then the stamp check short-circuits again.
    """
    convs = [conv("a", "2026-07-01T00:00:00+00:00")]
    run(monkeypatch, tmp_path, convs, {"a": TEXT})

    cached = tmp_path / "_raw" / "hostex" / "a.md"
    cached.write_text(cached.read_text().replace("payload_hash:", "content_hash:"))

    _, fetched, _ = run(monkeypatch, tmp_path, convs, {"a": TEXT})
    assert fetched == ["a"], "a pre-rename file must be refetched even on an unchanged stamp"
    assert "payload_hash:" in cached.read_text()

    _, fetched, _ = run(monkeypatch, tmp_path, convs, {"a": TEXT})
    assert fetched == [], "and it must settle immediately after, not refetch forever"
