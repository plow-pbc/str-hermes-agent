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
