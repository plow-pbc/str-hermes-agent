"""Nothing tracked may still reach for the home the cutover retired.

This class has now surfaced in three consecutive review rounds -- each time as a
different handful of files, each time found by a human or a bot reading rather
than by anything that fails. `~/.hermes` still EXISTS on the deploy host, frozen
at the cutover, so every one of these reads succeeds and returns stale data: the
smoke check answered 312 connections from a log nothing writes, and
skills-snapshot would archive a skill store the agent stopped writing to.

That is why this is a check rather than a fourth sweep. The sweeps kept working
and kept being incomplete; a grep that runs every time is what makes the class
stay closed.
"""
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The home is a named volume. Its host path is gone; `~/.plow-credentials-str`
# and `~/hermes-vault` are real host paths and are deliberately not matched.
RETIRED_HOME = re.compile(r"~/\.hermes\b|\$HOME/\.hermes\b|Path\.home\(\)\s*/\s*[\"']\.hermes[\"']")

# The container writes agent.log. There is no gateway.log anywhere -- naming it
# is how a diagnostic reads as "0 matches" when it means "wrong file".
RETIRED_LOG = re.compile(r"gateway\.log")

# Only this file may name them, to say they are retired.
EXEMPT = {"tests/test_retired_home.py"}

# Scoped to what a program or a reader EXECUTES, not to prose. A comment saying
# "agent-mgr used to bind ~/.hermes" is history and accurate; a `grep` aimed at
# that path is the defect. So: non-comment lines in executables, and fenced
# blocks in documentation -- which is where the smoke check's stale grep lived,
# the most dangerous instance of this class so far.
EXECUTABLE_DIRS = ("bin/", "scripts/", "docker/")
EXECUTABLE_FILES = ("justfile", "compose.yml", "Dockerfile")


def executable_lines(rel: str, body: str):
    """(lineno, line) for every position that runs, per file kind."""
    if rel.endswith(".md"):
        fenced = False
        for n, line in enumerate(body.splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                yield n, line
        return
    if not (rel.startswith(EXECUTABLE_DIRS) or rel in EXECUTABLE_FILES):
        return
    for n, line in enumerate(body.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        yield n, line


def tracked_files():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         check=True, capture_output=True, text=True).stdout
    return [p for p in out.splitlines() if p not in EXEMPT]


def test_nothing_executable_reaches_for_the_retired_host_home():
    offenders = []
    for rel in tracked_files():
        try:
            body = (ROOT / rel).read_text()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        # The home path can legitimately appear in prose describing what the
        # cutover retired, so it is only an offence where something executes.
        for n, line in executable_lines(rel, body):
            if RETIRED_HOME.search(line):
                offenders.append(f"{rel}:{n}: [host home] {line.strip()[:100]}")
        # gateway.log has no legitimate mention at all -- no such file is
        # written anywhere -- so it is an offence in prose too. A troubleshooting
        # table row is not fenced and is still a command someone runs, which is
        # exactly where this one hid.
        for n, line in enumerate(body.splitlines(), 1):
            if RETIRED_LOG.search(line):
                offenders.append(f"{rel}:{n}: [gateway.log] {line.strip()[:100]}")
    assert not offenders, (
        "these run against state the cutover retired; the host copy still exists "
        "so every read succeeds and returns stale data:\n  " + "\n  ".join(offenders))
