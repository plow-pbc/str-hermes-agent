---
name: str-setup
description: Use in the owner's one-to-one chat when str is not set up yet (no HOSTEX_TOKEN in $HERMES_HOME/.env or $HERMES_HOME/str/setup.env), and when the owner asks to add a property, change the cleaner, or switch where drafts go. Onboarding over chat, from Plow Latch to the first scheduled job.
platforms: [linux]
---

# str setup

Run this only in the owner's own one-to-one thread: it writes config. Asked
anywhere else, say the owner can start it privately with you, and stop. The
one exception is step 3's "trust this group", which the owner says in the
owners group itself.

This lands on a phone: one or two short lines per message, no bullet lists.
Answer what the owner said first, then carry on. Never narrate the mechanics.

**Where you are is in the config, not in your memory.** Each step below says
how to tell it is already done; skip those and resume at the first one that is
not. Setup is finished when step 6 has run `register-jobs` cleanly.

Start every terminal command with `S=${HERMES_HOME:-/var/lib/hermes}/scripts;`
so `$S/` below resolves. `setup-discover <cmd>` prints JSON, and `str-config`
prints one line per thing it saved; on failure either exits non-zero with one
sentence on stderr written for the owner: relay it as it is.

**Keys never come through chat.** Anything typed here goes to the model
provider. The owner pastes keys into a file on their own Mac, and `str-config
--from-latch` reads it over Plow Latch, saves it, and empties the file. Never
ask for a key here, never print `.env`, `str/setup.env` or any key's value, and
relay only the masked line `str-config` printed. If the owner pastes a key into
chat anyway, don't repeat it: tell them to rotate it in Hostex (or Seam),
because it has left their Mac, and carry on with the file.

**Getting a key** (steps 2 and 4): run `"$S/str-config" --latch-template`, then
say: "Open ~/Plow/str-keys.env on your Mac, paste your <Hostex|Seam> key after
the = sign, save, then tell me done." When they do, run
`"$S/str-config" --from-latch`. If it refuses a value's characters, ask them to
paste it into the file again without spaces or quotes around it, then run it again.

Everything else goes to `str-config` as JSON on stdin, through a quoted heredoc:

```sh
S=${HERMES_HOME:-/var/lib/hermes}/scripts; "$S/str-config" <<'JSON'
{"env": {"PLOW_CHAT_APPROVAL_GROUP": ""}}
JSON
```

## 1. Plow Latch

Run `"$S/setup-discover" latch`. `{"configured": true}` means continue. If it
is `false`, say: "str keeps its notes in your Mac's Plow wiki and reads your
keys from it, so it needs Plow Latch running on your Mac. Install it from
https://plow.co/latch, then message me again." Stop there.

## 2. Hostex key

Done if `"$S/setup-discover" hostex-properties` already succeeds.

Otherwise get the Hostex key as above, then run
`"$S/setup-discover" hostex-properties`. Success is a JSON list of their
properties: name them back in one line. On exit 2, relay the sentence and get
the key again.

## 3. Where drafts go

On a first run, done if `PLOW_CHAT_APPROVAL_GROUP=STR Owners` is in
`str/setup.env` and the group labelled `STR Owners` shows `"trusted": true` in
`plow_send_message(action=list)`.

Ask: "Should guest-reply drafts come to you here, or to an owners group?"
"Here": write `{"env": {"PLOW_CHAT_APPROVAL_GROUP": ""}}` (clears a group
chosen before), then go to step 4.

For a group:

1. The owner adds this line to the group in Messages, and someone sends a
   message there: Plow only lists a group once a message arrives from it.
2. Call `plow_send_message(action=list)` and offer the chats with
   `kind` `group`, by `title` (or their participants' names when there is
   none). None there means nobody has written in it yet: ask someone to send a
   message there, then list again.
