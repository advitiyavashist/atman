"""T-485: the three residuals cos-opus executed against T-192 (A3, A2, A1).

All three were found by running code, not by reading it, and none of them was
declared by T-192's author. They share one shape: a check that LOOKS like it
holds a floor, and a path around it that costs the caller nothing.

  A3  `/w/a3` and `w/a3` segmented identically, so a runner approved for the
      absolute directory could register the relative one -- which
      `ClaudeLauncher.start` resolves against the SUPERVISOR's working
      directory, i.e. a directory that was never inside the approved one.
  A2  `resolve()` is exact-match, which is right, but a near miss fell to
      DEFAULT_PRESET = `worker`, which is BROADER than the `reviewer` the
      operator was reaching for. A capitalisation slip turned a read-only
      reviewer into a writer, with no signal on either route.
  A1  `worktree` is OPTIONAL at enrolment, and when it was omitted the
      containment branch was skipped ENTIRELY -- not "only the policy is
      enforced", but the runner's own string honoured and recorded as an
      allowlist, including a directory the enrolment registry had refused to
      another agent moments earlier.

Route-level throughout, for the reason T-192's file gives: the defect lives at
the seam between the operator credential and the agent one, and a unit test on
either side of that seam cannot see it. The exceptions are the two `worktrees`
tables below, which pin comparisons that have no route of their own.

Teeth were RUN, not asserted -- see the T-485 review notes for the pre-fix
pass/fail split and, for the tests that pass on the pre-fix source, the
targeted mutation that kills each one.
"""

from __future__ import annotations

import json
import uuid

import pytest

from conftest import in_process_opener, rid  # noqa: F401

from ticket_board import presets, worktrees  # noqa: E402
from ticket_board.runners import RunnerClient  # noqa: E402
from ticket_board.runners.client import ApiError  # noqa: E402
from ticket_board.server.wire import Request  # noqa: E402


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
    """Enrol AND connect, which is what every case below actually needs."""
    return _connect(server, project,
                    _enroll(server, project, operator, name=name, role=role,
                            worktree=worktree))


def _runner_id():
    return "rnr_" + uuid.uuid4().hex[:8]


# ------------------------------------------------- A3: the containment floor

def test_a_relative_worktree_no_longer_passes_the_containment_floor(
        server, project, operator):
    """cos-opus A3, replayed with its own strings.

    Approved `/w/a3`, registered `w/a3/src`, got 200 and a lease naming a
    directory the supervisor would resolve against its own cwd. The refusal
    has to say RELATIVE: an operator told only "not inside it" will look at
    `w/a3/src`, see that it plainly is inside `/w/a3`, and conclude the board
    is broken.
    """
    agent_id, client = _agent(server, project, operator, name="a3",
                              worktree="/w/a3")

    with pytest.raises(ApiError) as caught:
        client.register(_runner_id(), agent_id, "w/a3/src")
    assert caught.value.status == 403
    assert caught.value.code == "forbidden_scope"
    assert "relative" in str(caught.value).lower(), str(caught.value)


def test_the_containment_floor_still_lets_a_runner_narrow_within_its_worktree(
        server, project, operator):
    """The other half of A3, and the one that makes the fix a narrowing rather
    than a wall. Refusing relative paths must not refuse the absolute
    subdirectory that is the whole point of allowing a runner to narrow."""
    agent_id, client = _agent(server, project, operator, name="a3-narrow",
                              worktree="/w/a3n")

    lease = client.register(_runner_id(), agent_id, "/w/a3n/src/deep")
    assert lease["allowlisted_worktree"] == "/w/a3n/src/deep", lease


def test_a_traversal_worktree_cannot_climb_out_of_the_approved_directory(
        server, project, operator):
    """`..` is the other way to name an outside directory with an inside-
    looking string. Both forms are covered: one that normalises to a sibling,
    and a relative one whose `..` cannot be folded away at all."""
    agent_id, client = _agent(server, project, operator, name="a3-dots",
                              worktree="/w/a3d")

    for escape in ("/w/a3d/../a3d-evil", "../a3d", "/w/a3d/../../etc"):
        with pytest.raises(ApiError) as caught:
            client.register(_runner_id(), agent_id, escape)
        assert caught.value.status == 403, escape


