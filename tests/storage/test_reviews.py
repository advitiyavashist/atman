"""Review evidence pinning -- T-178 freeze decision 3."""

import pytest

from ticket_board.storage import InvalidReviewEvidence, MissingAcceptanceCriteria

SHA = "a" * 40
OTHER_SHA = "b" * 40
REVIEWER = {"type": "human", "id": "mem_operator", "display_name": "Operator"}
AUTHOR = {"type": "agent", "id": "agt_backend1", "display_name": "backend-1"}


def _ticket_ready_for_review(store, project, key="REV-1"):
    store.create_ticket(project["id"], key, "Reviewable",
                        acceptance=[{"text": "It works", "checked": False,
                                     "checked_by": None, "checked_at": None}])
    return store.get_ticket(project["id"], key)


def test_accept_requires_the_pinned_sha(store, project):
    """A moving branch tip cannot be substituted for reviewed evidence."""
    _ticket_ready_for_review(store, project)
    review = store.submit_review(
        project["id"], "REV-1", AUTHOR,
        {"repository": "demo", "branch": "feature", "sha": SHA},
    )
    with pytest.raises(InvalidReviewEvidence) as caught:
        store.decide_review(project["id"], review["id"], "accepted", REVIEWER,
                            evidence_sha=OTHER_SHA)
    assert caught.value.status == 422
    assert caught.value.details["submitted_sha"] == SHA
    assert caught.value.details["presented_sha"] == OTHER_SHA
    assert store.get_ticket(project["id"], "REV-1")["state"] == "review", \
        "a refused acceptance must not advance the ticket"


def test_accept_with_the_pinned_sha_marks_the_ticket_done(store, project):
    _ticket_ready_for_review(store, project, "REV-2")
    review = store.submit_review(
        project["id"], "REV-2", AUTHOR,
        {"branch": "feature", "sha": SHA,
         "checks": [{"name": "pytest", "status": "passed"}]},
    )
    decided = store.decide_review(project["id"], review["id"], "accepted",
                                  REVIEWER, evidence_sha=SHA)
    assert decided["state"] == "accepted"
    assert store.get_ticket(project["id"], "REV-2")["state"] == "done"


def test_accept_refuses_when_a_check_failed(store, project):
    """'Done requires acceptance' is meaningless if it accepts a red build."""
    _ticket_ready_for_review(store, project, "REV-3")
    review = store.submit_review(
        project["id"], "REV-3", AUTHOR,
        {"branch": "feature", "sha": SHA,
         "checks": [{"name": "pytest", "status": "failed"}]},
    )
    with pytest.raises(InvalidReviewEvidence) as caught:
        store.decide_review(project["id"], review["id"], "accepted", REVIEWER,
                            evidence_sha=SHA)
    assert caught.value.details["failed_checks"] == ["pytest"]
    assert store.get_ticket(project["id"], "REV-3")["state"] == "review"


def test_reject_returns_the_ticket_to_the_author(store, project):
    _ticket_ready_for_review(store, project, "REV-4")
    review = store.submit_review(project["id"], "REV-4", AUTHOR,
                                 {"branch": "f", "sha": SHA})
    store.decide_review(project["id"], review["id"], "rejected", REVIEWER,
                        evidence_sha=SHA, notes="needs tests")
    assert store.get_ticket(project["id"], "REV-4")["state"] == "claimed"


def test_review_needs_acceptance_criteria(store, project):
    store.create_ticket(project["id"], "REV-5", "No criteria")
    with pytest.raises(MissingAcceptanceCriteria):
        store.submit_review(project["id"], "REV-5", AUTHOR,
                            {"branch": "f", "sha": SHA})


def test_evidence_without_a_sha_is_refused(store, project):
    _ticket_ready_for_review(store, project, "REV-6")
    with pytest.raises(InvalidReviewEvidence):
        store.submit_review(project["id"], "REV-6", AUTHOR, {"branch": "f"})


def test_a_decided_review_cannot_be_decided_again(store, project):
    from ticket_board.storage import InvalidStateTransition
    _ticket_ready_for_review(store, project, "REV-7")
    review = store.submit_review(project["id"], "REV-7", AUTHOR,
                                 {"branch": "f", "sha": SHA})
    store.decide_review(project["id"], review["id"], "accepted", REVIEWER,
                        evidence_sha=SHA)
    with pytest.raises(InvalidStateTransition):
        store.decide_review(project["id"], review["id"], "rejected", REVIEWER,
                            evidence_sha=SHA)
