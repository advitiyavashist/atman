# Promise candidates (T-1047)

Three one-liners, in the reader's words, for the merged accept gate: a
dependent ticket does not start until its predecessor is ACCEPTED on an
exact commit by a seat that is not its author. The chosen line is #1. It
is the H1 on `README.md` and `landing/index.html`.

## 1. The next ticket opens only after someone else accepts that commit.

**Chosen.** 11 words. Names the visible outcome (`atm next` can claim the
child) and the gate (another seat, that commit) without Atman jargon.

Evidence:

- `tests/test_t1031_accept_gate.py` — `test_done_without_accept_keeps_successor_blocked`:
  `atm done` alone leaves the child `blocked`; `atm next` / `atm claim` refuse it.
- Same file — `test_accept_then_done_releases_handoff_sha` and
  `test_done_then_accept_releases_successor`: a structured accept on the
  review head opens the child and puts the SHA in its handoff.
- `src/ticket_board/work_view.py` — `dep_released()` is false unless
  status is `done` and `structured_accept` (or merge / recorded override)
  matches the 40-character `review_head`.
- `tests/test_t944_accept_reject.py` — `test_author_cannot_accept_own_work`,
  `test_short_sha_is_refused_for_accept`.
- README table rows: "Acceptance bound to the full review SHA; author
  cannot accept own work" and "Dependent ticket stays closed until its
  predecessor is accepted…".

## 2. Nobody starts the next task until a non-author accepts the exact commit.

13 words. More precise (non-author, exact) and still demonstrable, but
"non-author" and "exact commit" are reviewer language, not what a first
reader says after the walkthrough.

Evidence: the T-1031 `WORK_ENTRY_POINTS` parametrize (`next`, `claim`,
`assign`, `reserve`, `dispatch`, `route --claim`, …) all share
`dep_released()`; T-944 refuses the author and a short SHA. Same table
rows as #1.

## 3. Downstream work stays closed until another reviewer accepts the commit.

11 words. True, but "downstream" and "reviewer" are ours. It also drops
the exact-SHA part that `atm accept` actually enforces.

Evidence: `dep_released()` plus T-944 short-SHA and author refusals; same
table rows as #1.

## Not used

- "Atman turns agent handoffs into a verification record, not a promise."
  — current README H1. Clever, no demo, not the gate.
- "Atman coordinates the agents you already run." — current Pages H1.
  Table-stakes coordination; not something we uniquely demonstrate.
