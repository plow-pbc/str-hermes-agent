"""What setup wrote: $HERMES_HOME/str/setup.env, under $HERMES_HOME/.env.

str-config writes setup.env (hermes-owned; the agent cannot write .env, which
plow-init rewrites root-owned every boot). 02-str-setup-env publishes it into the
container environment at boot, and the gateway's load_dotenv(override=True) then
lets .env win -- so .env wins here too. A caller merging this over os.environ
gets what the gateway sees, before any restart. Sibling scripts import this by
name with bin/ on sys.path.
"""
import pathlib

from dotenv import dotenv_values  # in the image; tests use the same parser

SETUP_ENV = "str/setup.env"


def read_setup_env(home) -> dict:
    """{**setup.env, **.env}; an absent file reads as {}."""
    home = pathlib.Path(home)
    return {k: v for path in (home / SETUP_ENV, home / ".env") if path.exists()
            for k, v in dotenv_values(path).items()}


def groups(env) -> dict:
    """PLOW_CHAT_GROUP_UIDS `uid=Label,...` as {label: uid}."""
    pairs = (item.split("=", 1) for item in env.get("PLOW_CHAT_GROUP_UIDS", "").split(",") if "=" in item)
    return {label.strip(): uid.strip() for uid, label in pairs}


def approval_group(env) -> str | None:
    """The owners' group's uid, or None when drafts go to the owner's own chat.
    Named rather than pinned by id, so re-creating the group means relabelling
    PLOW_CHAT_GROUP_UIDS and nothing else."""
    label = env.get("PLOW_CHAT_APPROVAL_GROUP", "").strip()
    if not label:
        return None
    if label not in groups(env):
        raise SystemExit(f"PLOW_CHAT_APPROVAL_GROUP {label!r} names no group in PLOW_CHAT_GROUP_UIDS")
    return groups(env)[label]
