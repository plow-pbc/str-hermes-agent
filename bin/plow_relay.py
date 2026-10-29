#!/usr/bin/env python3
"""The owner's Mac, for scripts, through the latch CLI.

str's wiki lives on the owner's Mac at ~/Plow/wiki, and the container reaches it
only through latch, which the image ships and boot logs in (bin/latch-boot). The
model calls latch itself; the nightly's deterministic steps call it through this.

  plow_relay.py read PATH                         the file's content on stdout
  plow_relay.py write PATH < CONTENT
  plow_relay.py run [--write PATH]... [--network] -- ARGV...
                                                  the command's output; exits with its code

Exit 2 means latch could not answer: the Mac unreachable, a refusal, or a
command still running at the deadline. `wiki validate` exits 1 for invalid
pages, so the two stay distinguishable.

The host is $LATCH_HOST and the chat the gatekeeper reads is $LATCH_PLOW_CHAT,
both latch's own settings, which boot publishes. Latch sandboxes every command: a
command that writes must name its write paths, and one that reaches the network
must say so; `~` in a path is expanded on the Mac, never in ARGV.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# latch exec's own ceiling, and every call's: a Mac gone quiet mid-call is exit 2,
# never a nightly that hangs.
TIMEOUT_S = 600


class RelayError(RuntimeError):
    """latch answered, but not with what was asked for. `code` is latch's own
    error code when it gave one (`not_found`, `host_offline`, ...)."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def latch(*args: str) -> dict:
    """One latch call's JSON, or RelayError carrying latch's own error code."""
    try:
        done = subprocess.run(["latch", *args], capture_output=True, text=True, timeout=TIMEOUT_S + 30)
    except subprocess.TimeoutExpired:
        raise RelayError(f"latch {args[0]}: no answer in {TIMEOUT_S + 30}s") from None
    if done.returncode == 0:
        return json.loads(done.stdout)
    try:
        error = json.loads(done.stderr.strip().splitlines()[-1])["error"]
        code, message = error["code"], error["message"]
    except (IndexError, KeyError, TypeError, ValueError):
        raise RelayError(f"latch {args[0]}: {done.stderr.strip() or f'exited {done.returncode}'}") from None
    raise RelayError(f"latch {args[0]}: {code}: {message}", code)


def read(path: str) -> str:
    with tempfile.TemporaryDirectory() as work:
        local = Path(work) / "file"
        latch("file", "get", path, str(local))
        return local.read_text()


def write(path: str, content: str) -> None:
    with tempfile.TemporaryDirectory() as work:
        local = Path(work) / Path(path).name
        local.write_text(content)
        latch("file", "put", str(local), path)


def run(argv: list[str], write_paths=(), network: bool = False) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) of ARGV on the Mac."""
    grants = [arg for path in write_paths for arg in ("--write", path)]
    result = latch("exec", *grants, *(["--network"] if network else []),
                   "--timeout", str(TIMEOUT_S), "--", *argv)
    if result.get("exit_code") is None:
        raise RelayError(f"{' '.join(argv[:2])} ended by signal {result.get('signal')}")
    return result["exit_code"], result["stdout"], result["stderr"]


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
        code, out, err = run(args.argv, args.write, args.network)
        sys.stdout.write(out)
        sys.stderr.write(err)
        return code
    except (RelayError, OSError) as e:
        print(f"plow_relay: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
