"""This repo's own runtime surface, now that it has one.

`compose.yml` lands beside agent-mgr's `compose.override.yml` rather than
replacing it: agent-mgr stays the live path until the cutover, and both existing
at once is what keeps it available as the rollback.
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
