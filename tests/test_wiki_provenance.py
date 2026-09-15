"""What bin/wiki-provenance reports about pages it reads over the relay.

The relay is replaced by a dict of the wiki's files, because what is under test
is the judgement over pages and manifest, not transport (tests/test_plow_relay.py
drives that).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
URL = "https://hostex.io/app/conversations/"


def load():
    loader = importlib.machinery.SourceFileLoader("wiki_provenance", str(REPO / "bin" / "wiki-provenance"))
    spec = importlib.util.spec_from_loader("wiki_provenance", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def page(sources):
    listed = "".join(f"  - {s}\n" for s in sources)
    return f"---\ntitle: T\nsources:\n{listed}---\n- The sauna takes an hour.\n"


INDEX = (
    "# Wiki Index\n\n## str/properties\n"
    "- [[str/properties/lake|Lake House]] — the lake property ( #property)\n\n"
    "## str/operations\n"
    "- [[str/operations/lake-sauna|Lake House sauna]] — how it heats ( #sauna)\n"
    "- [[str/operations/lake-parking|Lake House parking]] — where to park\n\n"
    "## people\n- [[people/key-people|Key people]] — standing cast\n"
    "- [[people/jane-doe|Jane Doe]] — an investor another agent keeps\n"
)
HEALTHY = {
    "index.md": INDEX,
    "str/properties/lake.md": page([]),
    "str/operations/lake-sauna.md": page([URL + "c1"]),
    "str/operations/lake-parking.md": page([URL + "c2"]),
    "people/key-people.md": page([URL + "c1"]),
    # Another agent's page, citing a conversation str never ingested: not str's.
    "people/jane-doe.md": page([URL + "c9"]),
}

CASES = [
    ("a healthy corpus", {}, []),
    ("an operations citation the manifest never recorded",
     {"str/operations/lake-parking.md": page([URL + "c9"])},
     ["str/operations/lake-parking: cites c9, which the manifest never recorded"]),
    ("a hub citation the manifest never recorded",
     {"str/properties/lake.md": page([URL + "c9"])},
     ["str/properties/lake: cites c9, which the manifest never recorded"]),
    ("a standing-cast citation the manifest never recorded",
     {"people/key-people.md": page([URL + "c9"])},
     ["people/key-people: cites c9, which the manifest never recorded"]),
    ("a truncated scheme",
     {"str/operations/lake-parking.md": page(["tps://hostex.io/app/conversations/c2"])},
     ["str/operations/lake-parking: cites c2 with a truncated scheme 'tps://'"]),
    ("an index listing no operations pages",
     {"index.md": "# Wiki Index\n\n## people\n- [[people/key-people|Key people]] — cast\n"},
     ["index.md lists no str/operations pages, so every check here would pass on nothing"]),
]


@pytest.mark.parametrize(("case", "changed", "expected"), CASES, ids=[c[0] for c in CASES])
def test_problems_name_the_page_and_the_defect(tmp_path, monkeypatch, capsys, case, changed, expected):
    mod = load()
    files = {**HEALTHY, **changed}
    monkeypatch.setattr(mod.plow_relay, "read", lambda path: files[path.removeprefix("~/Plow/wiki/")])
    (tmp_path / ".manifest.json").write_text(json.dumps(
        {"sources": {"/var/lib/hermes/repo/vault/_raw/hostex/c1.md": {}, "_raw/_archived/hostex/c2.md": {}}}))
    assert mod.main([str(tmp_path), "~/Plow/wiki"]) == (1 if expected else 0)
    assert capsys.readouterr().out.splitlines() == expected


def test_an_unreachable_mac_is_not_a_clean_corpus(tmp_path, monkeypatch, capsys):
    mod = load()

    def unreachable(path):
        raise mod.plow_relay.RelayError("plow_read_file: read failed (device_offline)")

    monkeypatch.setattr(mod.plow_relay, "read", unreachable)
    (tmp_path / ".manifest.json").write_text('{"sources": {}}')
    assert mod.main([str(tmp_path), "~/Plow/wiki"]) == 2
    assert "device_offline" in capsys.readouterr().err
