"""The shared Hostex transport: one redirect policy, exercised end to end.

Relocated from test_hostex_poll.py / test_hostex_raw.py when the three
per-script transport copies collapsed into bin/hostex_api.py; the per-script
wrappers are one-line delegates read at a glance, so only the shared client
is driven here.

Two loopback servers rather than a mock of urllib: the behaviour under test
belongs to the redirect handler, and mocking it would only assert that the
code calls what we already decided to call. The elsewhere server answers 500,
not 200 — a followed redirect must both record the header and raise, so
`received` is what separates refusal from replay.
"""
from __future__ import annotations

import http.server
import importlib.util
import json
import pathlib
import sys
import threading
import urllib.error

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))
import hostex_api


def test_a_redirect_is_refused_and_the_token_is_not_replayed(monkeypatch):
    received = {}
    ports = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/v3/"):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{ports['elsewhere']}/taken")
                self.end_headers()
                return
            received["token"] = self.headers.get("Hostex-Access-Token")
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args):
            pass

    origin = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    elsewhere = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    ports["elsewhere"] = elsewhere.server_address[1]
    for server in (origin, elsewhere):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(
            hostex_api, "BASE", f"http://127.0.0.1:{origin.server_address[1]}/v3")
        with pytest.raises(urllib.error.HTTPError) as caught:
            hostex_api.get("/conversations", "SECRET", "test/1.0")
        assert received == {}   # first, so a regression fails with the leak
        assert "refusing redirect" in str(caught.value)
    finally:
        for server in (origin, elsewhere):
            server.shutdown()
            server.server_close()


def _seam_post(monkeypatch, base):
    """turnover-poll's Seam client, which shares this transport."""
    spec = importlib.util.spec_from_file_location("turnover_poll", BIN / "turnover-poll.py")
    poll = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(poll)
    monkeypatch.setattr(poll, "SEAM", base)
    return lambda: poll.seam_client("SECRET")("/devices/get", {"device_id": "dev-1"})


CLIENTS = {
    "hostex": lambda monkeypatch, base: (
        monkeypatch.setattr(hostex_api, "BASE", base + "/v3")
        or (lambda: hostex_api.get("/conversations", "SECRET", "test/1.0"))),
    "seam": _seam_post,
}


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("responses, outcome", [
    (["stall", "stall", "ok"], {"data": {"conversations": []}}),
    (["drop", "ok"], {"data": {"conversations": []}}),
    (["stall", "stall", "stall"], TimeoutError),
    (["500"], urllib.error.HTTPError),
])
def test_a_stall_is_retried_and_a_status_is_not(monkeypatch, client, responses, outcome):
    """If a stall or a dropped connection were not retried, one bad read
    would fail the 2-minute poll and the scheduler would text the owners a
    failure report; if a status were retried, a real refusal would only
    surface three times slower."""
    requests = []
    released = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            response = responses[len(requests) - 1]
            if response == "drop":    # closed with no status line: RemoteDisconnected
                self.close_connection = True
                return
            if response == "500":
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            if response == "stall":   # headers out, body withheld until teardown
                released.wait(5)      # bounded so a lost client timeout fails, not hangs
                return
            self.wfile.write(json.dumps(outcome).encode())

        do_POST = do_GET

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        call = CLIENTS[client](monkeypatch, f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setattr(hostex_api, "TIMEOUT_S", 0.2)
        monkeypatch.setattr(hostex_api, "BACKOFF_S", 0)
        if isinstance(outcome, dict):
            assert call() == outcome
        else:
            with pytest.raises(outcome):
                call()
        assert len(requests) == len(responses)
    finally:
        released.set()
        server.shutdown()
        server.server_close()
