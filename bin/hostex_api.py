"""Shared HTTP transport for the bin/ scripts: one redirect policy, one GET.

urllib replays a Request's headers onto the redirect target, so a redirect off
the API origin would hand the bearer token to whatever it points at. Verified
against a local server that 302s elsewhere: as an ordinary header the token
arrived at the second origin. Refusing beats stripping-and-following — a
stripped redirect just fails authentication somewhere else. This used to be
three copies (hostex-poll, hostex-raw, checkin-watch); now it is one.

Sibling scripts import this by name: run as scripts, bin/ is sys.path[0];
loaded by path under test, each script inserts its own directory first.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.hostex.io/v3"
# Hostex stalls mid-body for over 30 s most days between 16:00 and 17:00 UTC.
# One stall on a 2-minute poll is not worth a text to the owners, so retry it.
TIMEOUT_S = 30
ATTEMPTS = 3
BACKOFF_S = 2


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, f"refusing redirect to {newurl}", headers, fp)


OPENER = urllib.request.build_opener(_NoRedirect)


def get(path: str, token: str, user_agent: str, **params: object) -> dict:
    """One Hostex GET. Raises rather than defaulting — a caller that cannot
    read must not report "nothing new". Hostex 403s the default Python-urllib
    User-Agent, so every caller names itself. A transport stall is retried;
    an HTTP status is not."""
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "Hostex-Access-Token": token,
        "User-Agent": user_agent,
        "Accept": "application/json",
    })
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with OPENER.open(request, timeout=TIMEOUT_S) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError:
            raise  # a status is an answer, not a stall; the redirect refusal lands here too
        except OSError:
            if attempt == ATTEMPTS:
                raise
            time.sleep(BACKOFF_S * attempt)
