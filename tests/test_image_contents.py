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


# One table, one shape: `(case, command, expected)`. Every row asks the image a
# question with a single-line answer, so a new claim about what the image
# carries is a row rather than another function.
#
# `|| echo missing` on the presence rows so a real absence reads as an
# assertion diff naming the case, while a docker that could not run at all
# still raises through _sh's check=True.
def _present(path):
    return f"test -s {path} && echo present || echo missing", "present"


# Where each path goes and why is the Dockerfile's to explain; these are the
# claims, not a second copy of the reasoning.
IMAGE_CLAIMS = [
    ("the agent's own skill",
     *_present("/opt/hermes/skills/productivity/property-guest-messaging/SKILL.md")),
    ("the scheduler's scripts", *_present("/opt/plow/str/bin/nightly.sh")),
    ("the seam mcp server", *_present("/opt/plow/str/mcp-seam/server.py")),
    ("the vault schema", *_present("/opt/plow/str/vault-seed/AGENTS.md")),
    *((f"wiki skill {name}", *_present(f"/opt/hermes/skills/{name}/SKILL.md"))
      for name in ("wiki-query", "wiki-ingest", "wiki-lint", "wiki-digest")),
    # Not installed from the wheel; a second copy never reaches the agent.
    ("the theory skill, from the base",
     *_present("/opt/hermes/skills/research/llm-wiki/SKILL.md")),
    # Persona and config on CONTENT, not size: the base ships its own at both
    # paths, so an existence check passes on an image carrying the generic
    # persona -- an agent that boots and answers as someone else.
    ("the persona is this agent's own",
     "grep -qF 'short-term rentals' /var/lib/hermes/SOUL.md && echo yes", "yes"),
    ("the persona is readable by the agent",
     "stat -c %a /var/lib/hermes/SOUL.md", "644"),
    ("the config is this agent's own",
     "grep -qF hostex /var/lib/hermes/config.yaml && echo yes", "yes"),
]


@pytest.mark.parametrize(("case", "command", "expected"), IMAGE_CLAIMS,
                         ids=[c[0] for c in IMAGE_CLAIMS])
def test_the_image_carries_what_the_host_used_to_supply(case, command, expected):
    assert _sh(command).strip() == expected, case


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

