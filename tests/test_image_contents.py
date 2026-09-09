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

    Content AND names. `grep -l` matches contents only, and a mutation test
    baking `henderson-ave-access-and-backup-codes.md` with unrelated text inside
    it passed a content-only gate -- while the filename alone leaks the property
    and the fact that its access codes exist. Six files in the live vault carry
    the slug in their name; fifteen carry it in their text.

    Through `_sh`, which is `check=True`: a raw subprocess.run reads a docker
    that never ran as empty output, and empty output is what this test wants --
    so the gate between the vault and a public registry passed while inspecting
    nothing. It has to fail closed, because the condition that silences it (no
    local image) is the ordinary state of a clean checkout.
    """
    pseudo = "--exclude-dir=proc --exclude-dir=sys --exclude-dir=dev"
    hits = _sh(
        f"{{ grep -ril henderson-ave / {pseudo} 2>/dev/null; "
        "find / -name '*henderson-ave*' -not -path '/proc/*' -not -path '/sys/*' "
        "-not -path '/dev/*' 2>/dev/null; } | sort -u | head -5"
    ).strip()
    assert hits == "", f"property data found in the image: {hits}"


# The wheel ships 37 wiki skills; these five are what this agent uses. The read
# side is named first because for three weeks it was missing entirely -- the
# enabled set was four write-side skills, so the vault was compiled nightly and
# the agent had no retrieval tool at all.
ENABLED_WIKI_SKILLS = ("wiki-query", "wiki-ingest", "wiki-lint", "wiki-digest")


@pytest.mark.parametrize("skill", ENABLED_WIKI_SKILLS)
def test_the_wiki_skills_the_agent_uses_ship_in_the_image(skill):
    """Installed at build time into the root the runtime reconciles from, rather
    than copied into the home by a cont-init script that exited 1 on every boot.
    Flat, as siblings under the root, because skills_sync preserves the path it
    finds a skill at and that is where the old script put them."""
    assert _sh(f"test -s /opt/hermes/skills/{skill}/SKILL.md && echo present").strip() == "present"


def test_the_theory_skill_comes_from_the_base_rather_than_the_wheel():
    """llm-wiki is in the enabled set the agent uses but not in the list above:
    a second copy under the bundled root never reaches the agent, for the reason
    the Dockerfile records. The base's is the one that lands, so this asserts the
    agent still gets it."""
    assert _sh("test -s /opt/hermes/skills/research/llm-wiki/SKILL.md && echo present").strip() == "present"
