"""Every script that reads a Hostex token sees what the gateway sees -- through
bin/str_env.py's dotenv -- so a hand-written quoted value works, and so does a
token that setup wrote only to str/setup.env."""
import importlib.machinery
import importlib.util
import pathlib

import pytest

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"

# (script, reader, args)
READERS = [("hostex-poll.py", "read_token", ()), ("hostex-raw", "read_token", ()),
           ("checkin-watch.py", "read_env_key", ("HOSTEX_TOKEN",))]


def _load(script):
    loader = importlib.machinery.SourceFileLoader(script.replace("-", "_").removesuffix(".py"), str(BIN / script))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("where", [".env", "str/setup.env"])
@pytest.mark.parametrize("script,reader,args", READERS, ids=[r[0] for r in READERS])
def test_a_quoted_token_is_read_unquoted(script, reader, args, where, tmp_path, monkeypatch):
    (tmp_path / "str").mkdir()
    (tmp_path / where).write_text('HOSTEX_TOKEN="tok-x"\n')
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HOSTEX_TOKEN", raising=False)
    assert getattr(_load(script), reader)(*args) == "tok-x"
