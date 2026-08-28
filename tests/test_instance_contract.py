"""What this repo must be now that deployment lives in plow-pbc/agent-mgr.

These are the invariants three sibling repos had and this one did not, while it
was the one breaking production.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

QUOTED = re.compile(r"'[^']*'" + r'|"[^"]*"')

# Every file that can start a container or tell a person how to. Named once:
# spelling it out per test is how the justfile-only scan kept `bin/ingest-all`
# out of reach.


def _scanned():
    return [ROOT / "justfile", ROOT / "README.md",
            *sorted((ROOT / "bin").iterdir()),
            # iterdir, not glob("*.sh"): scripts/no-nightly-running is
            # extensionless, so a glob leaves the script that owns the
            # container-transition rule invisible to every scanner here.
            *sorted((ROOT / "scripts").iterdir()),
            # The skills are agent-executed operator paths -- a stale command in
            # one is run, not just read.
            *sorted((ROOT / ".claude" / "skills").rglob("*.md"))]


def _source_lines():
    """Every scannable line in the repo, as (path, lineno, text).

    One generator rather than the same skip-non-files / read / enumerate
    preamble in four tests, where the mechanics could drift apart
    independently -- which is the shape that kept the earlier scanners
    pointed away from where the defect lived.
    """
    for path in _scanned():
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            yield path, number, line


def _lines(name):
    return (ROOT / name).read_text().splitlines()


def descriptor():
    """agent.env's settings, comments and blanks dropped."""
    return dict(
        line.split("=", 1) for line in _lines("agent.env")
        if line.strip() and not line.lstrip().startswith("#")
    )


def test_the_descriptor_is_the_whole_contract_with_agent_mgr():
    """One table, because these were five separate parses of the same six lines.

    Each entry is load-bearing:

    - AGENT_HOME / AGENT_CONTAINER are legacy overrides. This agent predates the
      ~/.hermes-<name> convention; renaming either would move live state --
      auth.json, sessions, the Hostex cursor -- under a running gateway.
    - AGENT_TZ is stated, not inherited. agent-mgr defaults to this zone, but a
      default this repo cannot see is not a guarantee it can make: every
      schedule here is a Pacific wall-clock expression, and `0 3 * * *` under
      UTC fires at 8 PM Pacific, during peak guest messaging.
    - STR_REPO is the DEPLOY clone. Code is written in ~/Hacking and runs from
      ~/services; a runtime aimed at the former goes stale the first time the
      branch moves.
    - AGENT_CONFIG is why `agent-mgr restore str` works at all -- this agent's
      config lives under runtime/, and without naming it this repo kept a second
      installer that hardcoded the path and the home.
    - AGENT_RESTORE_HOOK is what makes one command the whole deploy.
    """
    settings = descriptor()
    expected = {
        "AGENT_HOME": "$HOME/.hermes",
        "AGENT_CONTAINER": "hermes",
        "AGENT_PROJECT": "hermes-str",
        "AGENT_TZ": "America/Los_Angeles",
        "AGENT_IMAGE": "sams-str-hermes-agent:local",
        "STR_VAULT": "$HOME/hermes-vault",
        "STR_REPO": "$HOME/services/sams-str-hermes-agent",
        "AGENT_CONFIG": "runtime/config.yaml",
        "AGENT_RESTORE_HOOK": "scripts/restore-runtime-config.sh",
    }
    assert {k: settings.get(k) for k in expected} == expected

    # The two paths agent-mgr resolves against this repo have to exist, or the
    # failure surfaces only when someone runs a deploy.
    assert (ROOT / settings["AGENT_CONFIG"]).is_file()
    hook = ROOT / settings["AGENT_RESTORE_HOOK"]
    assert hook.is_file() and hook.stat().st_mode & 0o111, "the hook is not executable"

    # No dev checkout anywhere in the settings.
    assert not any("Hacking" in v for v in settings.values())

    # The legacy overrides are deliberate, and the file has to say so.
    assert "legacy" in (ROOT / "agent.env").read_text().lower()


def test_the_repo_no_longer_defines_its_own_compose_service():
    """Deployment comes from agent-mgr; a second definition here would drift from
    the one the gateway actually runs under."""
    assert not (ROOT / "compose.yml").exists()


