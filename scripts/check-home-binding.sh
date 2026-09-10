#!/usr/bin/env bash
# Which chat is Hermes bound to for home deliveries, and is it usable?
#
# Prints exactly one verdict line and no values — these are chat identifiers,
# and they come from a container whose environment also holds tokens.
#
# Asks the boot environment, not the dotenv. This compared the home's
# PLOW_CHAT_HOME_CHANNEL against PLOW_CHAT_CHAT_UID and the PLOW_CHAT_GROUP_UIDS
# list, which is the pre-v2.0.0 contract: the plow_chat plugin has read
# PLOW_HOME_CHANNEL from the boot environment since then, and plow-init
# publishes it there from the credential. Measured against the live agent, the
# dotenv key and the boot value disagree, and the boot value appears in neither
# the dotenv chat uid nor its group list -- so the three-way comparison printed
# STALE about inputs nothing reads, and prescribed a remedy that cannot work.
# A verdict from dead inputs is worse than no verdict.
#
# Asked of the container rather than of a host path: the home is a named volume
# now, and the only supported way in is through the container that mounts it.
set -uo pipefail

compose() { "$(dirname "$0")/compose" "$@"; }

BOOT_ENV=/run/s6/container_environment/PLOW_HOME_CHANNEL

# `exec`, never `run`: this reads one value and must never start a second
# gateway beside the serving one.
if compose exec -T hermes test -s "$BOOT_ENV" 2>/dev/null; then
  echo "home: bound — the gateway holds a home channel from its credential"
elif compose exec -T hermes true 2>/dev/null; then
  echo "home: UNSET — plow-init published no home channel. Re-mint the credential with"
  echo "      plow-pbc/plow-agents (\`plow-agents mint <line-uid> --credential-file"
  echo "      ~/.plow-credentials-str\`, then \`just restart\`). Not /sethome: without"
  echo "      PLOW_HOME_CHANNEL the plugin does not load, so it cannot receive it."
else
  echo "home: CANNOT ASK — the container is not up on this host"
fi
