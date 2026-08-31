# Review instructions — sams-str-hermes-agent

Reviewer-facing policy for this repo. Read it before any other input: § Product
context says what this repo is, § Review priority says how to review it, and the
review-loop rules each reviewer appends bind every finding.

This file is the single source for both reviewers' *repo-specific* policy, but
they reach it
differently, and only one of them reaches it today:

- **roborev** is live today — on a copy, not this file. It cannot open files a
  config merely points at, so a sync script inlines this file into
  `.roborev.toml`'s `review_guidelines`. **An edit here does not reach roborev
  until `.roborev.toml` carries it.** That script is mid-move and currently
  reachable from neither side — `claude-config` deleted its copy, and the
  replacement merged into a stale branch of `plow-pbc/seed-auto-roborev`
  rather than its `main` — so until it lands, edit both files in lockstep.
- **knightwatch** will read it from the base branch once its loader change
  lands. Until then it falls back to the org-default operating point, and this
  repo's specifics below do not reach it.

`standards.md` references below resolve inside the knightwatch bundle only;
roborev cannot see that file.

This posture binds every PR from here on, including the ones that first
introduce the artifacts it names.

## Product context

**Stage:** Prototype. Not shipped, not a product, no dates. The goal right now
is a **working end-to-end loop**, not a hardened one. The README's Status table
says which parts of that loop exist; its Roadmap says in what order the rest
arrives. That order is a sequence, not a commitment, and it does not widen the
scope of any single PR.

**Userbase:** One. A single operator (srosro) managing his own short-term
rentals. Public repo; one deployment, no other consumers yet.

**What it is:** A Hermes agent in a container on one Linux host (`wakeup`),
deployed out of `~/services/sams-str-hermes-agent`, texted from iMessage
via the Plow Chat platform plugin, reading Airbnb/Hostex guest conversations
over Hostex's hosted MCP server for agent operations — with the pre-agent
inbound poller reading the same conversations over REST, since it runs before
any agent turn exists — and compiling them into an Obsidian vault of
property-operations knowledge.

**Trust boundary (known and accepted):** Guest-authored text is untrusted and
it reaches the agent runtime by design — that *is* the product. The runtime
holds a Hostex token, messaging tools, and a writable vault. Prompt-injection,
PII, and credential-exposure consequences reachable **through that existing
runtime and tool surface** are a known, accepted, ticketed risk at this stage
(see `#7`), not a new finding. § The carve-outs is not covered by that
acceptance — those stay blocking. Nothing here is exposed to anyone but the
operator.

**Architectural commitments:**
- Compose + the upstream `nousresearch/hermes-agent` image. No hand-rolled
  image beyond a thin derived layer. All non-vault state in `~/.hermes` on the host; the vault is `~/hermes-vault`, mounted in.
- Shell + small Python scripts. No application server, no framework, no
  database.
- Generated vault content is data, not engineered code — job output, not
  reviewed as code. The vault is not in this checkout — the nightly writes its
  pages to `~/hermes-vault`, and `scripts/promote-vault` commits and pushes
  them into the private `sams-str-vault` repo on a host-side schedule, gated by
  a credential scan rather than an operator read. None of it reaches this
  repo's `main`.

**Update cadence:** Edit when the stage changes — first non-operator user, or a
decision to actually harden the guest-text boundary. Otherwise static.

## Review priority

**Stage:** Prototype, one operator. The scarce resource is the
operator's time, not correctness at scale. A review round that only *adds* LOC
has cost us more than the bug it guarded.

**Cultural emphasis:** SIMPLIFY. Subtractive remedies (delete, collapse, don't
build it yet) outrank additive ones at every severity. The failure mode this
repo has actually hit is non-convergence: PR #1 ran ~30 review rounds and was
closed and resubmitted as #5. Iteration cost is the risk here, not shipping a
weakness only the operator can reach.

### Security findings become tickets, not PR scope

**This is the rule that most changes how this repo should be reviewed.**

Guest-authored text reaching a privileged agent runtime is the product's
premise, not a regression a PR introduced. Hardening that boundary — sandboxed
extraction, tool-surface restriction, PII scrubbing, capability separation — is
a whole project, and it is deliberately **after** the working prototype.

So, for a security finding on this repo:

- **Emit it at `low`**, phrased as "worth a ticket", and say what the ticket
  should record. Do not make it the reason a PR can't merge.
- **Do not propose an in-PR remedy that adds a trust boundary** around guest
  text — a sandbox, a broker process, a capability split, an allowlist layer, a
  sanitizing wrapper. Those are the project, not this PR. Proposing one is
  scope creep under `standards.md` § Anti-Bloat, and an author declining it
  with a ticket link is a correct answer, not an unaddressed probe.
  This is **not** a prohibition on assertions: a check that makes a silently-
  passing path fail loudly is always in scope (see "What is worth blocking on").
