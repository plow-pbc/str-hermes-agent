---
name: str-setup
description: Use when str is not set up yet (no Hostex token configured), and when the owner asks to add a property, change the cleaner, or switch where drafts go. Onboarding over chat, from Plow Latch to the first scheduled job.
platforms: [linux]
---

# str setup

Run this only in the owner's own one-to-one thread: it collects keys and
writes config. Asked anywhere else, say the owner can start it privately with
you, and stop. The one exception is step 3's "trust this group", which the
owner says in the owners group itself.

This lands on a phone. One or two short lines per message, no bullet lists.
Answer what the owner actually said first, then carry on. Never narrate the
mechanics ("running setup-discover now"): send the question the step needs.

**Where you are is in the config, not in your memory.** Each step below says
how to tell it is already done; skip those and resume at the first one that is
not. Setup is finished when step 6 has run `register-jobs` cleanly.

Start every terminal command with `S=${HERMES_HOME:-/var/lib/hermes}/scripts;`
so `$S/` below resolves. `setup-discover <cmd>` prints JSON; on failure it exits
2 with one sentence on stderr written for the owner: relay it as it is.

**Secrets.** Pass a key to `str-config` only through a quoted heredoc, never as
an argument and never with `echo`:

```sh
S=${HERMES_HOME:-/var/lib/hermes}/scripts; "$S/str-config" <<'JSON'
{"env": {"HOSTEX_TOKEN": "<the token>"}}
JSON
```

Never repeat a key back in chat. Confirm only the masked form `str-config`
printed (`…` and the last three characters). A line reading "set by the
container, not written" means the deployment fixed that value: say so and move
on. If it refuses a value for a character it cannot write, ask the owner to
copy the key again without surrounding spaces or quotes.

## 1. Plow Latch

Run `"$S/setup-discover" latch`. `{"configured": true}` means continue. If it
is `false`, say: "str keeps its notes in your Mac's Plow wiki, so it needs Plow
Latch running on your Mac. Install it from https://plow.co/latch, then message
me again." Stop there.

## 2. Hostex token

Done if `"$S/setup-discover" hostex-properties` already succeeds.

Otherwise ask for their Hostex API token, write it as shown above, then run `"$S/setup-discover" hostex-properties`. Success is a
JSON list of their properties: name them back in one line. On exit 2, relay the
sentence and ask for the token again.

## 3. Where drafts go

On a first run, done if `PLOW_CHAT_APPROVAL_GROUP=STR Owners` is already in `.env`.

Ask: "Should guest-reply drafts come to you here, or to an owners group?"
"Here" needs nothing written: skip to step 4.

For a group:

1. The owner adds this line to the group in Messages.
2. Run `"$S/setup-discover" groups` and offer each group by its `title` and
   `members`. If the list is empty, the line is not in a group yet: ask them to
   add it and try again.
3. Read the current list with `grep '^PLOW_CHAT_GROUP_UIDS=' "${HERMES_HOME:-/var/lib/hermes}/.env"`.
   Keep every other entry, replace any entry already carrying this label, and
   add `<chat_uid>=STR Owners` (always exactly that label). Entries are
   comma-separated `<chat_uid>=<label>`. Write it:
   ```sh
   "$S/str-config" <<'JSON'
   {"env": {"PLOW_CHAT_GROUP_UIDS": "<merged list>", "PLOW_CHAT_APPROVAL_GROUP": "STR Owners"}}
   JSON
   ```
4. Say: "In that group, say 'trust this group' to me — that lets your
   co-owners approve drafts there." When the owner says it in the group, call
   `plow_set_conversation_trusted` with `trusted=true, confirm=true` (it works
   only on the owner's own turn in that group). Confirm from the tool result;
   if it fails, relay its error and mention the Plow dashboard's group-trust
   setting as the alternative.

## 4. Smart locks (optional)

On a first run, done if `ops.toml` (path below) already lists properties.

Ask: "Do you use smart locks through Seam? Then I can open doors on request
and check each morning that the cleaner has been in." If no, go to step 5.

If yes, ask for their Seam API key, write it as `{"env": {"SEAM_API_KEY": …}}`
the same way, and verify with `"$S/setup-discover" seam-devices`. On exit 2,
relay the sentence and ask again.

Then, from the `hostex-properties` list, ask which properties to watch. For
each one:

- the front door: offer `seam-devices` by `name`, keep its `seam_device_id`;
- the cleaner's codes: run `"$S/setup-discover" seam-codes <seam_device_id>`,
  offer them by `name`, keep each chosen `access_code_id`;
- the cleaner's first name;
- confirm the property's `timezone` and `default_checkin_time` from Hostex
  (ask for them if Hostex has none).

The cleaners group: pick it as in step 3, labelled exactly `Cleaners`, merged
into the same `PLOW_CHAT_GROUP_UIDS` list. Never trust it: full trust would let
cleaners use the owner's accounts. You may send one short intro there with
`send_message`, target `plow_chat:<chat_uid>` (the chat tool, not Hostex's
guest `send_message`), so the cleaners know who you are.

Write everything in one patch. `ops.timezone` is the first property's zone
unless the owner names another. `properties` replaces the whole list, so on a
re-run read `${VAULT:-${HERMES_HOME:-/var/lib/hermes}/repo/vault}/ops.toml`
first and send every property, old and new.

```sh
"$S/str-config" <<'JSON'
{"env": {"PLOW_CHAT_GROUP_UIDS": "<merged list>"},
 "ops": {"timezone": "<IANA zone>", "properties": [
   {"hostex_property_id": 123, "title": "<title>", "timezone": "<IANA zone>",
    "default_checkin_time": "16:00", "seam_device_id": "<id>",
    "cleaner_name": "<name>", "cleaner_access_code_ids": ["<id>"],
    "cleaners_thread": "Cleaners"}]}}
JSON
```

## 5. Timezone (no Seam)

Skip if step 4 set it. Otherwise propose the zone Hostex gives their first
property, let the owner confirm or correct it, and write
`{"ops": {"timezone": "<IANA zone>"}}`.

## 6. Finish

Run `"$S/register-jobs"`.

- It refuses with a sentence containing "restart": the agent has to restart
  once to run in the new timezone. Say: "Last step: restart me once from the
  Plow app, then text me 'done'." When they do, run `register-jobs` again.
- Each `register-jobs: create <name>` (or `replace`) line is a job now
  running. Tell the owner in plain words: `hostex-inbound` watches guest messages every two minutes,
  `wiki-nightly` updates the property notes at 3am, `checkin-watch` checks the
  cleaners at noon. No output means they were already installed: confirm with
  `hermes cron list`.
- Any other failure: relay its first line and say setup is not finished.

Never say jobs are running until `register-jobs` reported them.

**Later:** "add a property" or "change the cleaner" re-runs step 4; "switch
drafts to the group" re-runs step 3. Each ends with step 6.
