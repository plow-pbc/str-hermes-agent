import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts/check-home-binding.sh"


def verdict(tmp_path, dotenv=None):
    """Run the check under a temp $HOME; dotenv=None means no file at all.

    Driving it through $HOME rather than a test-only override means the
    fixtures exercise the same path production does.
    """
    if dotenv is not None:
        hermes = tmp_path / ".hermes"
        hermes.mkdir()
        (hermes / ".env").write_text(dotenv)
    # The script asks agent-mgr where this agent's home is rather than
    # assuming it, so the stub is what makes $HOME still steer the test.
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir(exist_ok=True)
    stub = stub_bin / "agent-mgr"
    stub.write_text('#!/bin/sh\ncase "$1" in resolve) echo "AGENT_HOME=$HOME/.hermes" ;; esac\n')
    stub.chmod(0o755)
    result = subprocess.run(
        [CHECK], env={"HOME": str(tmp_path), "PATH": f"{stub_bin}:/usr/bin:/bin"},
        text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("case", "dotenv", "expected"),
    [
        ("no dotenv", None, "NO DOTENV"),
        ("absent key", "PLOW_CHAT_CHAT_UID=cht_priv\n", "UNSET"),
        ("blank value", "PLOW_CHAT_CHAT_UID=cht_priv\nPLOW_CHAT_HOME_CHANNEL=\n", "UNSET"),
        (
            "private chat",
            "PLOW_CHAT_CHAT_UID=cht_priv\nPLOW_CHAT_HOME_CHANNEL=cht_priv\n",
            "the current private chat",
        ),
        (
            # entries carry a display name; only the uid side is matched, and
            # the plugin trims, so this spaced shape reaches the dotenv
            "group pin, labelled list",
            "PLOW_CHAT_CHAT_UID=cht_priv\n"
            "PLOW_CHAT_GROUP_UIDS=cht_a=Cleaners, cht_b=STR Owners\n"
            "PLOW_CHAT_HOME_CHANNEL=cht_b\n",
            "pinned to a configured group",
        ),
        (
            # re-activation issues a new chat UID; home keeps the old one
            "stale after re-activation",
            "PLOW_CHAT_CHAT_UID=cht_new\nPLOW_CHAT_GROUP_UIDS=cht_a=Cleaners\n"
            "PLOW_CHAT_HOME_CHANNEL=cht_old\n",
            "STALE",
        ),
    ],
)
def test_home_binding_verdicts(tmp_path, case, dotenv, expected):
    assert expected in verdict(tmp_path, dotenv)


def test_the_check_never_prints_a_uid(tmp_path):
    """It runs where tokens live; a verdict must not echo identifiers."""
    out = verdict(
        tmp_path,
        "PLOW_CHAT_CHAT_UID=cht_new\nPLOW_CHAT_HOME_CHANNEL=cht_old_secret\n",
    )
    assert "cht_old_secret" not in out
    assert "cht_new" not in out
