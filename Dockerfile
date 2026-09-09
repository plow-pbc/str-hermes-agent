# The Plow base image: upstream Hermes plus plow-init, which asks Plow who this
# agent is at every boot and writes what it learns -- the model route, the
# `plow` MCP server that is the operator's Mac over the relay -- into the home.
# Pinned by the base-<sha> tag AND its digest: the registry's tags are mutable
# (plow terraform/ecr.tf), so the tag names the commit for a reader and the
# digest is what docker actually resolves. CI publishes one tag per
# plow-hermes-agent commit that plow-pbc/plow's agents.json pins; bump both,
# taking the INDEX digest (`docker buildx imagetools inspect <tag>`, the top
# `Digest:` line) -- a per-platform manifest digest under it does not resolve
# as a FROM.
FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-cd2a898d673812621bae6764560e455807e9818e@sha256:bfd4980f361a551e62569f8c2eb717c1076d0b8be3a0499b869eaece151336a4

# The obsidian-wiki skills shell out to an `obsidian-wiki` CLI (cache-check,
# batch-plan, trust-check, ast-extract), which the skill directories don't
# contain — so the package has to be on PATH inside the container. The wheel
# also ships all 37 skills under obsidian_wiki/_data/skills/, so installing it
# is the whole delivery mechanism: no vendoring, no bind mounts per skill.
# Installed into its own venv rather than the system python: the base image is
# PEP 668 externally-managed, and an isolated venv also keeps obsidian-wiki's
# dependency graph from colliding with Hermes' own.
ARG OBSIDIAN_WIKI_VERSION=2026.7.10
ENV WIKI_VENV=/opt/wiki-venv
RUN uv venv "$WIKI_VENV" \
    && uv pip install --python "$WIKI_VENV/bin/python" "obsidian-wiki==${OBSIDIAN_WIKI_VERSION}" \
    && "$WIKI_VENV/bin/obsidian-wiki" --help > /dev/null
ENV PATH="/opt/wiki-venv/bin:${PATH}"

# The wiki skills this agent uses, out of the wheel and into the base's
# bundled-skill root. The other ~33 stay in the package, uninstalled and
# invisible to the agent -- enable one by adding it here and rebuilding.
#
# llm-wiki is deliberately not among them, and the base's research/llm-wiki is
# the one the agent gets. Measured against this image: skills_sync keys a
# relocation on SKILL.md's frontmatter name, so a second `name: llm-wiki`
# anywhere under this root is relocated onto the base's path and then
# overwritten by the base's own content -- the wheel's copy, references/ and
# all, never reaches the agent. Installing it would be inert, and would read
# like a delivery.
#
# This replaces docker/cont-init.d/03-link-wiki-skills.sh, which copied them
# into $HERMES_HOME/skills at every container start and exited 1 on every one:
# a `#!/usr/bin/env bash` cont-init script gets s6's own environment rather than
# the container's, so its `${HERMES_HOME:?}` was never set, and with
# S6_BEHAVIOUR_IF_STAGE2_FAILS=1 the boot warned and carried on. Installing at
# build time needs neither an environment nor a home: skills_sync reconciles
# this root into whichever home the agent gets, which is the same job done once
# by the layer that already owns every other bundled skill.
#
# Flat, as siblings directly under the root, because that is where the link
# script put them: skills_sync preserves the path it finds a skill at, so the
# running agent's skills keep the names it knows them by.
RUN set -eu; \
    src="$("$WIKI_VENV/bin/python" -c 'import obsidian_wiki, pathlib; print(pathlib.Path(obsidian_wiki.__file__).parent / "_data" / "skills")')"; \
    for skill in wiki-query wiki-ingest wiki-lint wiki-digest; do \
      cp -R "$src/$skill" /opt/hermes/skills/"$skill"; \
    done; \
    chmod -R a=rX,u+w /opt/hermes/skills/wiki-*

