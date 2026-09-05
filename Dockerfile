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
FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-357a87c0e511fbad5a1ab7adc4d8aeafde33c86f@sha256:b04c20af23f2c13ef67e350eda97116f64b641593e1fd767f4df8c72e0aa6f0d

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

# ~/.hermes is bind-mounted over $HERMES_HOME (/var/lib/hermes on this base), so
# anything written to its skills/ at build time is masked at runtime. The image's own bundled
# skills work around this by syncing in at boot; this does the same for ours.
COPY docker/cont-init.d/03-link-wiki-skills.sh /etc/cont-init.d/03-link-wiki-skills.sh
RUN chmod +x /etc/cont-init.d/03-link-wiki-skills.sh
