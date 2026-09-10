#!/usr/bin/env bash
# Which chat is Hermes bound to for /sethome deliveries, and is it usable?
#
# Prints exactly one verdict line and no values — PLOW_CHAT_* are chat
# identifiers, and this runs on a host whose dotenv also holds tokens.
#
# Parses the dotenv rather than sourcing it: `.` executes the file, and an
# unquoted spaced value (PLOW_CHAT_GROUP_UIDS=cht_a=STR Owners — display names
# routinely contain spaces) would run as a command.
set -uo pipefail

compose() { "$(dirname "$0")/compose" "$@"; }

# Asked of the container rather than of a host path: the home is a named volume
# now, and the only supported way in is through the container that mounts it.
# $HERMES_HOME rather than a literal, because the boot contract decides it.
#
# One verdict covers both "not up" and "no dotenv yet". They were separate while
# the home was a host directory that could exist without a container; a volume
# this script cannot reach is a volume it cannot judge, and inventing a
# distinction it cannot actually observe names a cause that may not be the cause.
if ! dotenv=$(compose exec -T hermes sh -c 'cat "$HERMES_HOME/.env"' 2>/dev/null); then
  echo "home: NO DOTENV — the container is not up, or has none yet"
  exit 0
fi

get() { printf '%s\n' "$dotenv" | sed -n "s/^$1=//p" | tail -1 | tr -d "[:space:]\"'"; }

home=$(get PLOW_CHAT_HOME_CHANNEL)
private=$(get PLOW_CHAT_CHAT_UID)
# Entries are <uid>=<display name>; only the uid side is matched. get() has
# already stripped whitespace, so a spaced name arrives run together — which
# does not matter, since the name is dropped here and never printed.
groups=$(get PLOW_CHAT_GROUP_UIDS | sed 's/=[^,]*//g')

if [ -z "$home" ]; then
  echo "home: UNSET — needs /sethome (the operator)"
elif [ "$home" = "$private" ]; then
  echo "home: the current private chat — fine"
elif case ",$groups," in *",$home,"*) true ;; *) false ;; esac; then
  echo "home: pinned to a configured group — fine"
else
  echo "home: STALE — needs /sethome (the operator)"
fi
