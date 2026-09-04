import importlib.util
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENV = {
    "HOSTEX_TOKEN",
    "PLOW_CHAT_TOKEN",
    "SEAM_API_KEY",
    "PLOW_CHAT_CHAT_UID",
    "PLOW_CHAT_APPROVAL_GROUP",
    "PLOW_CHAT_GROUP_UIDS",
}


@pytest.fixture
def restore_env(tmp_path):
    """The environment agent-mgr hands its restore hook.

    No agent-mgr stub: the hook cannot invoke agent-mgr, and proving that
    absence at runtime needed a recorder, a PATH injection and tuple plumbing
    for no coverage a source read does not already give. The source read lives
    in the stacked contract-suite PR.
    """
    return {
        **os.environ,
        "HOME": str(tmp_path),
        "AGENT_HOME": str(tmp_path / ".hermes"),
        # A name $HOME cannot produce. With tmp_path/"hermes-vault" the export
        # and the old $HOME/hermes-vault hardcode named the same directory, so
        # every assertion passed against both -- a check that passes on broken
        # data, over the one behaviour this change is.
        "STR_VAULT": str(tmp_path / "runtime-vault"),
        # A synthetic, container-shaped value rather than either real one
        # (/opt/data or /var/lib/hermes): proves restore-runtime-config.sh
        # actually threads the export through rather than happening to match
        # a hardcoded literal.
        "AGENT_HOME_TARGET": "/test-container-home",
    }


def env_assignments():
    lines = (ROOT / ".env.example").read_text().splitlines()
    return dict(
        line.split("=", 1)
        for line in lines
        if line and not line.startswith("#") and "=" in line
    )


def test_env_example_declares_blank_secret_and_chat_contract():
    values = env_assignments()
    assert set(values) == REQUIRED_ENV
    assert all(values[key] == "" for key in REQUIRED_ENV)


def fake_runtime_vault(home_parent: Path) -> Path:
    """The minimum a restore needs: an index to compose from, and the
    operations pages it rebuilds the hub lists out of.

    One hub and two pages under its slug, rather than a bare `operations/`: an
    empty one is the single input shape where that rebuild produces nothing and
    passes whether or not the restore still calls it.
    """
    v = home_parent / "runtime-vault"
    v.mkdir()
    (v / "operations").mkdir()
    for slug, title in (
        ("cedar-cabin-access-and-backup-codes", "Cedar Cabin access and backup codes"),
        ("cedar-cabin-parking", "Cedar Cabin parking"),
    ):
        (v / "operations" / f"{slug}.md").write_text(
            f"---\ntype: Operation\ntitle: {title}\n---\n\n# {title}\n"
        )
    (v / "properties").mkdir()
    (v / "properties" / "cedar-cabin.md").write_text(
        "---\ntype: Property\ntitle: Cedar Cabin\n---\n\n# Cedar Cabin\n\n## Operations\n"
    )
    (v / "index.md").write_text("# Index\n\n- [Sauna](operations/s.md) — how it works\n")
    return v


