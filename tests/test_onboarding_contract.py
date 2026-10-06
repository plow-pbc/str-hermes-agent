"""Setup starting on the owner's first message is prompt-shaped; only its wiring is here.

Asserted: the persona's First run section exists, its gather row loads the
str-setup skill, checks Latch and probes setup-ness with a command that prints
only set or unset, the key file is created before the reply that names it,
nothing in it reads an env file's content, and the base's first-message
profile offer stays off. NOT asserted: the wording. Whether the opener lands is
decidable only from a live transcript (plow-pbc/life-assistant-hermes-agent
tests/test_onboarding_contract.py draws the same line).
"""
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
PERSONA = (ROOT / "runtime" / "persona.md").read_text()
FIRST_RUN = PERSONA[PERSONA.index("## First run"):PERSONA.index("**You never message a guest")]
GATHER = next(line for line in FIRST_RUN.splitlines() if line.startswith("| 1 "))
PROBE = re.search(r'terminal\(command="(.+?)"\)', GATHER).group(1)


def test_gather_batches_the_probe_with_the_setup_skill():
    assert 'skill_view(name="str-setup")' in GATHER
    assert (ROOT / "agent-skills/productivity/str-setup/SKILL.md").exists()


def test_key_file_exists_before_the_reply_names_it():
    rows = [line for line in FIRST_RUN.splitlines() if re.match(r"\| \d ", line)]
    template = next(i for i, row in enumerate(rows) if "str-config --latch-template" in row)
    assert 'terminal(command="/opt/plow/str/bin/setup-discover latch")' in GATHER
    assert 0 < template < len(rows) - 1 and "Reply" in rows[-1]


def test_first_run_never_reads_an_env_file():
    assert not re.search(r"read_file\([^)]*\.env", FIRST_RUN)


@pytest.mark.parametrize("body,expected", [
    (None, "unset"),
    ("HOSTEX_TOKEN=\n", "unset"),
    ("HOSTEX_TOKEN=tok-secret-123\n", "set"),
])
def test_probe_prints_only_set_or_unset(tmp_path, body, expected):
    (tmp_path / "str").mkdir()
    if body is not None:
        (tmp_path / "str" / "setup.env").write_text(body)
    out = subprocess.run(["sh", "-c", PROBE.replace("/var/lib/hermes", str(tmp_path))],
                         capture_output=True, text=True, check=True).stdout
    assert out == f"{expected}\n"


def test_base_profile_offer_is_off():
    config = yaml.safe_load((ROOT / "runtime" / "config.yaml").read_text())
    assert config["onboarding"]["profile_build"] == "off"
