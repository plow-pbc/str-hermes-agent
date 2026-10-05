"""bin/setup-discover: canned API payloads -> the JSON setup offers as choices. No network."""
import importlib.machinery, importlib.util, io, json, pathlib, urllib.error

import pytest

ROOT = pathlib.Path(__file__).parent.parent
_loader = importlib.machinery.SourceFileLoader("setup_discover", str(ROOT / "bin" / "setup-discover"))
discover = importlib.util.module_from_spec(importlib.util.spec_from_loader("setup_discover", _loader))
_loader.exec_module(discover)

ENV = {"HOSTEX_TOKEN": "tok-abcdefgh", "SEAM_API_KEY": "seam-abcdefgh", "PLOW_AGENT_TOKEN": "plow-abcdefgh",
       "PLOW_API_BASE": "https://plow.example"}


def member(key, name=None):
    return {"type": "member", "provider_key": key, "display_name": name}


def chat(uid, people, name=None, provider="imessage", status="active"):
    me = {"type": "agent", "relationship": "self", "line": {"provider_type": provider}}
    return {"uid": uid, "status": status, "display_name": name, "participants": [me, *people]}


CHATS = {"has_more": False, "data": [
    chat("c-dm", [member("+15550000001")]),
    chat("c-group", [member("+15550000001", "Pat"), member("+15550000002")], name="Cleaners"),
    chat("c-unnamed", [member("+15550000001"), member("+15550000002")], name="+15550000001, +15550000002"),
    chat("c-email", [member("a@example.com"), member("b@example.com")], provider="email"),
    chat("c-closed", [member("+15550000001"), member("+15550000002")], status="archived"),
]}

CASES = [
    ("latch", [], {"PLOW_MCP_URL": " https://mcp.example "}, None, {"configured": True}),
    ("latch", [], {"PLOW_MCP_URL": "  "}, None, {"configured": False}),
    ("hostex-properties", [], {}, {"data": {"properties": [
        {"id": 1, "title": "Example Cabin", "timezone": "America/Denver", "default_checkin_time": "16:00"},
        {"id": 2, "title": None}]}},
     [{"hostex_property_id": 1, "title": "Example Cabin", "timezone": "America/Denver", "default_checkin_time": "16:00"},
      {"hostex_property_id": 2, "title": "", "timezone": None, "default_checkin_time": None}]),
    ("seam-devices", [], {}, {"devices": [{"device_id": "dev-1", "properties": {"name": "Front Door"}}]},
     [{"seam_device_id": "dev-1", "name": "Front Door"}]),
    # both of Seam's collections answer the same canned page; sorted by name, case-insensitive
    ("seam-codes", ["dev-1"], {}, {"access_codes": [{"access_code_id": "ac-2", "name": "guest"},
                                                    {"access_code_id": "ac-1", "name": "Cleaner"}]},
     [{"access_code_id": "ac-1", "name": "Cleaner"}, {"access_code_id": "ac-1", "name": "Cleaner"},
      {"access_code_id": "ac-2", "name": "guest"}, {"access_code_id": "ac-2", "name": "guest"}]),
    ("groups", [], {}, CHATS,
     [{"chat_uid": "c-group", "title": "Cleaners", "members": ["Pat", "+15550000002"]},
      {"chat_uid": "c-unnamed", "title": "+15550000001, +15550000002",
       "members": ["+15550000001", "+15550000002"]}]),
]


def run(tmp_path, argv, env, fake):
    return discover.main(argv, {**ENV, "HERMES_HOME": str(tmp_path), **env}, fetch=fake)


@pytest.mark.parametrize("name,args,env,payload,expected", CASES)
def test_command_prints_choices(tmp_path, capsys, name, args, env, payload, expected):
    assert run(tmp_path, [name, *args], env, lambda url, headers: payload) == 0
    assert json.loads(capsys.readouterr().out) == expected


@pytest.mark.parametrize("name,service", [("hostex-properties", "Hostex"), ("seam-devices", "Seam"), ("groups", "Plow")])
def test_rejected_token_is_one_sentence(tmp_path, capsys, name, service):
    def reject(url, headers):
        raise urllib.error.HTTPError(url, 401, "no", {}, io.BytesIO())
    assert run(tmp_path, [name], {}, reject) == 2
    err = capsys.readouterr().err
    assert f"{service} rejected the token" in err and err.count("\n") == 1
    assert "abcdefgh" not in err and "Traceback" not in err


@pytest.mark.parametrize("code,expected", [(401, "Seam rejected the token"), (403, "Seam refused the request (HTTP 403)"),
                                           (500, "Seam refused the request (HTTP 500)")])
def test_status_wording(tmp_path, capsys, code, expected):
    def fail(url, headers):
        raise urllib.error.HTTPError(url, code, "no", {}, io.BytesIO())
    assert run(tmp_path, ["seam-devices"], {}, fail) == 2
    err = capsys.readouterr().err
    assert expected in err and (code == 401) == ("check the key" in err)


@pytest.mark.parametrize("name,payload", [("hostex-properties", {}), ("seam-devices", {"nope": 1}),
                                          ("seam-codes", {}), ("groups", {"data": []})])
def test_malformed_payload_is_one_sentence(tmp_path, capsys, name, payload):
    args = ["dev-1"] if name == "seam-codes" else []
    assert run(tmp_path, [name, *args], {}, lambda url, headers: payload) == 2
    err = capsys.readouterr().err
    assert "answered something unexpected" in err and "Traceback" not in err


def test_non_json_body_is_one_sentence(tmp_path, capsys, monkeypatch):
    class Body:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"error code: 1010"
    monkeypatch.setattr(discover.hostex_api.OPENER, "open", lambda req, timeout: Body())
    assert discover.main(["seam-devices"], {**ENV, "HERMES_HOME": str(tmp_path)}) == 2
    assert "Seam answered something unexpected" in capsys.readouterr().err


def test_fetch_names_itself_to_the_api(monkeypatch):
    sent = []
    class Body:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"
    monkeypatch.setattr(discover.hostex_api.OPENER, "open", lambda req, timeout: sent.append(req) or Body())
    discover.fetch("https://api.example/x", {"Authorization": "Bearer t"})
    assert sent[0].get_header("User-agent") == "str-setup-discover/1"
    assert sent[0].get_header("Authorization") == "Bearer t"


def test_setup_env_fills_in_and_process_env_wins(tmp_path):
    (tmp_path / ".env").write_text("HOSTEX_TOKEN=from-file\nSEAM_API_KEY=seam-file\n")
    seen = []
    discover.main(["hostex-properties"], {"HERMES_HOME": str(tmp_path), "HOSTEX_TOKEN": "from-container"},
                  fetch=lambda url, headers: seen.append(headers) or {"data": {"properties": []}})
    discover.main(["seam-devices"], {"HERMES_HOME": str(tmp_path)},
                  fetch=lambda url, headers: seen.append(headers) or {"devices": []})
    assert seen[0]["Hostex-Access-Token"] == "from-container"
    assert seen[1]["Authorization"] == "Bearer seam-file"


def test_missing_secret_and_unreachable_exit_2(tmp_path, capsys):
    assert discover.main(["seam-devices"], {"HERMES_HOME": str(tmp_path)}, fetch=None) == 2
    assert "SEAM_API_KEY is not set" in capsys.readouterr().err

    def down(url, headers):
        raise urllib.error.URLError("dns")
    assert run(tmp_path, ["groups"], {}, down) == 2
    assert "could not reach Plow" in capsys.readouterr().err
