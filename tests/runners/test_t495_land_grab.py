"""T-495: T-485/C2 residual -- a broad runner lease must not lock out siblings.

cos-opus found this during the T-485 pre-merge pass at 9707e7a. One
registration with a parent path denies every other unapproved agent every
directory beneath it, because the lease half of `find_worktree_claimant` used
symmetric `overlaps`. The operator registry keeps that symmetry; only the
runner-lease scan changes.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.runner_helpers import in_process_opener, rid  # noqa: F401

from ticket_board import worktrees  # noqa: E402
from ticket_board.runners import RunnerClient  # noqa: E402
from ticket_board.runners.client import ApiError  # noqa: E402
from ticket_board.server.wire import Request  # noqa: E402


FLEET_ROOT = "/Users/kavana/Downloads/steer/.worktrees"
VICTIM_WT = FLEET_ROOT + "/victim"


def _enroll(server, project, operator, *, name, role="backend", worktree=None,
            expect=201):
    body = {"request_id": rid(), "agent_name": name, "role": role,
            "connection_mode": "managed", "max_active_tickets": 1}
    if worktree is not None:
        body["worktree"] = worktree
    response = server.handle(Request("POST", "/enrollments", headers={
        "x-project-id": project["id"],
        "cookie": "tb_session=" + operator["session_token"],
        "x-csrf-token": operator["csrf_token"],
        "origin": "http://127.0.0.1:4319",
        "content-type": "application/json",
    }, body=json.dumps(body).encode()))
    assert response.status == expect, response.body
    return response


def _connect(server, project, created):
    session_id = "ses_" + uuid.uuid4().hex[:8]
    exchanged = server.handle(Request("POST", "/sessions", headers={
        "x-project-id": project["id"], "content-type": "application/json",
    }, body=json.dumps({
        "request_id": rid(), "code": created.body["code"],
        "session_id": session_id,
        "runtime": {"adapter": "claude_code", "version": "2.1.0"},
    }).encode()))
    assert exchanged.status == 201, exchanged.body
    client = RunnerClient("http://board.test", project["id"],
                          exchanged.body["token"],
                          opener=in_process_opener(server), retries=0,
                          sleep=lambda _s: None)
    return exchanged.body["agent"]["id"], client


def _agent(server, project, operator, *, name, role="backend", worktree=None):
    return _connect(server, project,
                    _enroll(server, project, operator, name=name, role=role,
                            worktree=worktree))


def _runner_id():
    return "rnr_" + uuid.uuid4().hex[:8]


@pytest.mark.parametrize("held,requested,blocks", [
    (FLEET_ROOT, VICTIM_WT, False),
    ("/w/a1l", "/w/a1l/sub", True),
    ("/w/a1l", "/w/a1l", True),
    ("/w/a1l/sub", "/w/a1l", True),
    ("/atman/.worktrees/desk", "/atman/.worktrees/desk/.worktrees/integration",
     False),
    ("/w/free", "/w/free/other", True),
    ("/w/C3/x", "/w/c3/x", True),
])
def test_lease_precludes_registration_drops_parent_not_child(held, requested,
                                                             blocks):
    assert worktrees.lease_precludes_registration(held, requested) is blocks


def test_an_unapproved_runner_may_not_land_grab_the_fleet_worktrees_root_then_lock_siblings(
        server, project, operator):
    """cos-opus C2, replayed at the route with this board's real layout.

    Neither agent has an operator-approved worktree. The grabber registers the
    bare `.worktrees` container; the victim registers its own checkout beneath
    it. Pre-T-495 that returned 403 for the victim; post-fix the sibling seat
    must still register.
    """
    grabber_id, grabber = _agent(server, project, operator, name="c2-grabber")
    lease = grabber.register(_runner_id(), grabber_id, FLEET_ROOT)
    assert lease["allowlisted_worktree"] == FLEET_ROOT, lease

    victim_id, victim = _agent(server, project, operator, name="c2-victim")
    lease = victim.register(_runner_id(), victim_id, VICTIM_WT)
    assert lease["allowlisted_worktree"] == VICTIM_WT, lease


def test_nested_worktree_seats_do_not_block_each_other(server, project, operator):
    """The accidental self-inflicted 403 from T-495's ticket body."""
    parent = "/atman/.worktrees/desk-cursor-fable"
    nested = parent + "/.worktrees/integration"

    parent_id, parent_client = _agent(server, project, operator,
                                      name="c2-parent")
    parent_client.register(_runner_id(), parent_id, parent)

    nested_id, nested_client = _agent(server, project, operator,
                                      name="c2-nested")
    lease = nested_client.register(_runner_id(), nested_id, nested)
    assert lease["allowlisted_worktree"] == nested, lease


def test_a_runner_still_cannot_wrap_a_narrower_live_lease(server, project,
                                                          operator):
    """The child direction and refuse-the-grab half must stay."""
    child_id, child = _agent(server, project, operator, name="c2-child")
    child.register(_runner_id(), child_id, VICTIM_WT)

    grabber_id, grabber = _agent(server, project, operator, name="c2-grab-over")
    with pytest.raises(ApiError) as caught:
        grabber.register(_runner_id(), grabber_id, FLEET_ROOT)
    assert caught.value.status == 403
