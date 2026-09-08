"""The claim race: the acceptance bar T-179 is actually judged on.

`tickets next` claims atomically or the whole board is a lie -- two agents on
one ticket is the failure the ticket exists to prevent. These tests race real
processes and real threads against one SQLite file, not mocks.
"""

import multiprocessing
import sys
import threading
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[2] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ticket_board.storage import BoardStore, CapacityExhausted  # noqa: E402
from ticket_board.storage import TicketAlreadyClaimed, TicketVersionConflict  # noqa: E402
from ticket_board.storage.errors import BoardError  # noqa: E402


def _claim_in_process(args):
    """Claim from a separate OS process -- a genuinely concurrent writer.

    Non-BoardError exceptions are reported as "crash" rather than raised, so
    the test can assert on them. A loser that escapes as a raw
    `sqlite3.OperationalError` is a real defect: the contract promises callers
    a 409 they can act on, not a driver error.
    """
    db, project_id, ticket_id, agent_id, version = args
    store = BoardStore(db)
    try:
        store.claim_ticket(project_id, ticket_id, agent_id,
                           expected_version=version)
        return ("won", agent_id, None)
    except BoardError as exc:
        return ("lost", agent_id, exc.code)
    except Exception as exc:  # noqa: BLE001 - deliberately reported, not raised
        return ("crash", agent_id, "{}: {}".format(type(exc).__name__, exc))
    finally:
        store.close()


def test_eight_processes_race_one_ticket_exactly_one_wins(db_path):
    """Eight processes, one ticket, one winner. The headline requirement.

    Repeated over several rounds because the interesting failures here are
    probabilistic -- a single round can miss a scheduling window that a
    weaker locking strategy loses on maybe one run in three.
    """
    rounds = 5
    store = BoardStore(db_path)
    project = store.create_project("Race")
    # Capacity is deliberately generous: an agent that won an earlier round
    # would otherwise lose the next one with capacity_exhausted, which is
    # correct behaviour but not what this test is about. Capacity has its own
    # tests below; here every loss should be about the race itself.
    agents = [
        store.create_agent(project["id"], "agent-{}".format(i),
                           max_active_tickets=rounds)["id"]
        for i in range(8)
    ]
    for r in range(rounds):
        store.create_ticket(project["id"], "RACE-{}".format(r), "Contended")
    store.close()

    ctx = multiprocessing.get_context("spawn")
    for r in range(rounds):
        key = "RACE-{}".format(r)
        args = [(str(db_path), project["id"], key, a, 1) for a in agents]
        with ctx.Pool(8) as pool:
            results = pool.map(_claim_in_process, args)

        winners = [x for x in results if x[0] == "won"]
        losers = [x for x in results if x[0] == "lost"]
        crashes = [x for x in results if x[0] == "crash"]

        assert not crashes, (
            "a losing claim escaped as a driver error instead of a contract "
            "error: {}".format([c[2] for c in crashes])
        )
        assert len(winners) == 1, \
            "expected exactly one winner on {}, got {}".format(key, results)
        assert len(losers) == 7
        assert set(x[2] for x in losers) <= {
            "ticket_already_claimed", "ticket_version_conflict",
        }, "unexpected loss reasons: {}".format(sorted(set(x[2] for x in losers)))

    store = BoardStore(db_path)
    try:
        for r in range(rounds):
            key = "RACE-{}".format(r)
            final = store.get_ticket(project["id"], key)
            assert final["state"] == "claimed"
            assert final["owner"] in agents
            # One claim happened, so exactly one version bump.
            assert final["version"] == 2, \
                "{} took {} writes, expected 1".format(key, final["version"] - 1)
            claims = [e for e in store.audit_trail(project["id"], limit=10000)
                      if e["action"] == "ticket.claim" and e["subject_id"] == key]
            assert len(claims) == 1, \
                "audit recorded {} claims for {}".format(len(claims), key)
    finally:
        store.close()


