# Usage-limit expiry

A recorded limit pauses routing, dispatch and wake while its hold is active.
An explicit provider reset timestamp takes precedence. At capture, `resets
1:40am` resolves to the next occurrence in the capturing machine's local
timezone; a parenthesized IANA timezone overrides that default. `back in 50m`,
`retry in 30 seconds`, and `resets in 2 hours` resolve relative to capture time.
The resulting UTC `reset_at` is stored once, so polling never moves the reset.
Manual `atm limit --until` and `--note` use the same parser.

When no valid reset can be parsed, the observation expires after **five hours**.
A note explicitly naming a **weekly limit or quota** instead gets seven days.
These are bounded retry policies, not evidence that provider quota is available;
a new rejection starts a new hold. Invalid or absent observation timestamps
cannot justify an indefinite hold and are immediately considered stale.
Existing records use their original observation time, never their read time.

Expiry atomically removes the active seat limit and its failed-trigger hold,
retaining the observation and expiry reason for diagnosis. Read-only snapshots
apply the same policy without writing. Old rejection transcripts do not renew
an expired observation. Provider usage readings remain separately labeled
observations; expiry does not invent remaining quota.

`atm limits` lists oldest observations first and labels expired ones `STALE`,
with the reason and the fact they no longer block. Board attention calls out
stale limits so leadership can check that parked seats resume. Retained expiry
evidence is replaced by the next captured limit.
