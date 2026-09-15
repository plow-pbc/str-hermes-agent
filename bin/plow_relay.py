#!/usr/bin/env python3
"""The owner's Mac, for scripts: the same `plow_*` tools the model calls.

str's wiki lives on the owner's Mac at ~/Plow/wiki, and the container reaches it
only through the `plow` MCP server that plow-init configures over Plow's relay.
The model calls those tools itself; the nightly's deterministic steps call them
through this. Latch's server is stateless, so each operation is one JSON-RPC
`tools/call`, sent through hostex_api's opener so the bearer token never follows
a redirect.

  plow_relay.py read PATH                         the file's content on stdout
  plow_relay.py write PATH < CONTENT
  plow_relay.py run [--write PATH]... [--network] -- ARGV...
                                                  the command's output; exits with its code

Exit 2 means the relay could not answer: a transport failure, a refused tool
call, or a command still running at the deadline. `wiki validate` exits 1 for
invalid pages, so the two stay distinguishable.

Latch sandboxes every command. A command that writes must name its write paths,
and one that reaches the network must say so, or the kernel refuses it; `~` is
expanded on the Mac side.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

from hostex_api import OPENER

TIMEOUT_S = 60
# How long Latch holds a command call open before answering `pending` with a
# handle to poll. The deadline for the whole command is `run`'s own `timeout`.
WAIT_MS = 20000
POLL_S = 1


class RelayError(RuntimeError):
    """The relay answered, but not with what was asked for."""


def call(tool: str, arguments: dict) -> dict:
    """One tool call; its JSON payload, or RelayError carrying Latch's diagnosis."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }).encode()
    request = urllib.request.Request(os.environ["PLOW_MCP_URL"], data=body, method="POST", headers={
        "Authorization": "Bearer " + os.environ["PLOW_AGENT_TOKEN"],
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    with OPENER.open(request, timeout=TIMEOUT_S) as response:
        raw = response.read().decode()
    if raw.lstrip().startswith(("event:", "data:")):  # the streamable-HTTP framing
        raw = "\n".join(line[5:].strip() for line in raw.splitlines() if line.startswith("data:"))
    message = json.loads(raw)
    if "error" in message:
        raise RelayError(f"{tool}: {message['error'].get('message')}")
    result = message["result"]
    payload = json.loads(next(c["text"] for c in result["content"] if c.get("type") == "text"))
    if result.get("isError"):
        diagnosis = payload.get("diagnosis") or {}
        raise RelayError(f"{tool}: {payload.get('error')} ({diagnosis.get('cause', 'no diagnosis')})")
    return payload


def read(path: str) -> str:
    return call("plow_read_file", {"path": path})["content"]


def write(path: str, content: str) -> None:
    call("plow_write_file", {"path": path, "content": content})


def run(argv: list[str], write_paths=(), network: bool = False, timeout: float = 600) -> tuple[int, str]:
    """(exit code, output) of ARGV on the Mac.

    A call Latch cannot finish inside WAIT_MS comes back `pending`, polled with
    plow_get_result, or `running`, polled with plow_get_output. plow_get_result
    wraps a ready result as {"status": "ready", "result": {...}}, which may itself
    be running or already completed, so it is unwrapped before the status is read
    again: the loop plow-say (plow-pbc/plow) follows.
    """
    deadline = time.monotonic() + timeout
    what = " ".join(argv[:2])
    result = call("plow_run_command", {
        "argv": argv, "write_paths": list(write_paths), "network": network,
        "wait_ms": WAIT_MS, "goal": f"str: {what}",
    })
    while result.get("status") in ("pending", "running"):
        if time.monotonic() > deadline:
            raise RelayError(f"{what} still running at the deadline")
        time.sleep(POLL_S)
        poll = "plow_get_result" if result["status"] == "pending" else "plow_get_output"
        result = call(poll, {"handle": result["handle"]})
        if result.get("status") == "ready":
            result = result["result"]
    if result.get("status") != "completed":
        raise RelayError(f"{what} ended {result.get('status')!r}")
    return result["exit_code"], result["output"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plow_relay.py")
    ops = parser.add_subparsers(dest="op", required=True)
    ops.add_parser("read").add_argument("path")
    ops.add_parser("write").add_argument("path")
    run_op = ops.add_parser("run")
    run_op.add_argument("--write", action="append", default=[], metavar="PATH")
    run_op.add_argument("--network", action="store_true")
    run_op.add_argument("argv", nargs="+")
    args = parser.parse_args(argv)
    try:
        if args.op == "read":
            sys.stdout.write(read(args.path))
            return 0
        if args.op == "write":
            write(args.path, sys.stdin.read())
            return 0
        code, output = run(args.argv, args.write, args.network)
        sys.stdout.write(output)
        return code
    except (RelayError, OSError) as e:
        print(f"plow_relay: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