def test_restore_script_populates_fresh_hermes_home(tmp_path, restore_env):
    vault = fake_runtime_vault(tmp_path)
    home = tmp_path / ".hermes"
    # A relative symlink is what a terminal-enabled turn can plant in the vault
    # it writes, and it resolves against a different directory on each side:
    # inside the container this reaches /opt/data/repo/.ssh (nothing), on the
    # host it reaches ~/.ssh, which no mount exposes. Following it locks the
    # operator out of their own machine, so the copy must replace the link
    # rather than write through it.
    (tmp_path / ".ssh").mkdir()
    keys = tmp_path / ".ssh" / "authorized_keys"
    keys.write_text("ssh-ed25519 AAAA operator\n")
    (vault / "AGENTS.md").symlink_to("../.ssh/authorized_keys")
    env = restore_env
    # agent-mgr's half runs first, as the deploy skill and README sequence it.
    # Stubbed here to the one thing this script depends on downstream: a home
    # for build-soul to write into.
    (tmp_path / ".hermes").mkdir(exist_ok=True)
    result = subprocess.run(
        [ROOT / "scripts/restore-runtime-config.sh"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    # The restore applies the whole of runtime/, the pinned plugin included --
    # by delegating to agent-mgr rather than re-implementing it. Installing the
    # config and creating the home are agent-mgr's, and its own suite covers
    # them; what this repo owns is that it asks rather than assumes.
    # It asks agent-mgr where the home is and does not otherwise touch
    # deployment -- no config install, no plugin install, no ordering.
    assert str(home) in result.stdout
    # The composed SOUL, injected into every turn: the persona's reading rules
    # plus the vault index — the fake runtime vault's, not the checkout's,
    # since the restore now refuses to run without one.
    soul = home / "SOUL.md"
    assert "^[ambiguous]" in soul.read_text()
    assert "Sauna" in soul.read_text()
    assert stat.S_IMODE(soul.stat().st_mode) == 0o600
    # This script writes no dotenv of its own, and the check stays rather than
    # becoming a comment: it is what proves the sentence two docs assert -- the
    # /sethome target lives in that file as PLOW_CHAT_HOME_CHANNEL, so a seed
    # copy or a build-soul that grew into it would clobber the host's real
    # tokens with nothing red. Non-vacuous under the stub, which only mkdirs.
    assert not (home / ".env").exists()
    assert not (home / "channel_directory.json").exists()
    # The other half of one restore: the hand-authored seed lands in the runtime
    # vault, which is the deploy's whole claim on that directory. Asserted here
    # rather than in a second test — same fixture, same invocation, and a
    # separate one would only re-ask this question with a different assertion.
    assert (vault / "AGENTS.md").read_bytes() == (
        ROOT / "runtime/vault-seed" / "AGENTS.md").read_bytes()
    # .env is the one seed file the restore rewrites rather than copies
    # verbatim: obsidian-wiki reads OBSIDIAN_VAULT_PATH as a literal, so the
    # seed's placeholder is patched to the real container path, sourced from
    # AGENT_HOME_TARGET rather than restated. Every other line survives the
    # copy untouched.
    installed_env = (vault / ".env").read_text()
    seed_env = (ROOT / "runtime/vault-seed/.env").read_text()
    assert installed_env.splitlines()[1:] == seed_env.splitlines()[1:]
    assert "OBSIDIAN_VAULT_PATH=/test-container-home/repo/vault" in installed_env
    # The seed ships no hubs: the property list is the operator's, and lives in
    # the runtime vault. The deploy's claim on that directory is AGENTS.md and
    # .env, plus rebuilding the hub lists the vault already has (#78).
    assert sorted(p.name for p in (vault / "properties").glob("*.md")) == ["cedar-cabin.md"]
    hub = (vault / "properties" / "cedar-cabin.md").read_text()
    assert "- [Access and backup codes](../operations/cedar-cabin-access-and-backup-codes.md)" in hub
    assert "- [Parking](../operations/cedar-cabin-parking.md)" in hub
    assert keys.read_text() == "ssh-ed25519 AAAA operator\n", "wrote through the link"
    assert not (vault / "AGENTS.md").is_symlink(), "link survived instead of being replaced"


@pytest.mark.parametrize("state", ["absent", "empty", "symlinked-index"])
def test_restore_refuses_a_box_without_a_usable_runtime_vault(tmp_path, state, restore_env):
    """Proceeding would bring the agent up with the schema and no facts —
    indistinguishable from a healthy deploy, and the quietest failure this repo
    can ship.

    The empty row is not hypothetical: `docker compose up -d` creates a missing
    bind source as an empty directory, so the vault exists and holds nothing.
    A readiness check on the directory passed that, installed the seed, and only
    failed later at build-soul — after mutating the vault.

    The symlinked row is the read-side of the same boundary: the index feeds
    build-soul, whose output is injected into every turn, so a link there reads
    a host file into the agent's context. `../.ssh/id_ed25519` resolves to
    nothing in the container and to the operator's private key on the host.

    Keyed on index.md, every row refuses before anything is written — which is
    what `assert not .hermes.exists()` below pins for all three at once.
    """
    vault = tmp_path / "runtime-vault"
    if state != "absent":
        vault.mkdir()
    if state == "symlinked-index":
        # The index is read by build-soul and its output is injected into every
        # turn, so a link here reads a host file into the agent's context —
        # `../.ssh/id_ed25519` resolves to nothing in the container and to the
        # operator's private key on the host.
        (tmp_path / ".ssh").mkdir()
        (tmp_path / ".ssh" / "id_ed25519").write_text("PRIVATE KEY MATERIAL\n")
        (vault / "index.md").symlink_to("../.ssh/id_ed25519")
    env = restore_env
    result = subprocess.run(
        [ROOT / "scripts/restore-runtime-config.sh"],
        env=env,
        text=True, capture_output=True,
    )
    assert result.returncode == 1
    assert "no usable runtime vault" in result.stderr
    # The refusal has to precede the plugin install too. Before `just` was
    # stubbed, the .hermes assertion below covered that implicitly, since the
    # real installer creates the data dir; the stub writes nothing, so without
    # this the vault check could move below it and every row would stay green
    # while a refused deploy fetched and ran the upstream installer.
    assert "README" in result.stderr
    assert not (tmp_path / ".hermes").exists()
    if state == "empty":
        assert not list(vault.iterdir()), "refusal must not have seeded the vault"


def test_tracked_config_pins_the_model_route_uses_env_secrets_and_enables_plow():
    """A restore onto a model route production never ran is a silent wrong agent.

    The tracked config once carried `base_url: https://openrouter.ai/api/v1`
    while the running container reported `model.base_url` unset and
    .env.example declared no credential for it — so the restore succeeded and
    came up on a different provider. Same class as a missing MCP server.
    """
    config = (ROOT / "runtime/config.yaml").read_text()
    assert "provider: openai-codex" in config
    assert "default: gpt-5.6-sol" in config
    assert "base_url:" not in config
    assert "Authorization: Bearer ${HOSTEX_TOKEN}" in config
    assert "- search_conversations" in config   # only appears under include:
    assert "plow-chat-platform" in config
    assert "PLOW_CHAT_TOKEN" not in config


def test_every_first_party_mcp_server_is_in_the_restorable_config():
    """A backup that omits a shipped capability restores a lesser agent.

    This snapshot was taken before mcp-seam/ landed, so restoring on a fresh
    host produced an agent that could read Hostex but not touch a door — the
    one failure this whole PR exists to prevent, and invisible because the
    restore itself succeeds.
    """
    config = (ROOT / "runtime/config.yaml").read_text()
    servers = sorted(p.name for p in ROOT.glob("mcp-*") if p.is_dir())
    assert servers, "precondition: the repo ships at least one first-party MCP server"
    # ${HERMES_HOME}, not a literal /opt/data: Hermes' own config loader
    # interpolates it from the container's env, so this is the one path that
    # is correct whether or not this agent has opted into agent-mgr's boot
    # contract -- see the comment above the seam server's own entry.
    missing = [s for s in servers if f"${{HERMES_HOME}}/{s}/server.py" not in config]
    assert not missing, f"first-party MCP servers absent from runtime/config.yaml: {missing}"


def test_every_env_key_the_tracked_config_substitutes_is_declared():
    """`${FOO}` in the config with no FOO in .env.example is a silent gap.

    HERMES_HOME is excluded: it is the image's own env var, always set,
    never operator-supplied, so it has no business in .env.example beside the
    REQUIRED_ENV secrets that actually need a blank placeholder there.
    """
    config = (ROOT / "runtime/config.yaml").read_text()
    referenced = set(re.findall(r"\$\{([A-Z0-9_]+)\}", config)) - {"HERMES_HOME"}
    undeclared = sorted(referenced - set(env_assignments()))
    assert not undeclared, f".env.example does not declare: {undeclared}"


def test_tracked_config_excludes_host_specific_runtime_identity_and_state():
    config = (ROOT / "runtime/config.yaml").read_text()
    assert "home_channel:" not in config
    assert not re.search(r"(?<![A-Za-z0-9_])(?:cht|cp)_[A-Za-z0-9_-]+", config)
    assert "user_id:" not in config
    assert "\nonboarding:\n" not in f"\n{config}"


def test_the_agent_reaches_the_vault_and_not_the_checkout_around_it():
    """A mounted checkout hands an unattended turn `.git` and the scheduler's scripts.

    It happened: an ingest turn found pages missing from the working tree and
    ran `git restore --source=HEAD` over them (#89). The same mount also made
    `bin/` writable at `/opt/data/repo/bin`, defeating the read-only mount of
    that same directory at `/opt/data/scripts` — a turn processing guest text
    could rewrite what the scheduler runs. Nothing in the container reads the
    checkout, so widening this back to the repo root buys nothing and costs
    both.
    """
    # The override, not compose.yml: agent-mgr owns the service definition now,
    # and the override is where this repo can still widen a mount.
    compose = (ROOT / "compose.override.yml").read_text()
    # `- .:` in any form, and `${STR_REPO}:` too -- naming the repo root through
    # the variable reaches exactly the same directory.
    assert not re.search(r"^\s*- (\.|\$\{STR_REPO\}):/", compose, re.M), (
        "compose.override.yml mounts the checkout root; mount ~/hermes-vault instead"
    )
    # Every container-side path resolves through AGENT_HOME_TARGET, which
    # agent-mgr exports from the agent's boot contract: /opt/data for an agent
    # that has not opted in, /var/lib/hermes for one that has. A mount written
    # against it is correct under either, so the cutover between them is a
    # descriptor change alone, not a change here. `:?` on the first use,
    # matching how the file already reuses ${STR_REPO} bare after its own
    # first `:?` -- an agent-mgr that predates the export must fail loudly at
    # compose time, not silently mount at the literal `/repo/vault` an unset
    # variable would produce.
    home = r"\$\{AGENT_HOME_TARGET(?::\?[^}]*)?\}"
    for suffix in ("bin:" + home + "/scripts:ro", "mcp-seam:" + home + "/mcp-seam:ro",
                   "runtime:" + home + "/repo/runtime:ro"):
        assert re.search(rf"^\s*- \$\{{STR_REPO\}}/{suffix}$", compose, re.M), suffix
    assert re.search(rf"^\s*- \$\{{STR_VAULT:\?\}}:{home}/repo/vault$", compose, re.M)

    # Everything that reaches the vault (or mcp-seam) from INSIDE the
    # container resolves through $HERMES_HOME instead of a compose variable:
    # the image's own env var, /opt/data today and /var/lib/hermes once this
    # agent opts in, matching wherever compose actually mounted it above
    # either way. Anchored substrings, not full-line matches: each caller
    # embeds the reference in a longer expression (a bash default, a Python
    # call, a YAML scalar), and the literal is what would go stale if any one
    # of them stopped agreeing with the others.
    for path, literal in (
        ("bin/nightly.sh", 'VAULT="${VAULT:-$HERMES_HOME/repo/vault}"'),
        ("bin/nightly.sh", 'SOUL_OUT="${SOUL_OUT:-$HERMES_HOME/SOUL.md}"'),
        ("bin/nightly.sh", '"$HERMES_HOME/repo/runtime/SOUL.md"'),
        ("bin/checkin-watch.py", 'hermes_home() / "repo/vault"'),
        ("scripts/enable-checkin-watch.sh", '${VAULT:-$state/repo/vault}'),
        ("runtime/config.yaml", '${HERMES_HOME}/mcp-seam/server.py'),
    ):
        assert literal in (ROOT / path).read_text(), f"{path} no longer contains: {literal}"

    # ingest-all's host branch cannot read $HERMES_HOME directly -- it runs
    # without a container around it yet -- so it queries the image for it
    # instead of guessing, required (`:?`) so a throwaway container that
    # cannot report it stops the run rather than mounting the vault somewhere
    # the real container does not read from.
    ingest_all = (ROOT / "bin/ingest-all").read_text()
    assert '[ -n "${HERMES_HOME:-}" ] && [ -d "$HERMES_HOME/repo" ]' in ingest_all
    assert 'printf %s "${HERMES_HOME:?}"' in ingest_all
    assert 'CVAULT="$CHOME/repo/vault"' in ingest_all

    # justfile's test-wiki has no container to query either, but it IS run
    # through agent-mgr, which exports AGENT_HOME_TARGET into that same
    # environment -- the same variable compose resolves the real mount from,
    # so no image query is needed here the way ingest-all needs one.
    justfile = (ROOT / "justfile").read_text()
    assert 'HH="${AGENT_HOME_TARGET:?set by agent-mgr from the boot contract}"' in justfile
    assert 'CV="$HH/repo/vault"' in justfile
    # The seed's OBSIDIAN_VAULT_PATH is a placeholder here too, same reason
    # and same rewrite as restore-runtime-config.sh's -- the only two places
    # that copy runtime/vault-seed/.env, and both have to fix it up since the
    # file itself cannot.
    assert 'sed -i "s|^OBSIDIAN_VAULT_PATH=.*|OBSIDIAN_VAULT_PATH=$CV|" "$V/.env"' in justfile

    # runtime/vault-seed/.env's OBSIDIAN_VAULT_PATH is read by the
    # third-party obsidian-wiki CLI as plain KEY=VALUE, with no ${VAR}
    # expansion of its own (verified against its _read_config_value, a bare
    # string split on "=") -- so the file itself stays on a placeholder, and
    # restore-runtime-config.sh, the one layer that CAN expand a variable,
    # rewrites it from AGENT_HOME_TARGET after the seed copy. Covered by
    # test_restore_script_populates_fresh_hermes_home, which drives that
    # script end to end; asserted here only that the seed still ships a
    # value for the rewrite to replace.
    assert re.search(
        r"(?m)^\s*OBSIDIAN_VAULT_PATH=\S",
        (ROOT / "runtime/vault-seed/.env").read_text(),
    )
    restore = (ROOT / "scripts/restore-runtime-config.sh").read_text()
    assert "AGENT_HOME_TARGET:?" in restore
    assert "OBSIDIAN_VAULT_PATH=" in restore

    # runtime/SOUL.md's mention is prose, concatenated verbatim by build-soul
    # (`cat "$PERSONA"`) into the agent's own injected system prompt -- so
    # unlike the .env above, the fix is to name a real shell variable the
    # agent already has in its own tool-execution environment, not to derive
    # a value from either of build-soul's two callers (which pass genuinely
    # different $VAULT arguments -- see build-soul's own comment for why that
    # broke this before).
    assert "$HERMES_HOME/repo/vault" in (ROOT / "runtime/SOUL.md").read_text()

    # The host-side vault path has ONE owner now: agent.env declares
    # STR_VAULT, compose interpolates it for the mount, and agent-mgr exports
    # it to the restore hook. The hook used to keep a second spelling that
    # this row fenced against; it consumes the export instead, so there is
    # nothing left to drift.
    assert re.search(
        r"(?m)^\s*STR_VAULT=\$HOME/hermes-vault$", (ROOT / "agent.env").read_text()
    )
    assert 'VAULT="${1:-$HOME/hermes-vault}"' in ingest_all


def test_every_tool_soul_names_is_one_some_server_offers():
    """SOUL instructs; `include` decides whether the agent can comply.

    SOUL tells the agent to call `search_automation_actions` before promising
    anything about check-in, and `get_access_code` before publishing a PIN a
    guest will type. Drop either name from its server's `include` and the
    instruction survives, pointing at a tool the agent was never offered — it
    cannot comply and nothing reports that it could not. Same shape as the
    enable gate and `send_message` below: two files naming one tool, neither
    able to see the other. `hermes tools list` cannot stand in for this — it
    reports the surface, not that SOUL names something missing from it.

    Both servers, because SOUL now names tools from each: a Hostex-only slice
    would read `get_access_code` as unoffered and go red on a healthy config.

    `search_staffs` is absent on purpose — it returns each cleaner's mobile and
    email into a runtime that also drafts guest-facing messages, which #24 names
    as the thing that must never cross, and `search_tasks` carries the same
    schedule with staff_name and no contact fields. A deliberate absence that
    records no reason gets added back by the next person who wants a staff_id.
    """
    config = (ROOT / "runtime/config.yaml").read_text()
    # Both list styles — hostex writes one `- name` per line, seam an inline
    # `[a, b, …]` — with comment lines dropped first, since this file names
    # `search_staffs` in prose to explain why it is the one left out.
    offered = set(re.findall(r"[a-z_]{4,}", "\n".join(
        line for block in config.split("include:")[1:]
        for line in block.split("resources:")[0].splitlines()
        if not line.lstrip().startswith("#"))))

    named = re.findall(r"`((?:search|get|update|list|create|delete)_\w+)`",
                       (ROOT / "runtime/SOUL.md").read_text())
    assert named, "precondition: SOUL names at least one tool"
    for tool in named:
        assert tool in offered, f"SOUL.md tells the agent to call {tool}; no server offers it"

    assert "search_staffs" not in offered


def test_the_enable_gate_and_the_tracked_config_agree_on_send_message():
    """`send_message` is what lets an owner's approval reach the guest, and the
    enable script proves step 1's restore took by grepping the live tool list
    for it. Two files therefore have to name the same tool, in the same
    direction, and neither side is covered: the fake-docker test in
    test_hostex_poll.py answers every outer `docker compose exec` and never
    runs the script's inner shell, so the gate's polarity can invert with the
    suite green — which is exactly how it shipped inverted once.

    Sliced to the hostex include block rather than searched over the whole
    file, so a stray occurrence under another server cannot satisfy it.
    """
    config = (ROOT / "runtime/config.yaml").read_text()
    hostex_include = config.split("include:")[1].split("resources:")[0]
    assert "- send_message" in hostex_include

    # Both arms. The pass arm alone lets the polarity invert while the wording
    # stays; the fail arm alone lets it be collapsed to a no-op gate. Each is
    # the other's blind spot, and both have shipped as real bugs here.
    gate = (ROOT / "scripts/enable-hostex-inbound.sh").read_text()
    assert "*send_message*) ;;" in gate               # present is the pass arm
    # Bounded at `esac` for the same reason the include slice above is bounded:
    # unsliced, the arm's own `exit 1` could be deleted and this would still
    # match a later guard's, leaving a gate that warns and continues.
    assert "exit 1" in gate.split("send_message not allowlisted")[1].split("esac")[0]


def test_the_draft_reaches_the_session_that_approves_it():
    """The prompt tells the agent to send what an owner approved, which needs
    the draft in the session the owner answers in. The image puts it there only
    when BOTH halves hold, and each half alone is a silent no-op: the flag
    without an origin is how this shipped (a bare `--deliver` reads as a
    broadcast and is never mirrored — #62), and an origin without the flag
    mirrors nothing. Nothing else fails when one half goes; the loop just
    quietly answers about the wrong guest, which is the bug that prompted this.

    Text, not behaviour, for the same reason the gate above is: proving the
    mirror lands needs a live gateway, and the fake-docker test never runs the
    inner shell.
    """
    config = (ROOT / "runtime/config.yaml").read_text()
    assert re.search(r"^cron:\n(?:\s+#.*\n|\s*\n)*\s+mirror_delivery: true$",
                     config, re.MULTILINE)

    # The third half, and the one that shipped missing (#84): the image resolves
    # the delivery's session by chat id and returns nothing when the chat has
    # several open sessions owned by different people. One session per member
    # therefore makes mirror_delivery a silent no-op in the owners' group.
    # Anchored at column 0 — the image reads this key from the top level or a
    # `gateway:` section, never from a platform's `extra`, and the indented
    # form under `platforms:` is the shape that already failed here.
    assert re.search(r"^group_sessions_per_user: false$", config, re.MULTILINE)

    enable = (ROOT / "scripts/enable-hostex-inbound.sh").read_text()
    # The last exec in the file is the create; sliced so the env pair has to sit
    # on that call rather than anywhere earlier, where it would do nothing.
    create = enable.rsplit("agent-mgr compose str exec", 1)[1]
    assert "cron create" in create
    assert "-e HERMES_SESSION_PLATFORM=plow_chat" in create
    assert '-e HERMES_SESSION_CHAT_ID="$chat_uid"' in create
    # Absent on purpose: it resolves the mirror to the one member it names, and
    # every member of the owners' group can approve. Setting it would strand the
    # others with the same missing draft, silently.
    assert "HERMES_SESSION_USER_ID" not in enable

    # The marker is a contract across two files: the poller's prompt tells the
    # announcement to mark the wording, the group prompt sends what the marker
    # names. Renamed on one side only, the loop silently goes back to lifting
    # words out of prose — which sends different words under an approval.
    # Sliced to the prompt literal, as the assertions above are sliced: the
    # marker moving into a comment while PROMPT renames it would otherwise pass.
    poll = (ROOT / "bin/hostex-poll.py").read_text()
    assert "DRAFT:" in poll.split('PROMPT = """')[1].split('"""')[0]
    assert "DRAFT:" in config
    # The consumer half of the same contract: bounded to the one delivery, not
    # to everything below the marker in the thread — which would sweep in the
    # approval itself and any owner cross-talk that arrived first. Matched on
    # collapsed whitespace so reflowing the block cannot break the assertion
    # while the rule survives.
    flowed = " ".join(config.split())
    assert "send the rest of that delivery, below the last `DRAFT:` line" in flowed
    # Both halves are anchored, because the delivery now quotes the guest
    # verbatim and a guest can write either field. An unanchored marker lets a
    # bare approval send the guest's own wording back as the owners'; an
    # unanchored id lands approved wording on the wrong guest's conversation.
    assert "conversation id named directly above that same `DRAFT:` line" in flowed
    # Consumer half of the veto-tier contract (producer half pinned in
    # test_hostex_poll.py): the group prompt has to recognize a veto
    # announcement by the same wording the poller's prompt emits, and any
    # owner reply naming the draft id has to cancel the scheduled send —
    # otherwise "stop" is chatter and the one-shot job fires anyway.
    assert "sending in 30 minutes unless an owner says stop" in flowed
    assert "cancels the pending job" in flowed
    assert "an edit as a fresh draft on the approval path" in flowed
    # Single policy owner: eligibility for the veto tier is stated once, in
    # SOUL.md; the poller and group prompts reference it rather than restate
    # it (contract-drift finding, PR #3). These pins hold the owner's copy;
    # the reference pins live beside each consumer's other clauses.
    soul_src = " ".join((ROOT / "runtime/SOUL.md").read_text().split())
    assert "verbatim from an unmarked vault" in soul_src
    assert "commits the owners to nothing" in soul_src
    assert "SOUL.md veto-window test" in flowed