# The persona and the declarative half of this deployment's home. COPY'd rather
# than mounted: plow-init's harden_home() fchown()s /var/lib/hermes/SOUL.md on
# every boot, so a read-only mount at that path fails EROFS, plow-init parks and
# no gateway starts. Both stay inert under agent-mgr's ~/.hermes bind mount;
# they are what a named-volume home initialises from.
#
# config.yaml carries overrides only. Every base-owned key -- mcp_servers.plow,
# model.*, display, agent.api_max_retries, cron.model_drift_guard,
# tools.tool_search -- is re-asserted by plow-init on every boot, so declaring
# one here could only drift from what actually runs.
# The authoritative copy lives outside the home, because the home is a volume
# and a volume seeds from the image only while it is EMPTY -- so a migrated home
# shadows every later revision of these two files (plow-hermes-agent#58).
# 05-install-agent-payload.sh reinstalls them from here on every boot, which is
# what makes an image update reach a pre-populated home at all.
#
# The copies under /var/lib/hermes are derived from that one, not a second
# source: they are what an EMPTY volume initialises from, and what
# `docker run <tag> cat /var/lib/hermes/SOUL.md` shows an operator checking a
# published image.
COPY runtime/SOUL.md runtime/config.yaml /opt/plow/str/home/
RUN install -m 0644 -t /var/lib/hermes/ \
      /opt/plow/str/home/SOUL.md /opt/plow/str/home/config.yaml

# This agent's own skill, under the base's bundled root rather than in the home.
# tools/skills_sync.py rglobs /opt/hermes/skills for SKILL.md and reconciles
# what it finds into $HERMES_HOME/skills preserving the category path, so a
# volume home still receives this and an image update still reaches a copy the
# agent has not customised. A copy written into the home at build time would be
# masked by whatever mounts over it.
COPY agent-skills/productivity/property-guest-messaging/ /opt/hermes/skills/productivity/property-guest-messaging/
RUN chmod -R a=rX,u+w /opt/hermes/skills/productivity/property-guest-messaging

# What the host used to hand in: compose.override.yml bind-mounted bin/ and
# mcp-seam/ off the deploy clone, and the deploy hook copied the vault seed from
# it. A published image has no deploy clone, so it carries them.
#
# Root-owned under /opt/plow, the pattern life-assistant-hermes-agent uses for
# the same reason: everything under $HERMES_HOME/skills belongs to the agent's
# uid in a running container, so code scheduled from there is code one turn can
# rewrite. These the agent can read and cannot change.
COPY bin/ /opt/plow/str/bin/
COPY mcp-seam/ /opt/plow/str/mcp-seam/
COPY runtime/vault-seed/ /opt/plow/str/vault-seed/
RUN chown -R root:root /opt/plow \
 && find /opt/plow -type d -exec chmod 0755 {} + \
 && find /opt/plow -type f -exec chmod 0644 {} + \
 && find /opt/plow/str/bin -type f -exec chmod 0755 {} +

# The vault's corpus check, moved off the host. `docker compose up -d` creates a
# missing bind source as an empty root-owned directory, so this is what stands
# between a typo'd vault path and an agent that comes up looking healthy while
# knowing nothing.
#
# S6_BEHAVIOUR_IF_STAGE2_FAILS is what makes it a refusal rather than a note.
# The base ships 1, at which a cont-init script exiting non-zero prints one
# warning and the boot carries on and starts the gateway anyway -- which is how
# 03-link-wiki-skills.sh failed on every boot for months without anyone
# noticing. At 2, rc.init stops the container. Measured on the base with a probe
# cont-init, both ways round. Nothing else here fails: on the live agent's own
# boot log all three of the base's cont-init scripts exit 0.
# Plain COPY, not `COPY --chmod`: that option requires BuildKit, and on a stock
# builder the build dies here, before pytest collects. Both scripts are tracked
# 100755 so the executable bit travels; the chmod below normalises the rest,
# which a plain COPY would otherwise take from the builder's umask.
COPY docker/cont-init.d/04-require-vault-corpus.sh /etc/cont-init.d/04-require-vault-corpus.sh

# The one image-to-home seam. Everything above is authoritative under /opt/plow
# and unreachable from where its consumers look: hermes cron refuses a script
# resolving outside $HERMES_HOME/scripts (hermes_cli/cron.py:652), the Seam
# server is launched by a path in config.yaml, and a pre-populated volume never
# receives the image's SOUL or config at all. One script closes all three rather
# than three copies of the payload closing one each.
COPY docker/cont-init.d/05-install-agent-payload.sh /etc/cont-init.d/05-install-agent-payload.sh
RUN chmod 0755 /etc/cont-init.d/04-require-vault-corpus.sh \
               /etc/cont-init.d/05-install-agent-payload.sh

ENV S6_BEHAVIOUR_IF_STAGE2_FAILS=2
