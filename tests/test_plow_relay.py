"""bin/plow_relay.py against a loopback stand-in for the Latch relay.

A real HTTP server rather than a mock of urllib: what matters is what goes over
the wire (the bearer, the tool arguments) and what the CLI does with each shape
Latch answers in. The response payloads are the shapes measured against the live
relay on 2026-09-14.
"""
from __future__ import annotations

import http.server
import io
import json
import pathlib
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "bin"))
import plow_relay


def _text(payload, is_error=False):
    result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    if is_error:
        result["isError"] = True
    return {"jsonrpc": "2.0", "id": 1, "result": result}


@pytest.fixture
def relay(monkeypatch):
    """Answers each tool from a per-tool queue and records every call."""
    script: dict[str, list] = {}
    calls: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            params = body["params"]
            calls.append({"tool": params["name"], "arguments": params["arguments"],
                          "authorization": self.headers.get("Authorization")})
            answer = script[params["name"]].pop(0)
            if isinstance(answer, int):
                self.send_response(answer)
                self.send_header("Location", "http://127.0.0.1:9/elsewhere")
                self.end_headers()
                return
            data = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("PLOW_MCP_URL", f"http://127.0.0.1:{server.server_address[1]}/mcp")
    monkeypatch.setenv("PLOW_AGENT_TOKEN", "SECRET")
    monkeypatch.setattr(plow_relay, "POLL_S", 0)
    yield script, calls
    server.shutdown()


def test_read_prints_the_file_and_carries_the_bearer(relay, capsys):
    script, calls = relay
    script["plow_read_file"] = [_text({"content": "# Wiki Index\n", "path": "/Users/o/Plow/wiki/index.md",
                                       "status": "completed"})]
    assert plow_relay.main(["read", "~/Plow/wiki/index.md"]) == 0
    assert capsys.readouterr().out == "# Wiki Index\n"
    assert calls == [{"tool": "plow_read_file", "arguments": {"path": "~/Plow/wiki/index.md"},
                      "authorization": "Bearer SECRET"}]


def test_write_sends_stdin_as_the_content(relay, monkeypatch):
    script, calls = relay
    script["plow_write_file"] = [_text({"bytes": 6, "path": "/Users/o/Plow/wiki/x.md", "status": "completed"})]
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello\n"))
    assert plow_relay.main(["write", "~/Plow/wiki/x.md"]) == 0
    assert calls[0]["arguments"] == {"path": "~/Plow/wiki/x.md", "content": "hello\n"}


RUNS = [
    ("finished inside the wait",
     {"plow_run_command": [_text({"exit_code": 1, "output": "people/x.md: missing required field: tags\n",
                                  "status": "completed"})]},
     1, "people/x.md: missing required field: tags\n", []),
    ("pending, then resolved through the command's own handle",
     {"plow_run_command": [_text({"status": "pending", "handle": "OUTER", "retry_after_ms": 1000})],
      "plow_get_result": [_text({"status": "pending", "handle": "OUTER"}),
                          _text({"status": "ready", "result": {"handle": "INNER", "status": "running"}})],
      "plow_get_output": [_text({"status": "running", "output": ""}),
                          _text({"exit_code": 0, "output": "snapshot 62060f5\n", "status": "completed"})]},
     0, "snapshot 62060f5\n", ["INNER", "INNER"]),
]


@pytest.mark.parametrize(("case", "answers", "code", "output", "polled"), RUNS, ids=[r[0] for r in RUNS])
def test_run_exits_with_the_commands_code_and_prints_its_output(
        relay, capsys, case, answers, code, output, polled):
    script, calls = relay
    script.update(answers)
    assert plow_relay.main(["run", "--write", "~/Plow/wiki", "--", "wiki", "index"]) == code
    assert capsys.readouterr().out == output
    assert calls[0]["arguments"]["argv"] == ["wiki", "index"]
    assert calls[0]["arguments"]["write_paths"] == ["~/Plow/wiki"]
    assert [c["arguments"]["handle"] for c in calls if c["tool"] == "plow_get_output"] == polled


FAILURES = [
    ("a refused tool call names Latch's diagnosis",
     {"plow_read_file": [_text({"error": "read failed: ENOENT", "diagnosis": {"cause": "not_found"}}, is_error=True)]},
     ["read", "~/Plow/wiki/missing.md"], "read failed: ENOENT (not_found)"),
    ("a redirect is refused, not followed with the token",
     {"plow_read_file": [302]}, ["read", "~/Plow/wiki/index.md"], "refusing redirect"),
]


@pytest.mark.parametrize(("case", "answers", "argv", "message"), FAILURES, ids=[f[0] for f in FAILURES])
def test_a_relay_that_cannot_answer_exits_2_and_says_why(relay, capsys, case, answers, argv, message):
    script, calls = relay
    script.update(answers)
    assert plow_relay.main(argv) == 2
    err = capsys.readouterr().err
    assert message in err
    assert "SECRET" not in err
    assert len(calls) == 1  # the redirect target never saw a request


def test_a_command_still_running_at_the_deadline_is_a_relay_failure(relay, monkeypatch):
    script, _ = relay
    script["plow_run_command"] = [_text({"status": "pending", "handle": "OUTER"})]
    script["plow_get_result"] = [_text({"status": "pending", "handle": "OUTER"})] * 3
    clock = iter([0, 0, 1000, 2000])
    monkeypatch.setattr(plow_relay.time, "monotonic", lambda: next(clock))
    with pytest.raises(plow_relay.RelayError, match="still running at the deadline"):
        plow_relay.run(["wiki", "snapshot"], timeout=1)
