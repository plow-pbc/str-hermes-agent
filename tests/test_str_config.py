import importlib.machinery, importlib.util, pathlib, tomllib

import pytest

ROOT = pathlib.Path(__file__).parent.parent


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


str_config = _load("str_config", ROOT / "bin" / "str-config")
watch = _load("checkin_watch", ROOT / "bin" / "checkin-watch.py")

PROP = {"hostex_property_id": 12345, "title": "Example Property",
        "timezone": "America/Los_Angeles", "default_checkin_time": "16:00",
        "seam_device_id": "dev-1", "cleaner_name": "Jane",
        "cleaner_access_code_ids": ["code-1"], "cleaners_thread": "Cleaners"}


def test_preserves_unrelated_lines_and_env_wins(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# keep me\nOTHER=1\nSEAM_API_KEY=old\n")
    lines = str_config.apply(
        {"env": {"HOSTEX_TOKEN": "tok-abcdefgh", "SEAM_API_KEY": "new-12345678"}},
        tmp_path, environ={"HOSTEX_TOKEN": "from-compose"})
    text = env.read_text()
    assert text.startswith("# keep me\nOTHER=1\n")
    assert "SEAM_API_KEY=\"new-12345678\"" in text and "SEAM_API_KEY=old" not in text
    assert "HOSTEX_TOKEN" not in text                      # container value wins
    assert any("HOSTEX_TOKEN: set by the container" in l for l in lines)
    assert not any("new-12345678" in l for l in lines)     # summary is masked
    assert any("…678" in l for l in lines)


@pytest.mark.parametrize("secret", ["a=b#c", "has space", "q'uo\"te", "x" * 64])
def test_secret_roundtrip(tmp_path, secret):
    str_config.apply({"env": {"HOSTEX_TOKEN": secret}}, tmp_path, environ={})
    assert str_config.read_setup_env(tmp_path)["HOSTEX_TOKEN"] == secret


def test_read_setup_env_absent(tmp_path):
    assert str_config.read_setup_env(tmp_path) == {}


@pytest.mark.parametrize("environ,rel", [
    ({}, "repo/vault/ops.toml"),
    ({"VAULT": "VAULTDIR"}, "VAULTDIR/ops.toml"),
])
def test_ops_written_where_checkin_watch_reads_it(tmp_path, environ, rel):
    environ = {k: str(tmp_path / v) for k, v in environ.items()}
    str_config.apply({"ops": {"timezone": "America/Chicago", "properties": [PROP]}},
                     tmp_path, environ=environ)
    text = (tmp_path / rel).read_text()
    assert tomllib.loads(text) == {"timezone": "America/Chicago", "properties": [PROP]}
    assert watch.load_ops_text(text) == [PROP]             # the real contract


def test_ops_merge_keeps_timezone_when_only_properties_sent(tmp_path):
    str_config.apply({"ops": {"timezone": "America/Chicago"}}, tmp_path, environ={})
    str_config.apply({"ops": {"properties": [PROP]}}, tmp_path, environ={})
    ops = tomllib.loads((tmp_path / "repo/vault/ops.toml").read_text())
    assert ops["timezone"] == "America/Chicago" and ops["properties"] == [PROP]


@pytest.mark.parametrize("patch", [
    {"env": {"PATH": "/x"}},
    {"ops": {"colour": "red"}},
    {"ops": {"properties": [{**PROP, "extra": 1}]}},
])
def test_unknown_keys_refused(tmp_path, patch):
    with pytest.raises(SystemExit):
        str_config.apply(patch, tmp_path, environ={})
