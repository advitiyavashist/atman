# T-1089 protocol gate review

The protocol now teaches all four facts: review pins a head, a different seat
accepts that exact head, dependents remain withheld until acceptance and
completion, and a moved head voids acceptance. Self-acceptance is forbidden for
workers and coordinators alike.

## Artifacts to review together

This Atman PR contains the source protocol in both CLI implementations,
checked-in AGENTS.md and Cursor startup rules, master/CoS prompts, the worker
one-line gate, role templates, SEATS_AND_HOOKS, and regression tests.

Steer's existing startup files are tracked in a separate repository. Their
companion change is committed on `codex/t1089-steer-protocol` at
[`2dc96043c35fdcfd84abf9b93d5db91101e791a4`](https://github.com/advitiyavashist/steer/commit/2dc96043c35fdcfd84abf9b93d5db91101e791a4).
It updates `AGENTS.md` and both files under `.cursor/rules/`, preserving Steer's
project-specific preamble. There is one Atman PR; no second PR or merge was
performed. The reviewer must inspect this companion commit too and coordinate
its integration into Steer. Neither repository's changes are live merely
because the author submitted them.

Live board `briefs/roles/master.md` and `briefs/roles/cos.md` were swept and
received the same gate through `atm brief --role`, never by hand-editing the
board. They are board state, not Git deliverables.

## Validation

Foreground interpreter: `/Users/kavana/Downloads/steer/.venv/bin/python -m pytest`.
Initial run covered `test_t1089_protocol_gate.py`, `test_t263_init_isolation.py`,
`test_t1077_quickstart.py`, and `test_t1078_seat_brief.py`: 62 passed, two failed.
The failures were the replaced prose assertion and an expired fixed usage-limit
reset date. The assertion now checks the withheld-until-accept rule; the fixture
uses a reset seven days ahead of the run.

The new protocol suite plus those two failing cases were rerun: 12 passed.
Steer's three startup files separately passed the same gate-fact and forbidden
pre-gate-instruction assertions. `git diff --check` passed in both repositories.
No production gate semantics changed, and the author recorded no accept.