@pytest.mark.parametrize("approved,requested,allowed", [
    # The exact cos-opus case, both directions.
    ("/w/a3", "w/a3/src", False),
    ("w/a3", "/w/a3/src", False),
    # Absolute against absolute is unchanged.
    ("/w/a3", "/w/a3/src", True),
    ("/w/a3", "/w/a3", True),
    ("/w/a3", "/w/a3/", True),
    ("/w/a3/", "/w/a3", True),
    ("/w/a3", "/w/a3/./src", True),
    # A shared name prefix is still two directories, and a parent is still
    # more than the child -- neither is affected by the new floor.
    ("/w/a3", "/w/a3-2", False),
    ("/w/a3/src", "/w/a3", False),
    ("/w/a3", "/", False),
    # `..` refused on either side rather than resolved on the caller's behalf.
    ("/w/a3", "/w/a3/../a3-evil", False),
    ("../w/a3", "../w/a3/src", False),
    # Relative on BOTH sides is still comparable: the rule is that they must
    # agree, not that relative paths are forbidden outright.
    ("w/a3", "w/a3/src", True),
])
def test_containment_compares_absoluteness_as_well_as_segments(
        approved, requested, allowed):
    """The comparison itself, with no route in the way.

    A table rather than prose because the failure it pins is one the reader's
    eye slides over: `/w/a3` and `w/a3` differ by a character that `_segments`
    used to discard.
    """
    assert worktrees.contains(approved, requested) is allowed


def test_overlaps_is_deliberately_not_absoluteness_strict():
    """A guard on the ASYMMETRY, so a future tidy-up does not "fix" it.

    `contains` answers "may this runner run here?", where the loose answer is
    a GRANT, so it must be strict. `overlaps` answers "is this checkout
    free?", where the loose answer is a REFUSAL. If `/w/a` and `w/a` might be
    one directory, the registry should decline to hand the second one out.
    Making these two consistent would look like a cleanup and would quietly
    turn that refusal into a grant.
    """
    assert worktrees.overlaps("/w/a", "w/a") is True
    assert worktrees.contains("/w/a", "w/a") is False
    # ...and the name-prefix rule the loose form must NOT give up.
    assert worktrees.overlaps("/w/agent", "/w/agent-2") is False
    # `normalize` folding `.`/`..` is load-bearing HERE and nowhere else, so
    # it is pinned here. `contains` refuses a surviving `..` outright and
    # `_segments` drops `.`, which makes `posixpath.normpath` look redundant;
    # it is not. Dropping it leaves `/w/a/../b` and `/w/b` as different
    # segment lists, so the registry would hand out a directory it already
    # gave to somebody else. Verified by mutation: removing that call breaks
    # nothing in tests/runners, tests/server or tests/storage without this
    # line.
    assert worktrees.overlaps("/w/a/../b", "/w/b") is True


# ----------------------------------------------------------- A2: near misses

def test_a_capitalised_role_gets_the_preset_it_names_not_the_broader_default(
        server, project, operator):
    """cos-opus A2, replayed at the route.

    `role="Reviewer"` used to miss the exact-match table and fall to `worker`,
    so the runner was GRANTED `allowlist` -- where the same registration under
    `reviewer` is refused. Case is a typo, not a different role.
    """
    agent_id, client = _agent(server, project, operator, name="a2-cap",
                              role="Reviewer", worktree="/w/a2cap")

    lease = client.register(_runner_id(), agent_id, "/w/a2cap")
    assert lease["permission_policy"] == "deny_all", lease

    # And the refusal that `reviewer` gets is now the refusal `Reviewer` gets.
    other_id, other = _agent(server, project, operator, name="a2-cap-2",
                             role="  REVIEWER  ", worktree="/w/a2cap2")
    with pytest.raises(ApiError) as caught:
        other.register(_runner_id(), other_id, "/w/a2cap2",
                       permission_policy="allowlist")
    assert caught.value.status == 403


