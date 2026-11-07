"""send_after: the one way a scheduled job's message reaches a Plow chat.

A job decides from the thread as it read it; by the time its woken turn sends,
someone may have written. The wake names the newest message the job saw, and
this refuses the send if anything is newer, returning it to be read first.
"""
import os

from fastmcp import FastMCP

import plow_api

mcp = FastMCP("send_after")


@mcp.tool
def send_after(chat: str, body: str, after: str) -> dict:
    """Send `body` to Plow chat `chat` only if message `after` is still its newest.
    If anyone wrote since, nothing is sent and `newer` lists what they wrote:
    read it and decide again — the step may be answered, or need new words.
    To send anyway, call again with the newest uid from `newer` as `after`."""
    return plow_api.send_after(os.environ, chat, body, after)


if __name__ == "__main__":
    mcp.run()
