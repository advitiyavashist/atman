"""T-192: agent creation, enforced permission presets, worktree isolation.

These are ROUTE-level tests. They go through `POST /enrollments` with a real
operator session and `POST /runners/register` with a real agent token, because
the defect this ticket exists to close lives exactly at the seam between those
two credentials: the operator approves a profile, and the agent -- who is also
the runner -- used to be able to declare its own instead.

THE DEFECT, stated as the wire sees it. `register_runner` read
`permission_policy` and `allowlisted_worktree` straight out of the runner's own
request body, defaulting the policy to `prompt`, the broadest of the three. The
frozen contract's description of that field says "nothing here grants an agent
broader permissions than the operator configured"; the code did not hold it up.

Teeth for every test below were proven against the pre-fix source, not
asserted -- see the T-192 review notes for the exact pass/fail split.
"""

from __future__ import annotations

import json
import uuid

import pytest

from conftest import enroll, in_process_opener, rid  # noqa: F401

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
    """Exchange an enrollment code for a live agent token + runner client."""
    session_id = "ses_" + uuid.uuid4().hex[:8]
    exchanged = server.handle(Request("POST", "/sessions", headers={
        "x-project-id": project["id"], "content-type": "application/json",
    }, body=json.dumps({
        "request_id": rid(), "code": created.body["code"],
        "session_id": session_id,
        "runtime": {"adapter": "claude_code", "version": "2.1.0"},
    }).encode()))
    assert exchanged.status == 201, exchanged.body
    agent_id = exchanged.body["agent"]["id"]
    client = RunnerClient("http://board.test", project["id"],
                          exchanged.body["token"],
                          opener=in_process_opener(server), retries=0,
                          sleep=lambda _s: None)
    return agent_id, client


def _runner_id():
    return "rnr_" + uuid.uuid4().hex[:8]


# --------------------------------------------------------------- enforcement

def test_a_runner_cannot_grant_itself_a_broader_permission_policy(
        server, project, operator):
    """The ticket, in one case.

    The operator enrolled a `worker`, which is `allowlist`. The runner asks for
    `prompt`. Before T-192 the board wrote whatever the runner asked for onto
    the lease, so the agent chose its own permissions.
    """
    created = _enroll(server, project, operator, name="w1", role="worker",
                      worktree="/w/w1")
    agent_id, client = _connect(server, project, created)

    with pytest.raises(ApiError) as caught:
        client.register(_runner_id(), agent_id, "/w/w1",
                        permission_policy="prompt")
    assert caught.value.status == 403
    assert caught.value.code == "forbidden_scope"
    # The refusal has to NAME the mismatch: "diagnose inherited broad grants"
    # is not satisfied by a bare 403 the operator has to go and reverse-engineer.
    assert "allowlist" in str(caught.value) and "prompt" in str(caught.value)


def test_a_runner_that_declares_nothing_inherits_the_approved_policy(
        server, project, operator):
    """The default path, which is where the old escalation actually lived.

    Nobody had to ASK for `prompt` -- it was the default the client sent on
    every registration. Sending nothing must now mean "what the operator
    approved", not "the broadest value in the enum".
    """
    created = _enroll(server, project, operator, name="r1", role="reviewer")
    agent_id, client = _connect(server, project, created)

    lease = client.register(_runner_id(), agent_id, "/w/r1")
    assert lease["permission_policy"] == "deny_all", lease


def test_a_runner_may_narrow_its_own_policy_but_the_board_does_not_widen_it(
        server, project, operator):
    """min(requested, approved), not "approved wins".

    A supervisor that voluntarily drops to `deny_all` under an `allowlist`
    preset keeps `deny_all`. An earlier draft of this fix overrode it with the
    preset and thereby WIDENED a runner that had given up permissions -- the
    opposite of the job.
    """
    created = _enroll(server, project, operator, name="w2", role="worker",
                      worktree="/w/w2")
    agent_id, client = _connect(server, project, created)

    lease = client.register(_runner_id(), agent_id, "/w/w2",
                            permission_policy="deny_all")
    assert lease["permission_policy"] == "deny_all", lease


def test_an_unknown_role_gets_the_default_preset_and_never_prompt(
        server, project, operator):
    """Every role the live board actually uses -- backend, infra, console --
    is an "unknown" role to the preset table. They must all still work, and
    none of them may reach `prompt`."""
    for role in ("backend", "infra", "console", "verification"):
        created = _enroll(server, project, operator, name="a-" + role, role=role)
        agent_id, client = _connect(server, project, created)
        lease = client.register(_runner_id(), agent_id, "/w/" + role)
        assert lease["permission_policy"] == "allowlist", (role, lease)

        with pytest.raises(ApiError) as caught:
            client.register(_runner_id(), agent_id, "/w/" + role,
                            permission_policy="prompt")
        assert caught.value.status == 403, role


