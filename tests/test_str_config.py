import importlib.machinery, importlib.util, pathlib, stat, subprocess, sys, tomllib

import pytest

ROOT = pathlib.Path(__file__).parent.parent
IMAGE = "sams-str-hermes-agent:local"
sys.path.insert(0, str(ROOT / "bin"))
import str_env  # noqa: E402


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


str_config = _load("str_config", ROOT / "bin" / "str-config")
watch = _load("checkin_watch", ROOT / "bin" / "checkin-watch.py")
poll = _load("hostex_poll", ROOT / "bin" / "hostex-poll.py")
raw = _load("hostex_raw", ROOT / "bin" / "hostex-raw")

PROP = {"hostex_property_id": 12345, "title": "Example Property",
        "timezone": "America/Los_Angeles", "default_checkin_time": "16:00",
        "seam_device_id": "dev-1", "cleaner_name": "Jane",
        "cleaner_access_code_ids": ["code-1"], "cleaners_thread": "Cleaners"}


def setup_env(home):
    return home / "str" / "setup.env"


def test_writes_setup_env_never_dotenv_and_keeps_unrelated_lines(tmp_path):
    (tmp_path / ".env").write_text("OTHER=1\n")
    setup_env(tmp_path).parent.mkdir()
    setup_env(tmp_path).write_text("# keep me\nPLOW_CHAT_APPROVAL_GROUP=old\n")
    str_config.apply({"env": {"PLOW_CHAT_APPROVAL_GROUP": "STR Owners"}}, tmp_path, environ={})
    assert (tmp_path / ".env").read_text() == "OTHER=1\n"
    assert setup_env(tmp_path).read_text() == "# keep me\nPLOW_CHAT_APPROVAL_GROUP=STR Owners\n"


def test_a_new_setup_env_is_private(tmp_path):
    str_config.apply({"env": {"PLOW_CHAT_APPROVAL_GROUP": ""}}, tmp_path, environ={})
    assert stat.S_IMODE(setup_env(tmp_path).stat().st_mode) == 0o600


@pytest.mark.parametrize("key", ["HOSTEX_TOKEN", "SEAM_API_KEY"])
def test_a_key_on_stdin_is_refused(tmp_path, key):
    with pytest.raises(SystemExit, match="--from-latch"):
        str_config.apply({"env": {key: "tok-abcdefgh"}}, tmp_path, environ={})
    assert not setup_env(tmp_path).exists()


def test_dotenv_wins_over_setup_env_as_in_the_gateway(tmp_path):
    assert str_env.read_setup_env(tmp_path) == {}
    setup_env(tmp_path).parent.mkdir()
    setup_env(tmp_path).write_text("HOSTEX_TOKEN=setup\nSEAM_API_KEY=seam\n")
    (tmp_path / ".env").write_text("HOSTEX_TOKEN=dotenv\n")
    assert str_env.read_setup_env(tmp_path) == {"HOSTEX_TOKEN": "dotenv", "SEAM_API_KEY": "seam"}


@pytest.mark.parametrize("reader", [
    poll.read_token, raw.read_token, lambda: watch.read_env_key("HOSTEX_TOKEN"),
], ids=["hostex-poll", "hostex-raw", "checkin-watch"])
def test_a_token_only_setup_wrote_is_found_before_any_restart(tmp_path, monkeypatch, reader):
    setup_env(tmp_path).parent.mkdir()
    setup_env(tmp_path).write_text("HOSTEX_TOKEN=tok-abc.123_x\n")
    (tmp_path / ".env").write_text("PLOW_AGENT_TOKEN=x\n")  # what plow-init writes
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HOSTEX_TOKEN", raising=False)
    assert reader() == "tok-abc.123_x"


@pytest.mark.parametrize("value", ['a"b', "a'b", "a\\b", "a$b", "a#b", "a\nb", "a\rb", " zq9", "zq9 "])
def test_unwritable_values_refused_without_echo(tmp_path, value):
    with pytest.raises(SystemExit) as e:
        str_config.apply({"env": {"SEAM_API_KEY": value}}, tmp_path, environ={}, secrets_ok=True)
    assert "SEAM_API_KEY" in str(e.value) and "zq9" not in str(e.value) and value not in str(e.value)
    assert not setup_env(tmp_path).exists()


@pytest.mark.parametrize("key,value", [
    ("HOSTEX_TOKEN", "aB3-_.=+/:zZ9"),
    ("PLOW_CHAT_GROUP_UIDS", "cht_a=STR Owners,cht_b=Cleaners"),
    ("PLOW_CHAT_APPROVAL_GROUP", ""),                      # "here" clears the group
])
def test_allowed_values_roundtrip(tmp_path, key, value):
    str_config.apply({"env": {key: value}}, tmp_path, environ={}, secrets_ok=True)
    assert str_env.read_setup_env(tmp_path)[key] == value


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


@pytest.mark.parametrize("prop", [
    {k: v for k, v in PROP.items() if k != "cleaners_thread"},
    {**PROP, "default_checkin_time": "4pm"},
], ids=["incomplete", "bad time"])
def test_a_property_checkin_watch_would_refuse_is_never_written(tmp_path, prop):
    (tmp_path / "repo/vault").mkdir(parents=True)
    (tmp_path / "repo/vault/ops.toml").write_text('timezone = "America/Chicago"\n')
    with pytest.raises(SystemExit, match="ops.toml not written"):
        str_config.apply({"ops": {"properties": [prop]}}, tmp_path, environ={})
    assert (tmp_path / "repo/vault/ops.toml").read_text() == 'timezone = "America/Chicago"\n'