def test_nothing_reaches_docker_compose_without_agent_mgr():
    """The single consumer boundary, and the only compose check this repo needs.

    A bare `docker compose` resolves against this repo root, which no longer
    holds a compose file -- so it fails with "no configuration file provided"
    rather than doing the wrong thing. Routing through agent-mgr keeps the file
    list, the override and the env-file defined in one place.

    It also subsumes the gateway guard this file used to carry: once every
    invocation goes through agent-mgr, agent-mgr's own refusal of a `compose
    run` without --entrypoint covers the second-gateway shape. Re-implementing
    that grammar here was ~75 lines restating a policy its owner enforces.
    """
    offenders = []
    for f, i, line in _source_lines():
        if line.lstrip().startswith("#"):
            continue
        if "docker compose" in QUOTED.sub("", line):
            offenders.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:110]}")
    assert not offenders, "\n".join(offenders)


def test_the_justfile_keeps_no_fleet_wide_recipes():
    """Anything true of every agent belongs in agent-mgr.

    Stated as a denylist of the recipes that MOVED, not an allowlist of the
    three that stayed: this agent's domain workflow is still iterating, and an
    exact-set assertion would fail on the next legitimately-domain recipe --
    calcifying the whole task-runner surface into a deployment regression test.
    """
    recipes = {m.group(1) for line in _lines("justfile")
               if (m := re.match(r"^([a-z][a-z0-9-]*)(?: [A-Z]+)*:", line))}
    migrated = {"agent", "install-plugin", "up", "down", "restart", "logs",
                "restore", "activate", "sign-in"} & recipes
    assert not migrated, f"these belong to agent-mgr now: {sorted(migrated)}"


def test_the_override_names_paths_through_variables():
    """Compose resolves a relative path in an override against the BASE file's
    directory, which is agent-mgr's checkout, not this repo's -- so `./bin` would
    silently mount the wrong thing."""
    for line in _lines("compose.override.yml"):
        s = line.strip()
        if s.startswith("- ") and ":" in s:
            assert not s.startswith("- ./"), s
            assert "${" in s, s


def test_the_override_adds_only_what_this_agent_needs():
    """The home mount, the UID/GID guards and the absent ports come from
    agent-mgr. Restating any of them here creates a second owner."""
    text = (ROOT / "compose.override.yml").read_text()
    for owned_by_agent_mgr in ("HERMES_UID", "HERMES_GID", "container_name",
                               "restart:", "ports:", ":/opt/data\n"):
        assert owned_by_agent_mgr not in text, owned_by_agent_mgr


def test_this_repo_keeps_no_second_copy_of_a_fleet_wide_pin():
    """agent-mgr owns the plugin SHA for every agent on this host. A copy here
    would be inert -- `agent-mgr install-plugin` reads its own -- so bumping it
    would change nothing while the deploy reported success."""
    assert not (ROOT / "runtime" / "plow-chat-plugin.ref").exists()
    # Every file that could name it, including the skills -- the same list the
    # gateway guard scans, for the same reason: a guard pointed away from where
    # the shape lives is how the last two rounds of this were missed.
    for f, i, line in _source_lines():
        if "runtime/plow-chat-plugin.ref" in line and "agent-mgr" not in line:
            raise AssertionError(
                f"{f.relative_to(ROOT)}:{i} names a pin this repo no longer owns: "
                f"{line.strip()[:100]}")


def test_the_readme_does_not_hand_agent_mgr_a_pin_this_repo_owns():
    """agent-mgr pins the upstream image fleet-wide, but this agent derives its
    own on top of it -- so `Dockerfile`'s FROM is this repo's to bump. A
    delegation sentence that says otherwise sends an operator to the wrong repo
    for a pin they are holding.

    Asserted on the delegation list itself, not on the presence of the carve-out
    somewhere in the file: the defect was `image` appearing in the bolded list
    *while* the paragraph below said the Dockerfile owned it, so a check that
    only looks for the carve-out stays green on exactly that shape.
    """
    assert re.search(r"^FROM \S+@sha256:[0-9a-f]{64}$",
                     (ROOT / "Dockerfile").read_text(), re.M), "the image pin moved"
    readme = (ROOT / "README.md").read_text()
    m = re.search(r"\*\*How deployment works.*?not here\.\*\*", readme, re.S)
    assert m, "the delegation sentence is gone; this test tracks its contents"
    assert "image" not in m.group(), (
        "the delegation list hands agent-mgr the image pin, which this repo's "
        "Dockerfile owns:\n" + m.group())


