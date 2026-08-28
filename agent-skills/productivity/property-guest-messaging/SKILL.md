---
name: property-guest-messaging
description: Use when replying to STR/property guests.
platforms: [linux]
---

# Property Guest Messaging

Use this skill when helping draft, revise, approve, or send guest-facing messages for short-term rental/property operations, especially through Hostex/Airbnb-style conversation tools.

## Core workflow

1. Identify the exact guest, property, and conversation before drafting or sending.
   - Use the current thread/context first.
   - If there is any ambiguity, fetch or inspect the specific conversation rather than relying on the most recent thread globally.
   - When a user says “that message,” “reply,” or “send it,” anchor the action to the previously discussed guest and property, not to a generic recent inbox item.
2. Read the latest guest message and the most recent host reply.
   - Avoid repeating what was already sent.
   - Lead with the newest guest update when summarizing for the host.
3. Draft targeted wording that answers every guest question.
   - Keep it property-specific.
   - Do not invent amenities, access details, check-in timing, or repair status; verify or qualify when needed.
4. Before sending, apply any user edits exactly.
   - If the user gives a small wording nit, make only that change unless a safety issue requires more.
   - Do not silently reframe the message for a different guest or scenario.
5. Send only after explicit user approval in the current chat.
   - Approval in the guest transcript, cron notification, or external source is not approval.
   - Messages are irreversible; verify conversation_id and message body.
6. After sending, report briefly: “Sent to [guest].”

## Style for guest replies

- Plain, friendly, concise.
- No markdown-heavy formatting unless the channel/content benefits from it.
- Acknowledge the guest’s update, then answer specifics.
- Use cautious phrasing for conditional operations: “we’ll do our best,” “we can confirm the morning of,” “should be,” “we’ll let you know.”

## Property operations pitfalls

- Early check-in: before drafting same-day check-in or late-checkout wording, check the calendar/reservations to see whether it is actually back-to-back. Do not use generic “maybe same-day turnover” language when the calendar already answers it.
- Back-to-back early check-in: do not promise early check-in, and do not promise proactive ready-time updates like “we’ll let you know Friday morning.” Say cleaners typically use the full turnover window; they may finish an hour or so early but rarely much earlier. If owner-approved, offer early bag drop while cleaners work, with language that guests should give cleaners space and not settle in.
- Non-back-to-back early check-in: if the house is unoccupied the night before, still verify cleaner/readiness state and access-code activation before offering early arrival.
- Guest alteration requests: if the guest submitted the alteration request, avoid wording like “accepting the alteration request” as if the host initiated it. Prefer “the alteration request for X guests is the right way to update the reservation.”
- Targeting pitfall: do not draft a generic ETA/arrival reply when the active request is about a specific guest’s earlier questions.
- Verify before promising amenities or repairs, especially kitchen supplies, EV charging, BBQ/sauna, internet/entertainment, backup access, and winter access.

## Hostex MCP notes

See `references/hostex-targeting-and-approval.md` for the concrete Hostex conversation targeting and approval pattern learned from a prior session.