@pytest.mark.parametrize("role,near", [
    ("reviewers", "reviewer"),
    ("review", "reviewer"),
    ("worker-2", "worker"),
    ("master2", "master"),
])
def test_a_near_miss_role_is_refused_rather_than_silently_widened(
        server, project, operator, role, near):
    """The refusal, and the reason it is a refusal and not a best guess.

    There is no safe default for `reviewers`: `worker` grants more than the
    operator asked for, and picking `reviewer` would be exactly the fuzzy
    GRANT this module refuses to do. So the request fails and names the preset
    it nearly matched, which is the one thing the operator needs to know.
    """
    response = _enroll(server, project, operator, name="nm-" + role, role=role,
                       expect=400)
    assert near in json.dumps(response.body), response.body
    assert response.body["error"]["details"]["rejected_fields"] == ["role"]


def test_a_refused_role_creates_no_agent_and_mints_no_code(
        server, project, operator):
    """A2's fix runs BEFORE `create_agent`, and it has to stay there. A 400
    that left a half-created agent behind would hand the operator a name
    collision on their corrected retry."""
    _enroll(server, project, operator, name="nm-partial", role="reviewers",
            expect=400)

    assert server.store.find_agent_by_name(project["id"], "nm-partial") is None
    # The corrected retry is the case that actually matters to the operator.
    _enroll(server, project, operator, name="nm-partial", role="reviewer")


@pytest.mark.parametrize("role", [
    "backend", "infra", "console", "verification", "frontend",
    # The anti-substring case from T-192's docstring, unchanged: this contains
    # "review" and must NOT become a reviewer, and it is nobody's prefix, so it
    # must not be refused either.
    "code-review-tooling",
    # And the sharper version of it, which is what draws the line A2's fix has
    # to stay on. This CONTAINS "reviewer" outright. A typo of `reviewer`
    # mangles its end or its case; it does not wrap the word in two other
    # words. So a compound role is a different role and gets the default --
    # refusing it would be the substring heuristic again, just inverted from
    # granting to refusing, and it would brick ordinary role names.
    "backend-reviewer-support",
    "release-master-tooling",
])
def test_an_unrelated_role_still_gets_the_default_preset(
        server, project, operator, role):
    """The regression guard on judgement call (b).

    Every role the live board actually uses is unknown to the preset table.
    A2's fix narrows near misses; if it narrowed these too, enrolment would
    start failing for ordinary roles and the predictable operator response is
    to turn enforcement off.
    """
    assert presets.resolve(role) == presets.DEFAULT_PRESET
    agent_id, client = _agent(server, project, operator, name="ok-" + role,
                              role=role, worktree="/w/ok-" + role)
    lease = client.register(_runner_id(), agent_id, "/w/ok-" + role)
    assert lease["permission_policy"] == "allowlist", lease


# ------------------------------------------------------------- A1: the other door

def test_a_runner_without_an_approved_worktree_cannot_claim_an_occupied_checkout(
        server, project, operator):
    """cos-opus A1, replayed exactly.

    Enrol an occupant at `/w/a1-occupied`; a second ENROLMENT there is refused
    by the registry; then enrol an agent with NO worktree and register that
    same directory. That used to return 200 with
    `allowlisted_worktree=/w/a1-occupied` -- the exact path the registry had
    refused thirty seconds earlier, handed over through the other door.
    """
    _agent(server, project, operator, name="a1-occupant",
           worktree="/w/a1-occupied")
    _enroll(server, project, operator, name="a1-blocked",
            worktree="/w/a1-occupied", expect=400)

    sneak_id, sneak = _agent(server, project, operator, name="a1-sneak")

    with pytest.raises(ApiError) as caught:
        sneak.register(_runner_id(), sneak_id, "/w/a1-occupied")
    assert caught.value.status == 403
    assert "a1-occupant" in str(caught.value), str(caught.value)


