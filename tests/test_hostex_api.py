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
import pathlib
import socketserver
import sys
import threading
import urllib.error

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "bin"))
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

    origin = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    elsewhere = socketserver.TCPServer(("127.0.0.1", 0), Handler)
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