- **Check the existing tickets first.** If a finding restates an open issue,
  say so and move on — re-raising a recorded deferral each round is the
  non-convergence pattern this file exists to stop.

#### Tool reach is deliberate — do not propose narrowing it

The operator has ruled that Hermes holds **every** capability its integrations
offer, reads and writes alike (`#131`, Seam). So a PR that widens what a
prompt-injected agent could reach is not, on that ground alone, a finding — the
widening is the intent. Do not recommend dropping a tool from an `include:`
list, splitting a write surface away from its read surface, or gating a door
tool behind an approval step. Those are this section's remedies restated at the
capability layer, and they buy a lockout the operator can undo on his own house
at the price of the iteration speed he pays for every session.

The countermeasure he chose is **labelling, not gating**: inbound guest text
should carry a marker saying it is external and may carry prompt injection, so
the agent weighs it accordingly.

What stays reportable is the *record*. A PR that widens reach and says so —
in its body or in the README — has discharged it; one that widens reach
silently is § The carve-outs #2.

#### Accepted security deferrals — do not re-raise as new

| Issue | Accepted risk |
|---|---|
| `#7` | Prompt injection from guest text can reach the agent's *existing* tools |
| `#9` | Vault pages carry property access facts — door and keypad codes, key locations, entry fallbacks — **by design**. The operator ruled they belong there: the agent's job is getting guests in, and the vault lives in its own private repo and on the host, never in this one. Not a finding at any severity |

#### Accepted non-security deferrals — known, but not covered by the rule above

Don't re-raise these as new findings. They are *not* under the file-it-at-`low`
security posture — if a PR makes one materially worse, review it normally.

| Issue | Known gap |
|---|---|
| `#12` | `_raw/` retention documented at 12 months, nothing prunes |
| `#13` | Exported OKF bundles declare `okf_version 0.1` |

#### The carve-outs — still `blocking`

This is a posture on *hardening*, not a blanket security amnesty:

1. A real credential, token, or key **in the diff itself** or reachable in git
   history — a `HOSTEX_TOKEN`, `SEAM_API_KEY`, or anything else that belongs in
   `.env`. Property access facts in a vault page are the deliberate exception
   above, not an instance of this.
2. A change that **weakens a gate this repo's own tree enforces** — widening
   what `scripts/check-deploy-clean.sh` exempts, loosening a compose mount —
   without the PR saying why. `runtime/config.yaml` provisions the Hostex `tools.include`
   surface, so widening it lands here.

   `send_message` is the settled exception, not an instance of this. The
   operator granted it deliberately so a reply can reach the guest; the
   boundary is the two-tier guest-send instruction owned by `runtime/SOUL.md`
   (explicit approval by default; an announced 30-minute owner veto window for
   commitment-free, vault-verbatim drafts) and referenced from
   `bin/hostex-poll.py`, and it is a prompt instruction on purpose. Proposing
   a structural gate in its place — a draft store, an approval token, a
   `pre_tool_call` hook — is out of scope here and belongs to `#29` (public
   tracker: #5). The allowlist was never the security boundary it
   was documented as: the agent holds `terminal` and can read `HOSTEX_TOKEN`
   directly (`#46`).
3. A change that **creates a new path for `HOSTEX_TOKEN` or vault contents to
   leave the operator's machine** — a new outbound
   call, a log or artifact the token lands in, a write that escapes the vault.
   `#7` does not accept new exfiltration surface.
4. Destructive or unrecoverable data handling (clobbering the vault, force-push,
   deleting `~/.hermes` state).

### Contrast pairs

Beyond the universal set in `standards.md`:

| DON'T (in this repo) | DO |
|---|---|
| Ask for a restricted-extraction boundary, broker, or sandbox around guest text. | File it against `#7` and review the rest of the diff. |
| Review generated vault content as engineered code — it is job output, promoted on a schedule behind a credential scan. | Review the extractor and the promote gate, not the pages they produce. |
| Propose a defensive guard for an input shape not observed in the guest-conversation corpus. | Flag a failure the corpus actually produced, citing the file and the count. |
| Ask for a fallback when the operator would rather the run fail loudly and re-fetch. | Flag a failure that is *silent* — a check that passes on broken data. |
| Push a hardening remedy up to `blocking` because the runtime is privileged. | Emit it at `low` with the ticket it belongs to. |

### What is worth blocking on

The bug class that has actually cost this repo real work is **checks that pass
on broken data**: the Hostex fetcher read `body` where the API sends `content`,
so hundreds of well-formed files held thousands of empty messages while every
assertion — on frontmatter, on file existence — went green. Findings of that
shape are the highest-value thing a review can produce here, they are worth
blocking on, and the assertion that fixes one is never scope creep.
