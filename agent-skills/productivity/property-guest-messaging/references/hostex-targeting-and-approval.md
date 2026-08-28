# Hostex targeting and approval pattern

Session lesson: a guest-reply workflow went off track when a generic/most-recent Hostex conversation was used instead of the specific guest the host intended. The user corrected that the reply was for Ann, not Chris/Jim-style ETA context.

## Durable pattern

- First lock onto the intended thread: guest name, property, and conversation_id.
- If the user references “Ann,” “Chris,” or another guest by name, call/get the specific conversation for that guest before drafting or sending.
- Do not assume the newest Hostex conversation is the one to reply to.
- When the user asks for a “targeted edit,” preserve the existing drafted reply and make the requested change only.
- Before irreversible send_message, verify:
  - conversation_id matches the intended guest
  - message body includes the user’s latest wording correction
  - message body does not include disallowed external contact info/links

## Example wording lessons

Early check-in during busy season:

> On early check-in: we usually can’t confirm that until the morning of check-in, especially during this season when we often have back-to-back bookings and need to coordinate the cleaning schedule. We’ll do our best and can let you know that morning if the cabin is ready early.

Guest-submitted alteration request:

> The alteration request for 8 guests is the right way to update the reservation.

Avoid “accepting the alteration request” when the guest submitted it; that phrasing can sound like the host is taking the action rather than acknowledging the guest’s submitted request.

## Minimal completion report

After a successful send, keep the host-facing confirmation brief:

> Sent to Ann.