def test_the_other_door_is_shut_for_a_child_of_an_occupied_checkout_too(
        server, project, operator):
    """Equality would be a weaker check than the enrolment route's, which
    refuses a parent AND a child. An agent working in `/w/a1c` is working in
    every directory under it."""
    _agent(server, project, operator, name="a1c-occupant", worktree="/w/a1c")
    sneak_id, sneak = _agent(server, project, operator, name="a1c-sneak")

    with pytest.raises(ApiError) as caught:
        sneak.register(_runner_id(), sneak_id, "/w/a1c/src")
    assert caught.value.status == 403


def test_a_directory_claimed_only_by_a_lease_is_not_free_for_a_second_runner(
        server, project, operator):
    """The half `find_worktree_occupant` alone cannot see.

    Neither agent here has an operator-approved worktree, so neither has a
    value in `agents.worktree` and the enrolment registry is blind to both.
    The first one's claim lives on its RUNNER LEASE. Without the lease scan,
    A1's fix would close the door only against operator-approved checkouts and
    leave runner-claimed ones open to exactly the case it exists to stop.
    """
    first_id, first = _agent(server, project, operator, name="a1l-first")
    lease = first.register(_runner_id(), first_id, "/w/a1l")
    assert lease["allowlisted_worktree"] == "/w/a1l"

    second_id, second = _agent(server, project, operator, name="a1l-second")
    with pytest.raises(ApiError) as caught:
        second.register(_runner_id(), second_id, "/w/a1l/sub")
    assert caught.value.status == 403
    assert "a1l-first" in str(caught.value), str(caught.value)


def test_a_runner_without_an_approved_worktree_may_still_take_a_free_directory(
        server, project, operator):
    """The anti-over-refusal, and it is load-bearing.

    `worktree` is OPTIONAL in the frozen CreateEnrollmentRequest and T-192
    already reverted one draft that made it mandatory. A1's fix must refuse an
    OCCUPIED directory, not every unapproved one -- refusing all of them would
    be the same contract regression wearing a different hat.
    """
    agent_id, client = _agent(server, project, operator, name="a1-free")
    runner_id = _runner_id()

    lease = client.register(runner_id, agent_id, "/w/a1-free")
    assert lease["allowlisted_worktree"] == "/w/a1-free", lease
    # And a runner renewing its OWN live lease must not read as somebody else
    # occupying the directory -- which is what the claimant scan would say
    # without `exclude_agent_id`, and it would say it on every renewal.
    again = client.register(runner_id, agent_id, "/w/a1-free",
                            expected_epoch=lease["epoch"])
    assert again["allowlisted_worktree"] == "/w/a1-free", again


def test_a_runner_without_an_approved_worktree_cannot_register_a_relative_path(
        server, project, operator):
    """A1 and A3 meeting.

    With no approved directory there is nothing to contain the request, so the
    absolute-path floor is the ONLY thing standing between a runner-supplied
    string and `Path(spec.worktree)` as the child's cwd. The refusal has to
    say what to do instead, because the operator's fix is on the enrolment
    side, not the runner's.
    """
    agent_id, client = _agent(server, project, operator, name="a1-rel")

    for bad in ("w/a1-rel", "./a1-rel", "../a1-rel"):
        with pytest.raises(ApiError) as caught:
            client.register(_runner_id(), agent_id, bad)
        assert caught.value.status == 403, bad
    assert "re-enrol" in str(caught.value).lower(), str(caught.value)


