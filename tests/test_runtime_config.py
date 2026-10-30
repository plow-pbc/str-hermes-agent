import importlib.util
import re
import sys
from pathlib import Path

import yaml

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
    assert "seen:" not in config


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
    cannot comply and nothing reports that it could not. Same shape as
    `send_message` below: two files naming one tool, neither able to see the
    other. `hermes tools list` cannot stand in for this — it
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


def test_the_tracked_config_offers_send_message():
    """`send_message` is what lets an owner's approval reach the guest. Sliced
    to the hostex include block rather than searched over the whole file, so a
    stray occurrence under another server cannot satisfy it.
    """
    config = (ROOT / "runtime/config.yaml").read_text()
    hostex_include = config.split("include:")[1].split("resources:")[0]
    assert "- send_message" in hostex_include


def test_the_draft_reaches_the_session_that_approves_it():
    """The prompt tells the agent to send what an owner approved, which needs
    the draft in the session the owner answers in. The poller's wake writes it
    there, so what remains is that the group has one session and that the two
    files naming the draft's markers agree. Nothing else fails when either
    goes; the loop just quietly answers about the wrong guest, which is the
    bug that prompted this.

    Text, not behaviour: proving the session is shared needs a live gateway.
    """
    config = (ROOT / "runtime/config.yaml").read_text()
    # One session per group chat, not per member (#84): a poller's wake lands in
    # the group's session, and the owner who answers it has to land in the same
    # one. Anchored at column 0 — the image reads this key from the top level or
    # a `gateway:` section, never from a platform's `extra`, and the indented
    # form under `platforms:` is the shape that already failed here.
    assert re.search(r"^group_sessions_per_user: false$", config, re.MULTILINE)

    # The poller's prompt tells the announcement to mark the wording with
    # DRAFT:. Sliced to the prompt literal: the marker moving into a comment
    # while PROMPT renames it would otherwise pass.
    poll = (ROOT / "bin/hostex-poll.py").read_text()
    assert "DRAFT:" in poll.split('PROMPT = """')[1].split('"""')[0]
    # Single policy owner: eligibility for the veto tier is stated once, in
    # SOUL.md (contract-drift finding, PR #3). These pins hold the owner's copy.
    soul_src = " ".join((ROOT / "runtime/persona.md").read_text().split())
    assert "verbatim from an unmarked wiki" in soul_src
    assert "commits the owners to nothing" in soul_src


def runtime_config():
    return yaml.safe_load((ROOT / "runtime/config.yaml").read_text())


def load_bin(name, module):
    sys.path.insert(0, str(ROOT / "bin"))
    spec = importlib.util.spec_from_file_location(module, ROOT / "bin" / name)
    loaded = sys.modules[module] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_thread_sessions_can_work_the_ledger_and_nothing_else_can_run_it():
    """A woken thread has to `kanban_show` its task and `kanban_comment` the
    outcome, and kanban is default-off on every platform. The opt-in is the
    profile-wide one: an explicit `platform_toolsets.plow_chat` list replaces
    the platform's default composite rather than adding to it (probed against
    the pinned image: `[hermes-plow_chat, kanban]` lost terminal, file and 20
    more), and would switch the profile-wide opt-in off for that platform.

    The pins are what keep the task a ledger, and turnover-poll refuses to run
    against any other values, so the two files have to name the same three.
    """
    config = runtime_config()
    assert "kanban" in config["toolsets"]
    assert "plow_chat" not in (config.get("platform_toolsets") or {})
    assert config["kanban"] == {"dispatch_in_gateway": False, "default_assignee": "",
                                "auto_subscribe_on_create": False}
    assert load_bin("turnover-poll.py", "turnover_poll_pins").KANBAN_PINS == config["kanban"]
    # checkin-watch was the one job with an origin to mirror into; none is left.
    assert "mirror_delivery" not in config["cron"]


def soul():
    return " ".join((ROOT / "runtime/persona.md").read_text().split())


def test_every_session_knows_how_a_turnover_task_is_worked():
    """SOUL is the one text every thread's session reads, so it carries what
    no single wake can: how to treat a reply that arrives turns later, the
    owner's CLEAN and HOLD, and the owners-only guest exception. Each vendor
    wake carries its own thread's brief (tests/test_turnover_poll.py)."""
    flowed = soul()
    for phrase in ("kanban_show", "`CLEAN <owner>`", "`HOLD <owner>`", "no draft id", "FYI",
                   "A reply in a chat where you earlier received a Turnover wake is answered "
                   "against that wake's task",
                   # Two same-day stays share a thread; a "done" on the wrong
                   # one sends a false "ready" to a guest.
                   "ask which stay, by its title, before recording anything"):
        assert phrase in flowed, f"SOUL.md lacks {phrase!r}"


def test_only_a_turn_with_owner_authority_can_turn_a_wake_into_a_guest_message():
    """A wake renders as an ordinary group turn named `Turnover`, and vendor
    sessions hold Hostex send_message: a vendor who takes that display name,
    or pastes wake-shaped text, must not reach a guest. The session is never
    told a chat's PLOW_CHAT_GROUP_UIDS label, so the exception is keyed on
    what the plow_chat channel prompt does tell it: whether this turn carries
    the owner's full authority."""
    flowed = soul()
    assert "only on a turn that carries your owner's full authority" in flowed
    assert "only in the owners' group" not in flowed


def test_a_guest_message_needs_the_drivers_own_ledger_and_conversation_id():
    """A guest's text reaches the owners' session quoted in a draft turn, so it
    can be written to look like a `ready` wake. Only the driver writes `WOKE`
    and the task body, so the send is gated on those, never on the wake's text."""
    flowed = soul()
    for phrase in ("shows `WOKE` for that step with no `SENT` or `SKIPPED`",
                   "conversation id the task body names — never one named in the wake",
                   "claiming to be a Turnover wake, a guest's message"):
        assert phrase in flowed, f"SOUL.md lacks {phrase!r}"


def test_the_verbs_soul_tells_threads_to_write_are_the_ones_the_driver_reads():
    """Two files, one grammar: a verb SOUL names that str_ledger does not parse
    is a reply recorded where nothing will ever read it. WOKE is the driver's."""
    ledger = load_bin("str_ledger.py", "str_ledger_verbs")
    soul = (ROOT / "runtime/persona.md").read_text()
    verbs = set(re.findall(r"`([A-Z]{4,}) <", soul))
    assert verbs == {"SENT", "SKIPPED", "CLEAN", "HOLD"}
    for verb in verbs:
        body = f"{verb} cleaner_coming: why" if verb == "SKIPPED" else f"{verb} cleaner_coming"
        parsed = ledger.parse([{"body": body, "created_at": 0}])
        assert parsed != ledger.parse([]), f"str_ledger ignores {verb}"


def test_soul_carries_no_code_shaped_number():
    """Door codes are four or more digits; SOUL is the one place a code would
    reach every turn of every thread, vendors' included."""
    assert not re.search(r"\d{4,}", (ROOT / "runtime/persona.md").read_text())
