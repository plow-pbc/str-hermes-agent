"""What setup wrote: the parsed $HERMES_HOME/.env.

The gateway loads the same file with load_dotenv(override=True), so a caller
merging this over os.environ lets the file win, as the gateway does. Sibling
scripts import this by name with bin/ on sys.path.
"""
import pathlib

from dotenv import dotenv_values  # in the image; tests use the same parser


def read_setup_env(home: pathlib.Path) -> dict:
    """The parsed $HERMES_HOME/.env, {} if absent."""
    path = pathlib.Path(home) / ".env"
    return dict(dotenv_values(path)) if path.exists() else {}
