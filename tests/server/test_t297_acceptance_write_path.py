"""T-297: the acceptance write path T-224 assumed existed.

T-224 made `acceptance` optional at `POST /tickets` -- forced, not preferred:
T-213's legacy import has to be lossless and no ticket on the real board
carries the field. The amendment moved ticket quality from a create-time
schema minimum to enforcement "later", at the review gate, and that gate is
still live (`store.transition` and `store.submit_review` both refuse `review`
on an empty list). What the amendment did not notice is that "later" had no
door: no route could write `acceptance` after create, so a ticket that arrived
without criteria could never reach review through the API at all.

That is not a hypothetical shape. `storage/legacy.py:_import_ticket` writes
`acceptance` as the SQL literal `'[]'` -- it never consults the legacy item --
so it is *every* imported ticket. On the board this file was written against,
all 217 tickets lack the field; 29 of them are not `done` and would import
straight into the dead end.

The tests below drive the real loop rather than asserting on the new route in
isolation, because "the route exists" was never in doubt -- "the ticket can
actually get out again" is the claim.
"""

import json
import shutil
from pathlib import Path

import pytest

from api_client import Client, rid
from ticket_board.storage import MissingAcceptanceCriteria
from ticket_board.storage.legacy import import_legacy_board

SAMPLE_BOARD = Path(__file__).resolve().parents[1] / "data" / "legacy_board"

CRITERIA = [
    {"text": "The imported ticket can be submitted for review."},
    {"text": "The review gate still refuses an empty acceptance list."},
]


def _operator_for(server, project_id):
    """An operator session for the project the importer created.

    The `operator` fixture is bootstrapped against the Demo project; the
    import makes its own, and a credential is scoped to one project by design
    (T-240), so reusing it is a 403 rather than a shortcut.
    """
    session = server.bootstrap_operator(project_id)
    return Client(server, project_id=project_id,
                  cookie=session["session_token"],
                  csrf=session["csrf_token"])


def _acceptance_texts(ticket):
    return [item["text"] for item in ticket["acceptance"]]


# ------------------------------------------------------- the dead end itself

def test_a_ticket_without_criteria_cannot_reach_review_before_the_fix(
        server, project, operator):
    """The defect, stated as behaviour: created empty, refused at review.

    Created through the store because `POST /tickets` on this base still
    rejects `acceptance: []` at `validate.acceptance` -- that is T-288's gap,
    filed separately and not this ticket's to fix. The ticket that results is
    byte-identical either way: an `acceptance` column holding `[]`.
    """
    created = server.store.create_ticket(
        project["id"], "DEAD-1", "Imported with no criteria", acceptance=[],
    )
    assert created["acceptance"] == []

    claimed = server.store.claim_ticket(
        project["id"], "DEAD-1",
        server.store.create_agent(project["id"], "worker", role="backend")["id"],
        expected_version=created["version"], enforce_dependencies=False,
    )

    submitted = operator.post("/tickets/DEAD-1/reviews", {
        "request_id": rid(),
        "expected_version": claimed["version"],
        "evidence": {"repository": "demo", "branch": "f", "sha": "a" * 40},
    })
    assert submitted.status == 422, submitted.json()
    assert submitted.json()["error"]["code"] == "missing_acceptance_criteria"


# --------------------------------------------------------- the loop, closed