3. Read the current list with
   `grep -h '^PLOW_CHAT_GROUP_UIDS=' "${HERMES_HOME:-/var/lib/hermes}/str/setup.env"`.
   Entries are comma-separated `<chat_id>=<label>`. Keep every other entry,
   replace any entry already carrying this label, add `<chat_id>=STR Owners`
   (always exactly that label), and write:
   ```sh
   "$S/str-config" <<'JSON'
   {"env": {"PLOW_CHAT_GROUP_UIDS": "<merged list>", "PLOW_CHAT_APPROVAL_GROUP": "STR Owners"}}
   JSON
   ```
4. Say: "In that group, say 'trust this group' to me. Trusting it lets everyone
   there use my accounts and what I recall from my other chats without asking
   you, which is what lets your co-owners approve drafts."
   When "trust this group" arrives in a group, first run
   `printenv HERMES_SESSION_CHAT_ID` and the `grep` from item 3. Only if this
   chat's uid is the one labelled `STR Owners`, call
   `plow_set_conversation_trusted` with `trusted=true, confirm=true` (it works
   only on the owner's own turn). Anywhere else, decline: you only trust the
   owners group chosen in setup. On failure, relay its error and point to the
   group-trust setting on the Plow dashboard.

## 4. Smart locks (optional)

On a first run, done if `ops.toml` (path below) already lists properties.

Ask: "Do you use smart locks through Seam? Then I can open doors on request
and check each morning that the cleaner has been in." If no, go to step 5.

If yes, and `"$S/setup-discover" seam-devices` does not already succeed, get
the Seam key as above and verify with `seam-devices`. On exit 2, relay the
sentence and get it again.

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
`plow_send_message`, to its `cht_` id, so the cleaners know who you are.

Write everything in one patch. On a first run add `"timezone": "<IANA zone>"`
to `ops`: the first property's zone unless the owner names another. On a
re-run leave it out (moving it forces a restart), and since `properties`
replaces the whole list, read
`${VAULT:-${HERMES_HOME:-/var/lib/hermes}/repo/vault}/ops.toml` first and
send every property, old and new.

```sh
"$S/str-config" <<'JSON'
{"env": {"PLOW_CHAT_GROUP_UIDS": "<merged list>"},
 "ops": {"properties": [
   {"hostex_property_id": 123, "title": "<title>", "timezone": "<IANA zone>",
    "default_checkin_time": "16:00", "seam_device_id": "<id>",
    "cleaner_name": "<name>", "cleaner_access_code_ids": ["<id>"],
    "cleaners_thread": "Cleaners"}]}}
JSON
```

## 5. Timezone (no Seam)

Skip if `ops.toml` has one. Otherwise propose their first property's Hostex
zone, let the owner confirm it, and write `{"ops": {"timezone": "<IANA zone>"}}`.

## 6. Finish

The Hostex and Seam tools, the chat plugin and the timezone read config only
when the agent starts. So if this run wrote a key (`HOSTEX_TOKEN`,
`SEAM_API_KEY`, `PLOW_CHAT_*`), say: "Last step: restart me once from the Plow
app, then text me 'done'.", and run `"$S/register-jobs"` when they do.
Otherwise run it now, and if it refuses with a sentence containing "restart",
ask for that same restart and run it again after.

- Ask for one restart per run, never two: if it still says "restart"
  afterwards, relay its line and say setup isn't finished.
- Each `register-jobs: create <name>` (or `replace`) line is a job now
  running. Tell the owner in plain words: `hostex-inbound` watches guest
  messages every two minutes, `wiki-nightly` updates the property notes at 3am,
  `checkin-watch` checks the cleaners at noon. No output means they were
  already installed: confirm with `hermes cron list`.
- Any other failure: relay its first line and say setup is not finished.

Never say jobs are running until `register-jobs` reported them.

**Later:** "add a property" or "change the cleaner" re-runs step 4; "switch
drafts to the group" re-runs step 3. Each ends with step 6.
