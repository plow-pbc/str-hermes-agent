"""The shared Hostex transport: one redirect policy, exercised end to end.

Relocated from test_hostex_poll.py / test_hostex_raw.py when the three
per-script transport copies collapsed into bin/hostex_api.py. Driven through
each script's own entry point rather than the handler directly, because the
regression that matters is the wiring — a script rewritten back onto a bare
urlopen must fail this.

Two loopback servers rather than a mock of urllib: the behaviour under test
belongs to the redirect handler, and mocking it would only assert that the
code calls what we already decided to call. The elsewhere server answers 500,
not 200 — a followed redirect must both record the header and raise, so
`received` is what separates refusal from replay.
"""
from __future__ import annotations

import http.server
import importlib.machinery
import importlib.util
import pathlib
import socketserver
import sys
import threading
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The scripts import hostex_api by name off their own directory; importing the
# same module object here (one sys.modules entry) is what makes monkeypatching
# its BASE reach every script's transport at once.
sys.path.insert(0, str(ROOT / "bin"))
import hostex_api


def load(name: str, filename: str):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / "bin" / filename))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


poll = load("hostex_poll_transport", "hostex-poll.py")
raw = load("hostex_raw_transport", "hostex-raw")
watch = load("checkin_watch_transport", "checkin-watch.py")


@pytest.mark.parametrize("call", [
    lambda p, t: hostex_api.get(p, t, "test/1.0"),
    lambda p, t: poll.api_get(p, t),
    lambda p, t: raw.api_get(p, t),
    lambda p, t: watch.hostex_get(p, t),
], ids=["hostex_api", "hostex-poll", "hostex-raw", "checkin-watch"])
def test_a_redirect_is_refused_and_the_token_is_not_replayed(monkeypatch, call):
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
            call("/conversations", "SECRET")
        assert received == {}   # first, so a regression fails with the leak
        assert "refusing redirect" in str(caught.value)
    finally:
        for server in (origin, elsewhere):
            server.shutdown()
            server.server_close()