def test_the_full_loop_empty_at_create_then_review_reachable(
        server, project, operator):
    """create empty -> 422 at review -> set criteria -> review reachable.

    The whole point of the ticket in one test. Every step after creation goes
    through the HTTP surface, because "a client can fix this" is the claim
    being made and the store is not a client.
    """
    created = server.store.create_ticket(
        project["id"], "LOOP-1", "No criteria at create", acceptance=[],
    )
    agent = server.store.create_agent(project["id"], "worker", role="backend")
    claimed = server.store.claim_ticket(
        project["id"], "LOOP-1", agent["id"],
        expected_version=created["version"], enforce_dependencies=False,
    )

    # 1. refused, because the gate T-224 moved enforcement to is doing its job
    blocked = operator.post("/tickets/LOOP-1/reviews", {
        "request_id": rid(), "expected_version": claimed["version"],
        "evidence": {"repository": "demo", "branch": "f", "sha": "a" * 40},
    })
    assert blocked.status == 422
    assert blocked.json()["error"]["code"] == "missing_acceptance_criteria"

    # 2. the door this ticket adds
    filled = operator.post("/tickets/LOOP-1/acceptance", {
        "request_id": rid(), "expected_version": claimed["version"],
        "acceptance": CRITERIA,
    })
    assert filled.status == 200, filled.json()
    assert _acceptance_texts(filled.json()) == [c["text"] for c in CRITERIA]
    # stored in the checkable shape, same as a ticket created with criteria
    assert filled.json()["acceptance"][0]["checked"] is False
    assert filled.json()["version"] == claimed["version"] + 1

    # 3. and now the same submission the gate refused in step 1 goes through
    submitted = operator.post("/tickets/LOOP-1/reviews", {
        "request_id": rid(), "expected_version": filled.json()["version"],
        "evidence": {"repository": "demo", "branch": "f", "sha": "a" * 40},
    })
    assert submitted.status == 201, submitted.json()
    assert server.store.get_ticket(project["id"], "LOOP-1")["state"] == "review"


def test_the_loop_closes_for_a_real_imported_legacy_ticket(
        server, project, tmp_path):
    """The same loop on a ticket the importer actually produced.

    The synthetic case above proves the route works. This one proves it works
    on the shape that motivated the ticket, without trusting my own reading of
    what the importer writes.
    """
    sample = tmp_path / "legacy"
    shutil.copytree(SAMPLE_BOARD, sample)
    import_legacy_board(server.store, sample, project_name="Legacy")

    row = server.store.conn.execute(
        "SELECT id, project_id, version, acceptance FROM tickets"
        " WHERE state = 'claimed' LIMIT 1"
    ).fetchone()
    assert json.loads(row["acceptance"]) == [], "importer no longer empties it"

    legacy = _operator_for(server, row["project_id"])
    path = "/tickets/{}".format(row["id"])

    refused = legacy.post(path + "/reviews", {
        "request_id": rid(), "expected_version": row["version"],
        "evidence": {"repository": "demo", "branch": "f", "sha": "a" * 40},
    })
    assert refused.status == 422
    assert refused.json()["error"]["code"] == "missing_acceptance_criteria"

    filled = legacy.post(path + "/acceptance", {
        "request_id": rid(), "expected_version": row["version"],
        "acceptance": CRITERIA,
    })
    assert filled.status == 200, filled.json()

    submitted = legacy.post(path + "/reviews", {
        "request_id": rid(), "expected_version": filled.json()["version"],
        "evidence": {"repository": "demo", "branch": "f", "sha": "a" * 40},
    })
    assert submitted.status == 201, submitted.json()


def test_a_review_rejection_no_longer_strands_an_imported_ticket(
        server, project, tmp_path):
    """The case that widens this past open/claimed/blocked.

    A legacy ticket whose status was already `review` is INSERTed straight
    into that state -- the importer does not transition, so the gate never
    sees it. It looks safe. It is safe exactly until the review is rejected:
    that returns the ticket to `claimed`, and re-submitting is then refused
    forever. Before this ticket there was no way back.
    """
    sample = tmp_path / "legacy"
    shutil.copytree(SAMPLE_BOARD, sample)
    import_legacy_board(server.store, sample, project_name="Legacy")

    row = server.store.conn.execute(
        "SELECT id, project_id, version FROM tickets WHERE state = 'review' LIMIT 1"
    ).fetchone()
    pid, tid = row["project_id"], row["id"]

    # a rejected review sends it back to claimed
    back = server.store.transition(pid, tid, "claimed",
                                   expected_version=row["version"],
                                   reason="changes requested")
    assert back["state"] == "claimed"

    legacy = _operator_for(server, pid)
    filled = legacy.post("/tickets/{}/acceptance".format(tid), {
        "request_id": rid(), "expected_version": back["version"],
        "acceptance": CRITERIA,
    })
    assert filled.status == 200, filled.json()

    submitted = legacy.post("/tickets/{}/reviews".format(tid), {
        "request_id": rid(), "expected_version": filled.json()["version"],
        "evidence": {"repository": "demo", "branch": "f", "sha": "a" * 40},
    })
    assert submitted.status == 201, submitted.json()


