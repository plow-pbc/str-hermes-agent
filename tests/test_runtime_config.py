import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENV = {
    "HOSTEX_TOKEN",
    "SEAM_API_KEY",
    "PLOW_CHAT_CHAT_UID",
    "PLOW_CHAT_APPROVAL_GROUP",
    "PLOW_CHAT_GROUP_UIDS",
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


def test_tracked_config_pins_the_model_route_uses_env_secrets_and_enables_plow():
    """A restore onto a model route production never ran is a silent wrong agent.

    The tracked config once carried `base_url: https://openrouter.ai/api/v1`
    while the running container reported `model.base_url` unset and
    .env.example declared no credential for it — so the restore succeeded and
    came up on a different provider. Same class as a missing MCP server.
    """
    config = (ROOT / "runtime/config.yaml").read_text()
    # plow-init owns the model route and rewrites it every boot; a tracked copy
    # would be a second source of truth. What stays repo-owned is the codex
    # compression fallback -- a second provider a Plow stall cannot take down.
    assert "\nmodel:" not in f"\n{config}"
    assert "\nproviders:" not in f"\n{config}"
    assert "provider: openai-codex\n      model: gpt-5.5" in config
    assert "Authorization: Bearer ${HOSTEX_TOKEN}" in config
    assert "- search_conversations" in config   # only appears under include:
    assert "plow-chat-platform" in config
    assert "PLOW_CHAT_TOKEN" not in config


def test_the_tracked_config_bounds_how_stale_a_session_can_get():
    """A session that never ends keeps August's recipes in front of September's
    model. On 2026-09-11 a 42-day-old DM answered the owner by re-running a
    credential recipe from its own history, using the env alias the base has
    stripped since plow-hermes-agent#56, and reported the 401 as a token still
    unauthorized — while the credential plow-init publishes answered the same
    call 200 on that box.

    Column 0 and the exact key names, for the reason group_sessions_per_user is
    pinned below: the image reads this from the top level of this file or a
    `gateway:` section, and a misspelt key is not an error. What a wrong name
    leaves behind is the image's own default of "none" — the never-resets state
    this key exists to leave — so it reads as configured while changing nothing.

    `mode` is pinned to idle rather than merely present because daily and both
    are the values that would cut the owners' group off mid-draft; reset_by_type,
    which would scope this to DMs, is read only from the legacy gateway.json.
    Text, not behaviour: proving a reset fires needs a live gateway and a day of
    silence.
    """
    config = (ROOT / "runtime/config.yaml").read_text()
    assert re.search(r"^session_reset:\n  mode: idle\n  idle_minutes: 1440$",
                     config, re.MULTILINE)


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
    # The image's own authoritative path, not one under the home. These servers
    # used to arrive as bind mounts from the deploy clone, so the reference had
    # to follow wherever the home mounted; the image carries them now, root-owned
    # under /opt/plow, so no host has to supply one and the agent cannot rewrite
    # a server holding its lock credentials. What this test is about is
    # unchanged: a config omitting a shipped server restores an agent that can
    # read Hostex but not touch a door, and the restore still succeeds.
    missing = [s for s in servers if f"/opt/plow/str/{s}/server.py" not in config]
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


def test_compose_never_mounts_the_checkout_around_the_vault():
    """A mounted checkout hands an unattended turn `.git` and the scheduler's scripts.

    It happened: an ingest turn found pages missing from the working tree and ran
    `git restore --source=HEAD` over them (#89). The same mount also made `bin/`
    writable beside the read-only copy the scheduler runs, so a turn processing
    guest text could rewrite it. Nothing in the container reads the checkout --
    `bin/` and `mcp-seam/` arrive baked at /opt/plow/str and are symlinked into
    the home by docker/cont-init.d/05-install-agent-payload.sh -- so widening
    this back to the repo root buys nothing and costs both.
    """
    compose = (ROOT / "compose.yml").read_text()
    assert not re.search(r"^\s*- \.{1,2}?/?:", compose, re.M), \
        "compose.yml mounts the checkout root; the vault bind is the only repo-side mount"
    # runtime/ is deliberately not mounted either. Its only in-container reader
    # was build-soul, and both files it exposed now arrive in the image.
    assert "/repo/runtime" not in compose, "the dead runtime/ mount came back"


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
                       (ROOT / "runtime/persona.md").read_text())
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
    create = enable.rsplit("compose exec", 1)[1]
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
    soul_src = " ".join((ROOT / "runtime/persona.md").read_text().split())
    assert "verbatim from an unmarked vault" in soul_src
    assert "commits the owners to nothing" in soul_src
    assert "SOUL.md veto-window test" in flowed


