"""T-496: a case-variant path evades the claimant scan (cos-opus C3, found
during the T-485 pre-merge pass).

`worktrees.overlaps` compared paths by SEGMENT, which is right for `/w/agent`
vs `/w/agent-2`, but segments are still exact strings, so `/w/C3/x` and
`/w/c3/x` were two different segment lists. On darwin -- the default,
case-insensitive filesystem this fleet runs on -- those two strings name ONE
directory. Two agents could each register a runner lease against it and the
claimant scan, which exists precisely to stop that, would not see the
collision: no filesystem access needed, no symlink to create, just the shift
key. That is the same limit `worktrees.py` already declares for symlinks, only
cheaper, and it is the same argument that promoted A3 (relative vs absolute)
from a declared limit to a blocker-tier fix on T-485.

DIRECTIONAL, on purpose, same as A3. `overlaps` casefolds now; `contains` does
not and must not -- see `test_overlaps_casefolds_but_contains_never_does`
below for why folding both would be the wrong-direction fix.

This ticket does NOT touch C9 (the enrolment door's blindness to runner
leases): that is a documented consequence, not a code change -- see
docs/api-notes.md.
"""

from __future__ import annotations

import pytest

from conftest import in_process_opener, rid  # noqa: F401

from ticket_board import worktrees  # noqa: E402
from ticket_board.runners.client import ApiError  # noqa: E402

from test_t485_worktree_containment import _agent, _runner_id  # noqa: E402


# --------------------------------------------------------- C3: the claimant scan

def test_a_case_variant_path_can_no_longer_claim_an_occupied_checkout(
        server, project, operator):
    """cos-opus C3, replayed exactly.

    Neither agent has an operator-approved worktree, so this is the runner-
    lease half of the claimant scan (A1's addition): c3-a registers
    `/w/C3/x` and gets a lease; c3-b then registers `/w/c3/x` -- on
    9707e7a/818ad75 this ALSO returned 200, two live leases on what darwin
    treats as one directory. It must now be refused the same way an exact
    duplicate would be.
    """
    first_id, first = _agent(server, project, operator, name="c3-a")
    lease = first.register(_runner_id(), first_id, "/w/C3/x")
    assert lease["allowlisted_worktree"] == "/w/C3/x", lease

    second_id, second = _agent(server, project, operator, name="c3-b")
    with pytest.raises(ApiError) as caught:
        second.register(_runner_id(), second_id, "/w/c3/x")
    assert caught.value.status == 403
    assert "c3-a" in str(caught.value), str(caught.value)


def test_overlaps_casefolds_but_contains_never_does():
    """The unit-level guard, so a future tidy-up does not fold both.

    `overlaps` answers "is this checkout free?", where the loose answer is a
    REFUSAL -- folding case there is the safe side even on a case-SENSITIVE
    filesystem, where a genuine `/w/C3`/`/w/c3` pair would wrongly collide.
    `contains` answers "may this runner run here?", where the loose answer is
    a GRANT, so it must stay case-sensitive: an operator who approved
    `/w/C3` did not thereby approve `/w/c3`.
    """
    assert worktrees.overlaps("/w/C3/x", "/w/c3/x") is True
    assert worktrees.contains("/w/C3", "/w/c3/x") is False

    # The name-PREFIX rule survives the fold: `/w/AGENT` and `/w/agent-2`
    # share a case-folded prefix but are still two different directories.
    assert worktrees.overlaps("/w/AGENT", "/w/agent-2") is False

    # Casefolding composes with the existing `.`/`..` normalisation rather
    # than replacing it.
    assert worktrees.overlaps("/w/A/../B", "/w/b") is True