def test_a_revoked_agents_lease_does_not_hold_a_checkout(
        server, project, operator):
    """Judgement call (c) carried into the new check.

    T-192 made revocation the way an operator frees a checkout, since there is
    no delete-agent route. The lease scan added for A1 has to honour the same
    lever, or a revoked seat would hold a directory that the ENROLMENT route
    already considers free -- two checks disagreeing about one question.
    """
    departing_id, departing = _agent(server, project, operator,
                                     name="a1-departing")
    departing.register(_runner_id(), departing_id, "/w/a1-reused")

    successor_id, successor = _agent(server, project, operator,
                                     name="a1-successor")
    with pytest.raises(ApiError):
        successor.register(_runner_id(), successor_id, "/w/a1-reused")

    version = server.store.get_agent(departing_id, project["id"])["version"]
    revoked = server.handle(Request(
        "DELETE", "/agents/{}/session-lease".format(departing_id), headers={
            "x-project-id": project["id"],
            "cookie": "tb_session=" + operator["session_token"],
            "x-csrf-token": operator["csrf_token"],
            "origin": "http://127.0.0.1:4319",
            "content-type": "application/json",
        }, body=json.dumps({"request_id": rid(), "expected_version": version,
                            "note": "seat retired"}).encode()))
    assert revoked.status == 200, revoked.body

    lease = successor.register(_runner_id(), successor_id, "/w/a1-reused")
    assert lease["allowlisted_worktree"] == "/w/a1-reused", lease


def test_an_operator_approved_worktree_is_not_blocked_by_another_agents_lease(
        server, project, operator):
    """The claimant scan is scoped to the unapproved path DELIBERATELY.

    When an operator HAS approved a directory, that approval is the authority
    and the registry already refused any conflicting enrolment. Running the
    claimant check there too would let a stale runner lease override an
    operator's decision, which inverts who is in charge.
    """
    squatter_id, squatter = _agent(server, project, operator, name="a1-squat")
    squatter.register(_runner_id(), squatter_id, "/w/a1-contested")

    owner_id, owner = _agent(server, project, operator, name="a1-owner",
                             worktree="/w/a1-contested")
    lease = owner.register(_runner_id(), owner_id, "/w/a1-contested")
    assert lease["allowlisted_worktree"] == "/w/a1-contested", lease


def test_an_expired_lease_releases_the_checkout_it_claimed(
        server, project, operator):
    """A runner-claimed directory is held while the runner is alive, not
    forever. The enrolment registry can rely on `agents.worktree` being an
    operator's standing decision; a lease is not that -- it is a claim by a
    process, and when the process stops renewing, the claim is over. Holding
    it past expiry would leak a directory on every crashed supervisor, with no
    operator lever to get it back short of revoking the agent.
    """
    gone_id, gone = _agent(server, project, operator, name="a1-crashed")
    gone.register(_runner_id(), gone_id, "/w/a1-expired")

    server.store.conn.execute(
        "UPDATE runner_leases SET expires_at = ? WHERE agent_id = ?",
        ("2000-01-01T00:00:00Z", gone_id))
    server.store.conn.commit()

    successor_id, successor = _agent(server, project, operator,
                                     name="a1-took-over")
    lease = successor.register(_runner_id(), successor_id, "/w/a1-expired")
    assert lease["allowlisted_worktree"] == "/w/a1-expired", lease


def test_the_audit_trail_distinguishes_a_runner_supplied_worktree(
        server, project, operator):
    """The provenance A1 asked for, in the only place it fits.

    The frozen `RunnerLease` has one worktree field and no room to say where
    the value came from; adding one would be a contract change, so the shape
    is untouched and the distinction goes to the audit trail. Without it a
    reader cannot tell a directory an operator APPROVED from one a runner
    asked for and was merely not refused -- and after A1 those two states have
    genuinely different meanings.
    """
    approved_id, approved = _agent(server, project, operator,
                                   name="a1-audit-approved",
                                   worktree="/w/a1-audit-ok")
    approved.register(_runner_id(), approved_id, "/w/a1-audit-ok")

    asked_id, asked = _agent(server, project, operator, name="a1-audit-asked")
    asked.register(_runner_id(), asked_id, "/w/a1-audit-asked")

    summaries = {
        row["subject_id"]: row["summary"]
        for row in server.store.audit_trail(project["id"], limit=500)
        if row["action"] == "runner_lease.acquire"
    }
    assert "operator-supplied" in summaries[approved_id], summaries
    assert "runner-supplied" in summaries[asked_id], summaries
