"""This repo's own runtime surface, which is now the only one.

`compose.yml` used to land beside agent-mgr's `compose.override.yml`, which kept
the outgoing path available as the rollback through the cutover. The cutover
held, so the deploy path it rolled back to is gone.
"""
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "compose.yml").read_text())["services"]["hermes"]
JUSTFILE = (ROOT / "justfile").read_text()

# Every recipe that stops or replaces the container. `up` is one because it
# force-recreates.
TRANSITIONS = ("up", "down", "restart")


def _recipe_body(recipe: str) -> str:
    """The indented lines under `recipe:`, to the next unindented one."""
    lines = JUSTFILE.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{recipe}:"):
            body = []
            for follow in lines[i + 1:]:
                if follow.strip() and not follow[:1].isspace():
                    break
                body.append(follow)
            return "\n".join(body)
    raise AssertionError(f"no `{recipe}` recipe in the justfile")


def test_every_transition_recipe_runs_the_nightly_veto():
    """agent-mgr invoked scripts/no-nightly-running as AGENT_PRE_TRANSITION.
    docker compose has no such hook, so these recipes are the only thing left
    that can refuse: a transition landing between a page write and its manifest
    entry leaves the vault holding a page nothing recorded, and the next run
    appends its facts a second time with nothing reporting it."""
    for recipe in TRANSITIONS:
        body = _recipe_body(recipe)
        assert "no-nightly-running" in body, \
            f"`just {recipe}` can stop the container without the veto"
        assert body.index("no-nightly-running") < body.index("docker compose"), \
            f"`just {recipe}` transitions before it asks"
    # agent.env used to declare it as AGENT_PRE_TRANSITION and agent-mgr ran it.
    # The recipes invoke it as a path now, so the bit is what makes it runnable.
    guard = ROOT / "scripts" / "no-nightly-running"
    assert guard.is_file() and guard.stat().st_mode & 0o111, "the veto is not executable"


def test_no_transition_reaches_docker_compose_outside_a_vetoed_recipe():
    """The bypass the veto exists to prevent: a later recipe reaching for
    `docker compose down` on its own stops the container without ever asking.

    Scoped to the justfile, which is the only file allowed to reach compose at
    all -- test_instance_contract.py holds bin/ and scripts/ to the stricter
    rule that they may not reach it in any form."""
    vetoed = {line.strip() for recipe in TRANSITIONS
              for line in _recipe_body(recipe).splitlines()}
    offenders = [
        f"justfile:{i}: {line.strip()[:90]}"
        for i, line in enumerate(JUSTFILE.splitlines(), 1)
        if not line.lstrip().startswith("#") and "docker compose" in line
        and any(re.search(rf"\b{verb}\b", line) for verb in ("up", "down", "restart", "stop"))
        and line.strip() not in vetoed
    ]
    assert not offenders, "\n".join(offenders)


def test_the_veto_and_compose_agree_on_the_container_name():
    """no-nightly-running asks docker about the container by name and refuses to
    guess one. A justfile exporting a name compose does not declare would pass
    every transition by asking after a container that does not exist -- a veto
    that always says yes, silently."""
    exported = next(line.split('"')[1] for line in JUSTFILE.splitlines()
                    if line.startswith("export AGENT_CONTAINER"))
    assert COMPOSE["container_name"] == exported


def test_compose_never_mounts_over_a_hardened_path():
    """plow-init's harden_home() fchown()s SOUL.md and chmods the home and its
    skills on every boot. A read-only mount at one of those fails EROFS,
    plow-init parks, and no gateway starts."""
    targets = [v.split(":")[1] for v in COMPOSE["volumes"]]
    for hardened in ("/var/lib/hermes/SOUL.md", "/var/lib/hermes/skills"):
        assert hardened not in targets, f"{hardened} is fchown()d every boot"


def test_the_home_is_a_volume_and_the_vault_is_a_bind():
    """The home is a volume so it survives `up`/`down` and travels with the
    image's payload on first init. The vault stays a bind: it holds per-property
    operations including access codes, and never enters an image."""
    vols = COMPOSE["volumes"]
    assert any(v.startswith("agent-home:/var/lib/hermes") for v in vols)
    assert any(v.endswith("/repo/vault") and v.startswith("${HOME}") for v in vols)



def test_the_agent_uid_is_declared_so_it_can_write_the_vault():
    """The vault is a host bind the agent WRITES -- the nightly ingests into it.

    At the image's baked uid the gateway cannot: the vault is 775 and owned by
    the host account. `s6-setuidgid` re-derives supplementary groups from the
    account database, so compose's `group_add` never reaches the gateway; the
    image's own stage2 hook names HERMES_UID as the supported way to match host
    ownership, and refuses `--user` outright. Dropping these is silent -- the
    boot is clean, the cron reports ok, and the vault simply stops changing.
    """
    for key in ("HERMES_UID", "HERMES_GID"):
        assert COMPOSE["environment"].get(key), \
            f"{key} is unset -- the agent cannot write the vault bind"
