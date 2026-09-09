"""The image is the agent: everything it needs is in it, and nothing private is.

str's own content used to reach the container from the host — `agent-mgr deploy`
staged the persona and config into ~/.hermes, and compose.override.yml
bind-mounted bin/ and mcp-seam/ off the deploy clone. None of that travels with
a published image, so what follows asserts the image carries it instead.

Runs against the locally built tag, so `docker build -t sams-str-hermes-agent:local .`
is a precondition. A missing image raises rather than skips: these are the whole
contract of a self-contained image, and a suite that goes quiet about them is
how an agent ships knowing nothing.
"""
import subprocess

import pytest

IMAGE = "sams-str-hermes-agent:local"


def _sh(cmd: str) -> str:
    return subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", IMAGE, "-c", cmd],
        capture_output=True, text=True, check=True,
    ).stdout


@pytest.mark.parametrize("path", [
    # Under the base's bundled root, not the home: tools/skills_sync.py
    # reconciles this root into $HERMES_HOME/skills on every boot, preserving
    # the category path, so a volume home still receives it.
    "/opt/hermes/skills/productivity/property-guest-messaging/SKILL.md",
    # The three the host used to hand in — the scheduler's scripts, the Seam
    # stdio server, and the vault schema a deploy installs.
    "/opt/plow/str/bin/nightly.sh",
    "/opt/plow/str/mcp-seam/server.py",
    "/opt/plow/str/vault-seed/AGENTS.md",
    # The declarative half of the home, which a named-volume home seeds from.
    "/var/lib/hermes/config.yaml",
])
def test_the_image_carries_what_the_host_used_to_supply(path):
    assert _sh(f"test -s {path} && echo present").strip() == "present"


def test_the_persona_ships_at_the_path_the_boot_hardens():
    """COPY'd, never mounted: harden_home() fchown()s this exact path on every
    boot, so a read-only mount here fails EROFS and no gateway starts.

    Asserted on content, not on size: the base ships its own SOUL.md at this
    path, so a size check passes on an image carrying the generic persona
    instead of this agent's -- an agent that boots and answers as someone else.
    """
    assert "short-term rentals" in _sh("cat /var/lib/hermes/SOUL.md")
    assert _sh("stat -c %a /var/lib/hermes/SOUL.md").strip() == "644"


def test_the_config_in_the_image_is_this_agents_own():
    """Same reason as the persona above -- the base ships a config.yaml here,
    so only its content distinguishes a baked one from the base's."""
    assert "hostex" in _sh("cat /var/lib/hermes/config.yaml")


def test_no_vault_content_is_baked():
    """The vault holds per-property operations including access codes. It is a
    bind-mount and must never enter an image a public registry serves.

    --exclude-dir names the kernel's pseudo-filesystems, not image content:
    /proc/<pid>/cmdline holds this grep's own command line, so without the
    exclusion the check matches itself and can never pass, and /proc/kcore is
    a 128 TiB sparse file that takes the run from 4 seconds to 6 minutes.
    Nothing in the image's own filesystem is excluded, and it must stay that
    way -- a failure here is a stop, not a grep to narrow.

    The needle is the property's slug rather than the bare surname: "Henderson"
    is a copyright name in two of the base's C headers, so the bare word
    reports a hit on a clean image and would be read as noise the first time it
    mattered. `henderson-ave` is spelled by the vault and nothing else.
    """
    out = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", IMAGE, "-c",
         "grep -ril henderson-ave / --exclude-dir=proc --exclude-dir=sys "
         "--exclude-dir=dev 2>/dev/null | head -5"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert out == "", f"property data found in the image: {out}"


# The wheel ships 37 wiki skills; these five are what this agent uses. The read
# side is named first because for three weeks it was missing entirely -- the
# enabled set was four write-side skills, so the vault was compiled nightly and
# the agent had no retrieval tool at all.
ENABLED_WIKI_SKILLS = ("wiki-query", "wiki-ingest", "wiki-lint", "wiki-digest")


@pytest.mark.parametrize("skill", ENABLED_WIKI_SKILLS)
def test_the_wiki_skills_the_agent_uses_ship_in_the_image(skill):
    """Installed at build time into the root the runtime reconciles from.

    They used to be copied into $HERMES_HOME/skills at container start by
    docker/cont-init.d/03-link-wiki-skills.sh, which exited 1 on every boot: a
    plain `#!/usr/bin/env bash` cont-init script gets s6's own environment, not
    the container's, so its `${HERMES_HOME:?}` was never set. A build-time
    install needs no environment and no home, and it is the same job the layer
    that ships every other bundled skill already does.

    Flat, as siblings directly under the root, because that is where the link
    script put them: skills_sync preserves the path it finds a skill at, so the
    running agent's skills keep the names it knows them by.
    """
    assert _sh(f"test -s /opt/hermes/skills/{skill}/SKILL.md && echo present").strip() == "present"


def test_the_theory_skill_comes_from_the_base_rather_than_the_wheel():
    """llm-wiki is in the enabled set the agent uses but not in the list above.

    Measured against this image: skills_sync keys a relocation on SKILL.md's
    frontmatter name, so a second `name: llm-wiki` under the bundled root is
    relocated onto the base's research/llm-wiki and then overwritten by the
    base's own content. Installing the wheel's copy would be inert while
    reading like a delivery, so the base owns this one and this asserts the
    agent still gets it.
    """
    assert _sh("test -s /opt/hermes/skills/research/llm-wiki/SKILL.md && echo present").strip() == "present"
