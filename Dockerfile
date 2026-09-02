# Pinned by digest, not by tag. This image is a large unreviewed surface landing
# in a runtime that holds a Hostex token and a writable vault, and `docker
# compose build` — which the README documents as the routine rebuild —
# re-resolves a tag every time it runs.
# Bump deliberately: `docker buildx imagetools inspect nousresearch/hermes-agent:latest`.
FROM nousresearch/hermes-agent@sha256:8f4e8677281eca188bc9d2fda90806646ba19941fce55fa8fda2d63112ff48a8

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

# ~/.hermes is bind-mounted over /opt/data, so anything written to
# /opt/data/skills at build time is masked at runtime. The image's own bundled
# skills work around this by syncing in at boot; this does the same for ours.
COPY docker/cont-init.d/03-link-wiki-skills.sh /etc/cont-init.d/03-link-wiki-skills.sh
RUN chmod +x /etc/cont-init.d/03-link-wiki-skills.sh
