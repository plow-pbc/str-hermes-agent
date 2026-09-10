#!/usr/bin/env bash
# Which chat is Hermes bound to for home deliveries, and is it usable?
#
# Prints exactly one verdict line and no values — these are chat identifiers,
# and the file they come from also holds tokens.
#
# Asks the runtime, not the dotenv. Before #39 the binding lived in the home's
# `PLOW_CHAT_HOME_CHANNEL`, checked against `PLOW_CHAT_CHAT_UID` and the
# `PLOW_CHAT_GROUP_UIDS` list. The `plow_chat` plugin has read `PLOW_HOME_CHANNEL`
# from the boot environment since v2.0.0, and plow-init publishes it there from
# the credential; which chats are usable comes from the grant, not from a list in
# the dotenv. Measured on the live agent: the dotenv key and the boot value
# disagree, and the boot value appears in neither the dotenv's chat uid nor its
# group list — so the old three-way comparison reported STALE about data nothing
# reads. A verdict from dead inputs is worse than no verdict, because it names an
# action (`/sethome`) that would change nothing.
set -uo pipefail

CONTAINER=hermes
BOOT_ENV=/run/s6/container_environment/PLOW_HOME_CHANNEL

# `docker exec`, not `compose run`: this reads one value and must never start a
# second gateway. The container name is fixed by compose.yml.
if ! docker exec "$CONTAINER" true 2>/dev/null; then
  echo "home: CANNOT ASK — $CONTAINER is not running on this host"
  exit 0
fi

if docker exec "$CONTAINER" test -s "$BOOT_ENV" 2>/dev/null; then
  echo "home: bound — the gateway holds a home channel from its credential"
else
  echo "home: UNSET — plow-init published no home channel. Re-mint with plow-pbc/plow-agents"
  echo "      ('plow-agents mint <line-uid> --credential-file ~/.plow-credentials-str', then 'just restart')."
  echo "      Not /sethome: without PLOW_HOME_CHANNEL the plugin does not load, so it cannot receive it."
fi