# ----------------------------------------------------- what it must NOT do

def test_the_route_refuses_an_empty_list(server, project, operator):
    """Re-arming the dead end would be the worst possible success.

    T-224's `[]` default is right at create and wrong here: this route exists
    to supply criteria, so an empty list is a caller mistake, not a lossless
    import.
    """
    created = server.store.create_ticket(
        project["id"], "EMPTY-1", "t", acceptance=[{"text": "a"}])
    response = operator.post("/tickets/EMPTY-1/acceptance", {
        "request_id": rid(), "expected_version": created["version"],
        "acceptance": [],
    })
    assert response.status == 400, response.json()
    assert response.json()["error"]["code"] == "malformed_request"
    # and the existing criteria are untouched
    assert _acceptance_texts(
        server.store.get_ticket(project["id"], "EMPTY-1")) == ["a"]


def test_the_empty_list_refusal_is_this_route_s_own(server, project, operator,
                                                    monkeypatch):
    """The refusal must not be on loan from `validate.acceptance`.

    Found by mutation, and it is the one that would have shipped silently.
    Deleting this route's own empty-list check leaves every other test in this
    file green, because `validate.acceptance` on this base still raises on
    `[]` -- so the guard reads as redundant. It is not: T-288 makes
    `validate.acceptance` return `[]` for an absent or empty list, which is
    correct there (T-224's create-time default) and would, at that moment,
    turn this route into one that silently re-arms the dead end it exists to
    close. This test pins the behaviour to the layer that has to own it, so
    the guard is load-bearing before T-288 lands rather than after something
    breaks.
    """
    monkeypatch.setattr("ticket_board.server.validate.acceptance",
                        lambda body: [])
    created = server.store.create_ticket(
        project["id"], "LOAN-1", "t", acceptance=[{"text": "a"}])
    response = operator.post("/tickets/LOAN-1/acceptance", {
        "request_id": rid(), "expected_version": created["version"],
        "acceptance": [{"text": "ignored -- validate is stubbed"}],
    })
    assert response.status == 400, response.json()
    assert response.json()["error"]["code"] == "malformed_request"
    assert _acceptance_texts(
        server.store.get_ticket(project["id"], "LOAN-1")) == ["a"]


def test_it_does_not_relax_the_review_gate(server, project, operator):
    """Ruling (b), the rejected option, must stay rejected.

    If this ever passes by returning 201 the fix has been "simplified" into
    the thing the planner refused: deleting the guarantee instead of
    relocating it.
    """
    created = server.store.create_ticket(
        project["id"], "GATE-1", "still guarded", acceptance=[])
    agent = server.store.create_agent(project["id"], "w", role="backend")
    claimed = server.store.claim_ticket(
        project["id"], "GATE-1", agent["id"],
        expected_version=created["version"], enforce_dependencies=False)

    refused = operator.post("/tickets/GATE-1/reviews", {
        "request_id": rid(), "expected_version": claimed["version"],
        "evidence": {"repository": "demo", "branch": "f", "sha": "a" * 40},
    })
    assert refused.status == 422
    assert refused.json()["error"]["code"] == "missing_acceptance_criteria"


def test_the_transition_gate_is_also_still_guarded(server, project):
    """Ruling (b) named `store.transition`, and `submit_review` is a different
    door -- a mutation that deletes the transition guard alone leaves every
    HTTP-level test in this file green, so this one goes at the store.

    Found by mutation: removing the `to_state == "review"` guard from
    `transition` passed all eleven tests written before this one.
    """
    created = server.store.create_ticket(
        project["id"], "TRN-1", "no criteria", acceptance=[])
    agent = server.store.create_agent(project["id"], "w", role="backend")
    claimed = server.store.claim_ticket(
        project["id"], "TRN-1", agent["id"],
        expected_version=created["version"], enforce_dependencies=False)

    with pytest.raises(MissingAcceptanceCriteria):
        server.store.transition(project["id"], "TRN-1", "review",
                                expected_version=claimed["version"])

    # and it opens once the criteria are there -- same guard, not a wall
    filled = server.store.set_acceptance(
        project["id"], "TRN-1", [{"text": "c", "checked": False,
                                  "checked_by": None, "checked_at": None}],
        expected_version=claimed["version"])
    moved = server.store.transition(project["id"], "TRN-1", "review",
                                    expected_version=filled["version"])
    assert moved["state"] == "review"


