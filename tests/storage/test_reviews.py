"""Review evidence pinning -- T-178 freeze decision 3."""

import pytest

from ticket_board.storage import InvalidReviewEvidence, MissingAcceptanceCriteria

SHA = "a" * 40
OTHER_SHA = "b" * 40
REVIEWER = {"type": "human", "id": "mem_operator", "display_name": "Operator"}
AUTHOR = {"type": "agent", "id": "agt_backend1", "display_name": "backend-1"}
REQUEST_ID = "99999999-8888-7777-6666-555555555555"


def _ticket_ready_for_review(store, project, agent, key="REV-1"):
    """A claimed ticket with acceptance criteria -- submit_review now checks
    the "claimed" state itself (T-255, moved down from the app.py handler),
    so the fixture must actually claim it, not just create it."""
    created = store.create_ticket(project["id"], key, "Reviewable",
                        acceptance=[{"text": "It works", "checked": False,
                                     "checked_by": None, "checked_at": None}])
    return store.claim_ticket(project["id"], key, agent["id"],
                              expected_version=created["version"])


def test_accept_requires_the_pinned_sha(store, project, agent):
    """A moving branch tip cannot be substituted for reviewed evidence."""
    ticket = _ticket_ready_for_review(store, project, agent)
    review = store.submit_review(
        project["id"], "REV-1", AUTHOR,
        {"repository": "demo", "branch": "feature", "sha": SHA},
        expected_version=ticket["version"],
    )
    with pytest.raises(InvalidReviewEvidence) as caught:
        store.decide_review(
            project["id"], review["id"], "accepted", REVIEWER,
            evidence_sha=OTHER_SHA,
            expected_version=store.get_ticket(project["id"], "REV-1")["version"],
        )
    assert caught.value.status == 422
    assert caught.value.details["submitted_sha"] == SHA
    assert caught.value.details["presented_sha"] == OTHER_SHA
    assert store.get_ticket(project["id"], "REV-1")["state"] == "review", \
        "a refused acceptance must not advance the ticket"


def test_accept_with_the_pinned_sha_marks_the_ticket_done(store, project, agent):
    ticket = _ticket_ready_for_review(store, project, agent, "REV-2")
    review = store.submit_review(
        project["id"], "REV-2", AUTHOR,
        {"branch": "feature", "sha": SHA,
         "checks": [{"name": "pytest", "status": "passed"}]},
        expected_version=ticket["version"],
    )
    decided = store.decide_review(
        project["id"], review["id"], "accepted", REVIEWER, evidence_sha=SHA,
        expected_version=store.get_ticket(project["id"], "REV-2")["version"],
    )
    assert decided["state"] == "accepted"
    assert store.get_ticket(project["id"], "REV-2")["state"] == "done"


def test_accept_refuses_when_a_check_failed(store, project, agent):
    """'Done requires acceptance' is meaningless if it accepts a red build."""
    ticket = _ticket_ready_for_review(store, project, agent, "REV-3")
    review = store.submit_review(
        project["id"], "REV-3", AUTHOR,
        {"branch": "feature", "sha": SHA,
         "checks": [{"name": "pytest", "status": "failed"}]},
        expected_version=ticket["version"],
    )
    with pytest.raises(InvalidReviewEvidence) as caught:
        store.decide_review(
            project["id"], review["id"], "accepted", REVIEWER,
            evidence_sha=SHA,
            expected_version=store.get_ticket(project["id"], "REV-3")["version"],
        )
    assert caught.value.details["failed_checks"] == ["pytest"]
    assert store.get_ticket(project["id"], "REV-3")["state"] == "review"


def test_reject_returns_the_ticket_to_the_author(store, project, agent):
    ticket = _ticket_ready_for_review(store, project, agent, "REV-4")
    review = store.submit_review(
        project["id"], "REV-4", AUTHOR, {"branch": "f", "sha": SHA},
        expected_version=ticket["version"],
    )
    store.decide_review(
        project["id"], review["id"], "rejected", REVIEWER,
        evidence_sha=SHA, notes="needs tests",
        expected_version=store.get_ticket(project["id"], "REV-4")["version"],
    )
    assert store.get_ticket(project["id"], "REV-4")["state"] == "claimed"