# ------------------------------------------------------- worktree confinement

def test_a_runner_cannot_allowlist_a_directory_outside_its_approved_worktree(
        server, project, operator):
    """The allowlisted worktree is the directory the runtime may touch, so
    choosing it is the same escalation wearing a different hat."""
    created = _enroll(server, project, operator, name="w3", role="worker",
                      worktree="/w/w3")
    agent_id, client = _connect(server, project, created)

    with pytest.raises(ApiError) as caught:
        client.register(_runner_id(), agent_id, "/w/somebody-else")
    assert caught.value.status == 403
    assert "/w/w3" in str(caught.value)


def test_a_runner_may_narrow_to_a_subdirectory_of_its_worktree(
        server, project, operator):
    created = _enroll(server, project, operator, name="w4", role="worker",
                      worktree="/w/w4")
    agent_id, client = _connect(server, project, created)

    lease = client.register(_runner_id(), agent_id, "/w/w4/src")
    assert lease["allowlisted_worktree"] == "/w/w4/src", lease


def test_a_runner_cannot_escape_by_asking_for_a_parent_of_its_worktree(
        server, project, operator):
    """Containment is DIRECTIONAL.

    A symmetric "do these paths overlap?" test passes `/` against an approved
    `/w/w5`, because a parent does overlap its child. Asking for the parent is
    asking for strictly more.
    """
    created = _enroll(server, project, operator, name="w5", role="worker",
                      worktree="/w/w5")
    agent_id, client = _connect(server, project, created)

    for escape in ("/", "/w", "/w/w5/../..", "/w/w5-other"):
        with pytest.raises(ApiError) as caught:
            client.register(_runner_id(), agent_id, escape)
        assert caught.value.status == 403, escape


# ------------------------------------------------- the live-checkout registry

def test_a_second_agent_cannot_enrol_into_an_occupied_checkout(
        server, project, operator):
    """T-192's 19:15Z acceptance case, refusal direction.

    Identity plus path containment cannot separate "a clone someone is working
    in" from "an ordinary project"; the board knows which checkouts are
    occupied, and that is the signal used.
    """
    _enroll(server, project, operator, name="occupant", worktree="/w/shared")

    refused = _enroll(server, project, operator, name="intruder",
                      worktree="/w/shared", expect=400)
    assert "occupant" in refused.body["error"]["message"]


def test_the_registry_refuses_a_parent_and_a_child_of_an_occupied_checkout(
        server, project, operator):
    """Occupation is containment in BOTH directions here -- unlike the runner
    check. An agent working in a parent checkout is working in every child of
    it, and an agent enrolling a parent takes in the occupied child."""
    _enroll(server, project, operator, name="occupant2", worktree="/w/proj/sub")

    _enroll(server, project, operator, name="takes-parent",
            worktree="/w/proj", expect=400)
    _enroll(server, project, operator, name="takes-child",
            worktree="/w/proj/sub/deeper", expect=400)


def test_an_unoccupied_clone_still_enrols(server, project, operator):
    """The converse the acceptance note insists on, and the reason a
    git-identity check was the wrong reach: an ordinary project -- including a
    clone of a board checkout that nobody is working in -- must still enrol."""
    _enroll(server, project, operator, name="occupant3", worktree="/w/live")

    _enroll(server, project, operator, name="clone-nobody-uses",
            worktree="/w/clones/live-copy")
    # `/home/agent` is a PLACEHOLDER_HOME_SEGMENTS handle. A literal operator
    # home here would fail test_no_test_file_names_a_real_operator_home, which
    # is T-210's anti-regression guard -- it caught this line.
    _enroll(server, project, operator, name="ordinary-project",
            worktree="/home/agent/my-app")


def test_a_shared_name_prefix_is_not_an_occupied_checkout(
        server, project, operator):
    """`/w/agent` and `/w/agent-2` are different directories. A raw string
    prefix test would call them the same and refuse a legitimate enrolment."""
    _enroll(server, project, operator, name="agent-a", worktree="/w/agent")
    _enroll(server, project, operator, name="agent-b", worktree="/w/agent-2")


def test_a_revoked_agent_releases_its_checkout(server, project, operator):
    """There is no delete-agent route, so if a revoked agent kept its checkout
    forever the operator would have no way to reuse the directory. Revocation
    is the contract-supported release."""
    created = _enroll(server, project, operator, name="departing",
                      worktree="/w/reused")
    # The route revokes a SESSION lease, so the agent has to have connected.
    agent_id, _client = _connect(server, project, created)

    _enroll(server, project, operator, name="too-early", worktree="/w/reused",
            expect=400)

    version = server.store.get_agent(agent_id, project["id"])["version"]
    revoked = server.handle(Request(
        "DELETE", "/agents/{}/session-lease".format(agent_id), headers={
            "x-project-id": project["id"],
            "cookie": "tb_session=" + operator["session_token"],
            "x-csrf-token": operator["csrf_token"],
            "origin": "http://127.0.0.1:4319",
            "content-type": "application/json",
        }, body=json.dumps({"request_id": rid(), "expected_version": version,
                            "note": "seat retired"}).encode()))
    assert revoked.status == 200, revoked.body

    _enroll(server, project, operator, name="successor", worktree="/w/reused")


