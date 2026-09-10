#!/usr/bin/env bash
# Make the image's payload reachable from where its consumers look.
#
# Everything this agent ships is authoritative under /opt/plow/str -- root-owned,
# so a prompt-injected turn cannot rewrite what its own cron runs. But none of
# its consumers look there:
#
#   scripts   hermes cron refuses a script that resolves outside
#             $HERMES_HOME/scripts (hermes_cli/cron.py:652), so wiki-nightly and
#             checkin-watch cannot name /opt/plow at all.
#   mcp-seam  config.yaml names the server by path, and a home migrated from the
#             agent-mgr shape carries the old spelling until it is refreshed.
#   SOUL.md   a named-volume home seeds from the image only while EMPTY, so a
#   config.yaml  migrated home shadows every later revision of both
#             (plow-hermes-agent#58). Reinstalling here is what makes an image
#             update reach a home that already exists.
#
# Directory symlinks, not per-file ones, and that is load-bearing for scripts:
# cron resolves BOTH its scripts dir and the candidate path before comparing
# them, so a link on the directory leaves both sides inside /opt/plow/str/bin and
# the check passes, while linking each file individually resolves outside a
# scripts dir that did not move and is refused.
#
# HERMES_HOME is defaulted, not required: a `#!/usr/bin/env bash` cont-init
# script gets s6's own environment rather than the container's. Demanding it is
# the bug that made 03-link-wiki-skills.sh exit 1 on every boot.
set -euo pipefail

home="${HERMES_HOME:-/var/lib/hermes}"

link_payload() {
  local target="$1" link="$home/$2"
  # An empty directory is what a migrated home carries: these were bind-mount
  # targets on the host, so the copy brings the directory and nothing in it.
  if [ -d "$link" ] && [ ! -L "$link" ] && [ -z "$(ls -A "$link")" ]; then
    rmdir "$link"
  fi
  # -T so a surviving populated directory is an error rather than a link created
  # inside it. Under `set -e` that parks the boot, which is the right answer: the
  # only thing that produced one was agent-mgr's bind, and it is gone.
  ln -sfnT "$target" "$link"
}

link_payload /opt/plow/str/bin scripts
link_payload /opt/plow/str/mcp-seam mcp-seam

# Only on a home this image manages, and the link above is that signal:
# link_payload made it, and leaves a directory someone else mounted alone. The
# agent-mgr deploy that staged SOUL and config into a bind-mounted home is gone,
# but the guard is what makes that true rather than assumed: on any home this
# image did not lay out, restoring its copies would revert whatever did.
#
# Unconditional on that path: overwriting the stale copy a volume kept is the
# entire point (#58). Root-owned 0644, which harden_home() re-asserts on SOUL.md
# moments later; plow-init rewrites its own keys in config.yaml after this.
# Two owners, deliberately. harden_home() fchown()s SOUL.md to 0:0 on every
# boot, so root is right there. config.yaml is the AGENT's: plow-init rewrites
# it as the agent rather than as root -- its own comment says so -- and $home
# carries the sticky bit, so a root-owned config.yaml is one the agent cannot
# replace. Installing it root-owned parks the boot on
# `PermissionError: os.replace('config.yaml.tmp' -> 'config.yaml')`,
# after cont-init has already reported success. The base ships it 0640
# agent-owned; match that.
install -o root -g root -m 0644 -t "$home" /opt/plow/str/home/SOUL.md
install -o "$(id -u hermes)" -g "$(id -g hermes)" -m 0640 -t "$home" \
  /opt/plow/str/home/config.yaml