def test_eight_threads_race_one_ticket_exactly_one_wins(store, project):
    """Same race in-process: shared connection, no cross-process lock to help."""
    agents = [store.create_agent(project["id"], "t-{}".format(i))["id"]
              for i in range(8)]
    ticket = store.create_ticket(project["id"], "RACE-2", "Contended")
    outcomes = []
    lock = threading.Lock()
    barrier = threading.Barrier(len(agents))

    def attempt(agent_id):
        barrier.wait()
        try:
            store.claim_ticket(project["id"], "RACE-2", agent_id,
                               expected_version=ticket["version"])
            with lock:
                outcomes.append(("won", agent_id))
        except BoardError as exc:
            with lock:
                outcomes.append(("lost", exc.code))

    threads = [threading.Thread(target=attempt, args=(a,)) for a in agents]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len([o for o in outcomes if o[0] == "won"]) == 1, outcomes
    assert store.get_ticket(project["id"], "RACE-2")["version"] == \
        ticket["version"] + 1


def test_stale_version_is_rejected_with_both_versions(store, project, agent):
    """A stale claim is refused, and the 409 says what to re-read."""
    ticket = store.create_ticket(project["id"], "STALE-1", "Moves underneath you")
    stale = ticket["version"]
    store.set_dependencies(project["id"], "STALE-1", [],
                           expected_version=stale)  # bumps to stale+1

    with pytest.raises(TicketVersionConflict) as caught:
        store.claim_ticket(project["id"], "STALE-1", agent["id"],
                           expected_version=stale)

    err = caught.value
    assert err.code == "ticket_version_conflict"
    assert err.status == 409
    assert err.details["expected_version"] == stale
    assert err.details["actual_version"] == stale + 1
    # The caller can re-read in one round trip, which is the whole point.
    assert store.get_ticket(project["id"], "STALE-1")["version"] == \
        err.details["actual_version"]


def test_second_claim_of_a_claimed_ticket_is_already_claimed(store, project):
    a = store.create_agent(project["id"], "first")
    b = store.create_agent(project["id"], "second")
    ticket = store.create_ticket(project["id"], "TAKEN-1", "One at a time")
    claimed = store.claim_ticket(project["id"], "TAKEN-1", a["id"],
                                 expected_version=ticket["version"])
    with pytest.raises(TicketAlreadyClaimed) as caught:
        store.claim_ticket(project["id"], "TAKEN-1", b["id"],
                           expected_version=claimed["version"])
    assert caught.value.details["owner"] == a["id"]


def test_capacity_is_enforced_at_claim_time(store, project):
    """An agent at capacity cannot take a second ticket."""
    a = store.create_agent(project["id"], "solo", max_active_tickets=1)
    first = store.create_ticket(project["id"], "CAP-1", "First")
    second = store.create_ticket(project["id"], "CAP-2", "Second")
    store.claim_ticket(project["id"], "CAP-1", a["id"],
                       expected_version=first["version"])
    with pytest.raises(CapacityExhausted) as caught:
        store.claim_ticket(project["id"], "CAP-2", a["id"],
                           expected_version=second["version"])
    assert caught.value.details == {
        "agent_id": a["id"], "active_tickets": 1, "max_active_tickets": 1,
    }
    # The refused ticket is untouched -- no partial write.
    assert store.get_ticket(project["id"], "CAP-2")["state"] == "open"
    assert store.get_ticket(project["id"], "CAP-2")["version"] == second["version"]


def test_capacity_two_allows_two_then_refuses_the_third(store, project):
    a = store.create_agent(project["id"], "double", max_active_tickets=2)
    for n in (1, 2, 3):
        store.create_ticket(project["id"], "MULTI-{}".format(n), "t{}".format(n))
    for n in (1, 2):
        t = store.get_ticket(project["id"], "MULTI-{}".format(n))
        store.claim_ticket(project["id"], t["id"], a["id"],
                           expected_version=t["version"])
    assert store.get_agent(a["id"])["capacity"]["active_tickets"] == 2
    third = store.get_ticket(project["id"], "MULTI-3")
    with pytest.raises(CapacityExhausted):
        store.claim_ticket(project["id"], "MULTI-3", a["id"],
                           expected_version=third["version"])
