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

# Asked of agent-mgr rather than assumed: it owns where an agent's state lives,
# and a second copy of that path here is how the two drift.
#
# Bound and checked before the /.env is appended. Swallowing the failure
# collapses to `/.env` and prints "wrong host or account" -- naming a cause that
# is not the cause, when agent-mgr simply could not resolve. Its own stderr is
# left to reach the operator for the same reason.
home="$(agent-mgr resolve str | sed -n 's/^AGENT_HOME=//p')"
if [ -z "$home" ]; then
  echo "home: agent-mgr cannot resolve str -- register it first: agent-mgr register str <repo>"
  exit 0
fi
env_file="$home/.env"

if [ ! -r "$env_file" ]; then
  echo "home: NO DOTENV at $env_file — wrong host or account"
  exit 0
fi

get() { sed -n "s/^$1=//p" "$env_file" | tail -1 | tr -d "[:space:]\"'"; }

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