def test_review_needs_acceptance_criteria(store, project, agent):
    created = store.create_ticket(project["id"], "REV-5", "No criteria")
    ticket = store.claim_ticket(project["id"], "REV-5", agent["id"],
                                expected_version=created["version"])
    with pytest.raises(MissingAcceptanceCriteria):
        store.submit_review(
            project["id"], "REV-5", AUTHOR, {"branch": "f", "sha": SHA},
            expected_version=ticket["version"],
        )


def test_evidence_without_a_sha_is_refused(store, project, agent):
    ticket = _ticket_ready_for_review(store, project, agent, "REV-6")
    with pytest.raises(InvalidReviewEvidence):
        store.submit_review(
            project["id"], "REV-6", AUTHOR, {"branch": "f"},
            expected_version=ticket["version"],
        )


def test_a_decided_review_cannot_be_decided_again(store, project, agent):
    from ticket_board.storage import InvalidStateTransition
    ticket = _ticket_ready_for_review(store, project, agent, "REV-7")
    review = store.submit_review(
        project["id"], "REV-7", AUTHOR, {"branch": "f", "sha": SHA},
        expected_version=ticket["version"],
    )
    store.decide_review(
        project["id"], review["id"], "accepted", REVIEWER, evidence_sha=SHA,
        expected_version=store.get_ticket(project["id"], "REV-7")["version"],
    )
    with pytest.raises(InvalidStateTransition):
        store.decide_review(
            project["id"], review["id"], "rejected", REVIEWER, evidence_sha=SHA,
            expected_version=store.get_ticket(project["id"], "REV-7")["version"],
        )


def test_submit_review_replays_a_byte_identical_retry_past_a_stale_version(
    store, project, agent
):
    """T-255: expected_version moved into the store, after `_replay`, so a
    byte-identical retry must return the original response even once the
    ticket's current version has moved past what the retry still carries --
    not die as TicketVersionConflict the way the handler-level check did."""
    ticket = _ticket_ready_for_review(store, project, agent, "REV-8")
    evidence = {"branch": "feature", "sha": SHA}
    first = store.submit_review(
        project["id"], "REV-8", AUTHOR, evidence,
        expected_version=ticket["version"], request_id=REQUEST_ID,
    )
    assert store.get_ticket(project["id"], "REV-8")["version"] == ticket["version"] + 1

    replay = store.submit_review(
        project["id"], "REV-8", AUTHOR, evidence,
        expected_version=ticket["version"], request_id=REQUEST_ID,
    )
    assert replay == first
    assert store.get_ticket(project["id"], "REV-8")["version"] == ticket["version"] + 1


def test_decide_review_replays_a_byte_identical_retry_past_a_stale_version(
    store, project, agent
):
    """Same shape one level later: deciding also bumps the ticket's version,
    so a retry must replay before the store re-checks expected_version
    against the now-advanced ticket."""
    ticket = _ticket_ready_for_review(store, project, agent, "REV-9")
    review = store.submit_review(
        project["id"], "REV-9", AUTHOR, {"branch": "f", "sha": SHA},
        expected_version=ticket["version"],
    )
    submitted_version = store.get_ticket(project["id"], "REV-9")["version"]
    first = store.decide_review(
        project["id"], review["id"], "accepted", REVIEWER, evidence_sha=SHA,
        expected_version=submitted_version, request_id=REQUEST_ID,
    )
    assert store.get_ticket(project["id"], "REV-9")["version"] == submitted_version + 1

    replay = store.decide_review(
        project["id"], review["id"], "accepted", REVIEWER, evidence_sha=SHA,
        expected_version=submitted_version, request_id=REQUEST_ID,
    )
    assert replay == first
    assert store.get_ticket(project["id"], "REV-9")["version"] == submitted_version + 1
