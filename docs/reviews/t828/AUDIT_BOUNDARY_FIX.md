# T-828: truthful join events after transfer

Independent T-846 found that a real installed wheel rolled back a late failed
transfer correctly but retained a `joined the board; roles=['backend']` event
describing the abandoned identity. The join application emitted that event
before its final inbox read.

Both source and packaged join applications now return their announcement after
the application and briefing steps complete. Their transaction callers emit it
after the rollback boundary, followed by the existing transfer audit. Failure
before that boundary restores the original six identity artifacts and emits no
join or transfer success event. Message history is never restored or truncated;
messages appended by another agent remain intact. Failure delivering a success
announcement after commitment does not undo the committed identity.

Validation used Python 3.9.6 and an actual built, installed wheel, isolated from
checkout imports:

- Focused transfer, identity isolation and hook tests: 26 passed.
- Unchanged independent T-846 checks, redirected to this candidate: 4 passed,
  including mixed source/installed-wheel 64-way rejoin and current-provider
  limit preservation.
- New checkin/unread failures exercise source and installed wheel, original raw
  bytes and file modes, no abandoned success event, and preservation of an
  unrelated message appended during the failing operation.
- Negative control against the prior T-846 candidate: all four unread mutations
  fail on the unexpected abandoned join event. This confirms the new guards
  catch the reported defect.

Wheel used by the independent checks:
`18079f463a2557a7ff1d5b6fca464cdd567448b80da6b37d19dd4682414b1ec2`.

This is a review submission, not an independent final approval. The existing
transfer snapshot, transport fencing and provider-state rules are unchanged.