# ------------------------------------------------------- creation invariants

def test_worktree_stays_optional_so_existing_enrolments_keep_working(
        server, project, operator):
    """`worktree` is OPTIONAL in the frozen CreateEnrollmentRequest.

    A draft of this ticket made it mandatory for the worker preset, which
    turned every previously-valid enrolment into a 400 -- a contract
    regression wearing a security hat. The policy is still enforced without
    it; only the directory confinement is unavailable, because the operator
    did not ask for any.
    """
    created = _enroll(server, project, operator, name="no-worktree")
    agent_id, client = _connect(server, project, created)

    lease = client.register(_runner_id(), agent_id, "/anywhere/at/all")
    assert lease["permission_policy"] == "allowlist", lease
    assert lease["allowlisted_worktree"] == "/anywhere/at/all"


def test_a_duplicate_agent_name_is_refused_and_creates_nothing(
        server, project, operator):
    _enroll(server, project, operator, name="only-one", worktree="/w/one")
    refused = _enroll(server, project, operator, name="only-one",
                      worktree="/w/two", expect=400)
    assert "already exists" in refused.body["error"]["message"]

    listed = server.handle(Request("GET", "/agents", headers={
        "x-project-id": project["id"],
        "cookie": "tb_session=" + operator["session_token"],
        "origin": "http://127.0.0.1:4319",
    }))
    names = [a["name"] for a in listed.body["items"]]
    assert names.count("only-one") == 1, names


def test_a_refused_enrolment_leaves_no_partial_agent_behind(
        server, project, operator):
    """Partial-start cleanup, checked where it actually matters.

    The registry check runs BEFORE create_agent precisely so a refusal has
    nothing to clean up. If it ran after, a rejected enrolment would strand an
    agent row holding a name the operator can then never reuse.
    """
    _enroll(server, project, operator, name="holder", worktree="/w/held")
    _enroll(server, project, operator, name="ghost", worktree="/w/held",
            expect=400)

    listed = server.handle(Request("GET", "/agents", headers={
        "x-project-id": project["id"],
        "cookie": "tb_session=" + operator["session_token"],
        "origin": "http://127.0.0.1:4319",
    }))
    names = [a["name"] for a in listed.body["items"]]
    assert "ghost" not in names, names
    # ...and the name is genuinely free afterwards, not merely absent.
    _enroll(server, project, operator, name="ghost", worktree="/w/elsewhere")


def test_a_retried_create_does_not_duplicate_the_agent_or_misreport_why(
        server, project, operator):
    """`POST /enrollments` is NOT idempotent on request_id, on main or here.

    The ticket asks for "idempotent create" and this test deliberately does
    NOT claim it was delivered. True replay would have to return the
    enrollment `code` a second time, and the frozen contract says that code is
    "returned exactly once here and never again" -- so idempotent replay of
    this route is a contract question, not an implementation one. It is
    reported as a residual in the T-192 review notes rather than quietly
    asserted here.

    What IS pinned, because it is what the new checks could have broken: a
    retry creates no second agent, and it fails as a DUPLICATE NAME. Without
    the ordering fix the worktree registry answered first and said "somebody
    else is working there" about the agent's own row -- confusing, and untrue.
    """
    shared = rid()
    body = json.dumps({"request_id": shared, "agent_name": "retried",
                       "role": "worker", "worktree": "/w/retried",
                       "connection_mode": "managed",
                       "max_active_tickets": 1}).encode()
    headers = {
        "x-project-id": project["id"],
        "cookie": "tb_session=" + operator["session_token"],
        "x-csrf-token": operator["csrf_token"],
        "origin": "http://127.0.0.1:4319",
        "content-type": "application/json",
    }
    first = server.handle(Request("POST", "/enrollments", headers=headers,
                                  body=body))
    assert first.status == 201, first.body
    second = server.handle(Request("POST", "/enrollments", headers=headers,
                                   body=body))
    assert second.status == 400, second.body
    assert "already exists" in second.body["error"]["message"], (
        "a retry must be reported as the name collision it is, not as a"
        " worktree occupied by the agent's own row: %r" % (second.body,))

    listed = server.handle(Request("GET", "/agents", headers={
        "x-project-id": project["id"],
        "cookie": "tb_session=" + operator["session_token"],
        "origin": "http://127.0.0.1:4319",
    }))
    names = [a["name"] for a in listed.body["items"]]
    assert names.count("retried") == 1, names