def test_a_refused_property_in_an_env_and_ops_patch_writes_neither_file(tmp_path):
    """Step 4 sends env and ops together; a refusal must not leave setup.env half-applied."""
    prop = {k: v for k, v in PROP.items() if k != "cleaners_thread"}
    with pytest.raises(SystemExit, match="ops.toml not written"):
        str_config.apply({"env": {"PLOW_CHAT_GROUP_UIDS": "cht_c=Cleaners"},
                          "ops": {"timezone": "America/Chicago", "properties": [prop]}},
                         tmp_path, environ={})
    assert not setup_env(tmp_path).exists() and not (tmp_path / "repo/vault/ops.toml").exists()


class Mac:
    """The owner's Mac behind plow_relay: read/write on a dict, or a dead relay."""

    def __init__(self, monkeypatch, files=None, down=False):
        self.files = dict(files or {})

        def read(path):
            if down:
                raise OSError("connection refused")
            if path not in self.files:
                raise str_config.plow_relay.RelayError("plow_read_file: not found")
            return self.files[path]

        def write(path, content):
            if down:
                raise OSError("connection refused")
            self.files[path] = content

        monkeypatch.setattr(str_config.plow_relay, "read", read)
        monkeypatch.setattr(str_config.plow_relay, "write", write)


KEYS = str_config.KEY_FILE


def run(tmp_path, *argv):
    return str_config.main(list(argv), {"HERMES_HOME": str(tmp_path)})


@pytest.mark.parametrize("before,after", [
    (None, str_config.TEMPLATE),                                    # first run
    ("HOSTEX_TOKEN=\nSEAM_API_KEY=\n", str_config.TEMPLATE),        # an empty one is refreshed
    ("HOSTEX_TOKEN=tok-abcdefgh\n", "HOSTEX_TOKEN=tok-abcdefgh\n"),  # never over a pasted key
], ids=["absent", "empty", "has a key"])
def test_latch_template(tmp_path, monkeypatch, before, after):
    mac = Mac(monkeypatch, {} if before is None else {KEYS: before})
    assert run(tmp_path, "--latch-template") == 0
    assert mac.files[KEYS] == after


def test_from_latch_saves_the_keys_empties_the_file_and_prints_only_masks(tmp_path, monkeypatch, capsys):
    mac = Mac(monkeypatch, {KEYS: str_config.TEMPLATE.replace("HOSTEX_TOKEN=", "HOSTEX_TOKEN=tok-abcdefgh")
                            .replace("SEAM_API_KEY=", "SEAM_API_KEY=seam\n") + "PATH=/x\n"})
    assert run(tmp_path, "--from-latch") == 0
    assert setup_env(tmp_path).read_text() == "HOSTEX_TOKEN=tok-abcdefgh\nSEAM_API_KEY=seam\n"
    assert mac.files[KEYS] == str_config.TEMPLATE
    out = capsys.readouterr()
    assert out.out == "HOSTEX_TOKEN: …fgh\nSEAM_API_KEY: …***\n"
    assert "abcde" not in out.out + out.err


@pytest.mark.parametrize("files,down,says", [
    ({KEYS: str_config.TEMPLATE}, False, "has no key in it yet"),
    ({}, False, "couldn't read ~/Plow/str-keys.env"),
    ({}, True, "couldn't reach your Mac"),
], ids=["empty", "absent", "relay down"])
def test_from_latch_failures_are_one_owner_sentence(tmp_path, monkeypatch, capsys, files, down, says):
    Mac(monkeypatch, files, down=down)
    assert run(tmp_path, "--from-latch") == 2
    err = capsys.readouterr().err
    assert says in err and err.count("\n") == 1 and "Traceback" not in err
    assert not setup_env(tmp_path).exists()


def test_from_latch_refuses_a_bad_value_and_leaves_the_file_for_another_try(tmp_path, monkeypatch):
    mac = Mac(monkeypatch, {KEYS: 'HOSTEX_TOKEN="tok#abc$x"\n'})
    with pytest.raises(SystemExit, match="HOSTEX_TOKEN value"):
        run(tmp_path, "--from-latch")
    assert mac.files[KEYS] == 'HOSTEX_TOKEN="tok#abc$x"\n' and not setup_env(tmp_path).exists()


def test_the_agent_writes_setup_env_beside_a_dotenv_it_cannot_write():
    """In the image, as the agent's uid, against the root:hermes 0640 .env
    plow-init leaves in the root-owned sticky home at every boot."""
    script = """
set -e
printf 'PLOW_AGENT_TOKEN=x\\n' > /var/lib/hermes/.env
chown root:hermes /var/lib/hermes/.env && chmod 0640 /var/lib/hermes/.env
before=$(sha256sum /var/lib/hermes/.env)
echo '{"env": {"PLOW_CHAT_APPROVAL_GROUP": "STR Owners"}}' \
  | /command/s6-setuidgid hermes env HERMES_HOME=/var/lib/hermes /opt/plow/str/bin/str-config
[ "$(sha256sum /var/lib/hermes/.env)" = "$before" ] && echo dotenv-untouched
stat -c '%U %a' /var/lib/hermes/str/setup.env
cat /var/lib/hermes/str/setup.env
"""
    out = subprocess.run(["docker", "run", "--rm", "--entrypoint", "sh", IMAGE, "-c", script],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == ["PLOW_CHAT_APPROVAL_GROUP: STR Owners", "dotenv-untouched",
                                       "hermes 600", "PLOW_CHAT_APPROVAL_GROUP=STR Owners"]