def test_a_done_ticket_is_not_editable(server, project, operator):
    """`done` is terminal in ALLOWED_TRANSITIONS; its criteria are history."""
    created = server.store.create_ticket(
        project["id"], "DONE-1", "t", acceptance=[{"text": "a"}])
    done = server.store.transition(project["id"], "DONE-1", "done",
                                   expected_version=created["version"])
    response = operator.post("/tickets/DONE-1/acceptance", {
        "request_id": rid(), "expected_version": done["version"],
        "acceptance": CRITERIA,
    })
    assert response.status == 409, response.json()
    assert response.json()["error"]["code"] == "acceptance_not_editable"


def test_a_ticket_under_review_is_not_editable(server, project, operator):
    """Moving the bar under an in-flight reviewer is the failure the gate
    exists to prevent -- so the door is shut while a review is open."""
    created = server.store.create_ticket(
        project["id"], "REV-9", "t", acceptance=[{"text": "a"}])
    agent = server.store.create_agent(project["id"], "w", role="backend")
    claimed = server.store.claim_ticket(
        project["id"], "REV-9", agent["id"],
        expected_version=created["version"], enforce_dependencies=False)
    server.store.submit_review(
        project["id"], "REV-9", {"type": "agent", "id": agent["id"],
                                 "display_name": "w"},
        {"repository": "demo", "branch": "f", "sha": "a" * 40},
        expected_version=claimed["version"])

    current = server.store.get_ticket(project["id"], "REV-9")
    response = operator.post("/tickets/REV-9/acceptance", {
        "request_id": rid(), "expected_version": current["version"],
        "acceptance": CRITERIA,
    })
    assert response.status == 409, response.json()
    assert response.json()["error"]["code"] == "acceptance_not_editable"


# ------------------------------------------------ the envelope, like its peers

def test_a_stale_expected_version_conflicts(server, project, operator):
    created = server.store.create_ticket(
        project["id"], "VER-1", "t", acceptance=[{"text": "a"}])
    response = operator.post("/tickets/VER-1/acceptance", {
        "request_id": rid(), "expected_version": created["version"] + 5,
        "acceptance": CRITERIA,
    })
    assert response.status == 409
    assert response.json()["error"]["code"] == "ticket_version_conflict"


def test_a_replayed_request_id_returns_the_first_result(server, project,
                                                        operator):
    """Same envelope as every other mutation: a retry is not a second write."""
    created = server.store.create_ticket(
        project["id"], "RPL-1", "t", acceptance=[{"text": "a"}])
    key = rid()
    body = {"request_id": key, "expected_version": created["version"],
            "acceptance": CRITERIA}
    first = operator.post("/tickets/RPL-1/acceptance", dict(body))
    assert first.status == 200, first.json()
    second = operator.post("/tickets/RPL-1/acceptance", dict(body))
    assert second.status == 200, second.json()
    assert second.json()["version"] == first.json()["version"]


def test_an_agent_cannot_set_criteria_on_another_agents_ticket(
        server, project, operator, enrolled):
    """Owner-or-operator, matching setTicketBlocked."""
    created = server.store.create_ticket(
        project["id"], "OWN-1", "t", acceptance=[{"text": "a"}])
    other = server.store.create_agent(project["id"], "someone-else", role="backend")
    claimed = server.store.claim_ticket(
        project["id"], "OWN-1", other["id"],
        expected_version=created["version"], enforce_dependencies=False)

    response = enrolled["client"].post("/tickets/OWN-1/acceptance", {
        "request_id": rid(), "expected_version": claimed["version"],
        "acceptance": CRITERIA,
    })
    assert response.status == 403, response.json()