def test_the_hook_neither_reimplements_nor_orchestrates_deployment():
    """It used to `mkdir` the home and `install` the config itself, hardcoding
    both -- which is how `agent-mgr restore str` came to be broken. Then it
    called back into agent-mgr, which left two places sequencing the plugin
    install. Then the sequencing moved to the README, which is worse: the
    operator is not an owner.

    Now agent-mgr owns the deploy end to end and runs this as its hook. The hook
    does the vault half and nothing else.
    """
    script = (ROOT / "scripts" / "restore-runtime-config.sh").read_text()
    body = "\n".join(l for l in script.splitlines() if not l.lstrip().startswith("#"))
    # Quoted spans stripped for the orchestration check below: the hook's own
    # error message names `agent-mgr restore str` as the command to run
    # instead, which is guidance, not a call.
    unquoted = QUOTED.sub("", body)
    for reimplemented in ('hermes_home="$HOME/.hermes"',
                          'install -m 600 "$repo_root/runtime/config.yaml"',
                          'mkdir -p "$hermes_home"'):
        assert reimplemented not in body, f"re-implements agent-mgr: {reimplemented}"
    for orchestrated in ("agent-mgr restore", "agent-mgr install-plugin", "agent-mgr resolve"):
        assert orchestrated not in unquoted, (
            f"the hook is calling its own caller: {orchestrated} -- agent-mgr "
            "runs this and exports what it needs")
    # It takes the home agent-mgr exported. One owner, and no second process.
    assert 'hermes_home="${AGENT_HOME:-}"' in body


def test_nothing_writes_into_the_agents_home_without_resolving_it():
    """One owner for where an agent's state lives.

    Scoped to lines that WRITE -- prose naming ~/.hermes/logs/gateway.log is a
    reader's landmark, not a second owner, and a guard that flags it reads as a
    repo-wide one-owner proof it does not deliver. Markdown is in scope because
    the README and the skills are operator- and agent-executed: a hardcoded
    install path in one is run, not just read.
    """
    spellings = ("$HOME/.hermes", "~/.hermes", "${HOME}/.hermes")
    writes = ("install ", "cp ", "mkdir ", "tee ", "rm ", "mv ", "touch ", "chmod ", "> ")
    offenders = []
    for f, i, line in _source_lines():
        if line.lstrip().startswith("#"):
            continue
        if not any(sp in line for sp in spellings):
            continue
        if not any(w in line for w in writes):
            continue
        if "agent-mgr" in line:
            continue
        offenders.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
    assert not offenders, (
        "these write into the agent's home at a hardcoded path instead of "
        "resolving it from agent-mgr:\n" + "\n".join(offenders))


def test_the_restore_hook_is_never_invoked_directly():
    """`agent-mgr restore str` is the one deploy entry point, and it runs this
    hook itself. A doc or recipe that calls the script directly is the shape
    that put deployment ordering in the operator's hands: the hook alone
    installs no config and no plugin, so it produces a deploy that reads
    healthy and ships nothing.
    """
    offenders = []
    for f, i, line in _source_lines():
        stripped = line.lstrip()
        if stripped.startswith(("#", "|", ">")):
            continue
        if "restore-runtime-config.sh" not in line:
            continue
        if stripped.startswith("test -"):        # the deploy skill's presence guards
            continue
        if "AGENT_RESTORE_HOOK" in line:         # the declaration, and prose naming it
            continue
        # Prose naming the file in backticks is a reference, not a call --
        # bin/build-hubs explains its own contract that way.
        if re.search(r"`[^`]*restore-runtime-config\.sh[^`]*`", line):
            continue
        offenders.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
    assert not offenders, (
        "these invoke the restore hook directly instead of through "
        "`agent-mgr restore str`:\n" + "\n".join(offenders))




def test_the_deploy_asserts_the_hook_actually_ran():
    """Declaring AGENT_RESTORE_HOOK proves nothing: this repo cannot see whether
    agent-mgr honours it, and the test that checks the key is present stays green
    either way. Unhonoured, config.yaml still lands and the container still
    recreates, so the gateway comes up over an un-overlaid vault, empty hub lists
    and a stale SOUL -- every check green, the deploy shipping nothing.

    The hook's closing line is the only evidence available, so the deploy has to
    check for it.
    """
    skill = (ROOT / ".claude/skills/deploy-str-hermes/SKILL.md").read_text()
    assert "Restored tracked Hermes configuration to" in skill, (
        "the deploy does not verify the restore hook ran")
    assert "AGENT_RESTORE_HOOK" in skill and "NOT applied" in skill

    # And the hook must still print the line the deploy greps for.
    hook = (ROOT / "scripts/restore-runtime-config.sh").read_text()
    assert "Restored tracked Hermes configuration to" in hook, (
        "the hook stopped printing the line the deploy greps for")


