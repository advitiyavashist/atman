"""Dependency cycles, derived blocking, and unmet dependencies."""

import pytest
import sqlite3

from ticket_board.storage import DependencyCycle, DependencyUnmet


def test_dependency_blocked_is_derived_not_stored(store, project):
    """T-178 freeze decision 2: it is a computed flag, not a sixth state."""
    store.create_ticket(project["id"], "DEP-1", "Upstream")
    store.create_ticket(project["id"], "DEP-2", "Downstream",
                        dependencies=["DEP-1"])

    downstream = store.get_ticket(project["id"], "DEP-2")
    assert downstream["dependency_blocked"] is True
    assert downstream["state"] == "open", "blocking must not become a state"

    # No column stores it -- the flag cannot drift out of sync with the deps.
    columns = {r[1] for r in store.conn.execute("PRAGMA table_info(tickets)")}
    assert "dependency_blocked" not in columns

    upstream = store.get_ticket(project["id"], "DEP-1")
    store.transition(project["id"], "DEP-1", "done",
                     expected_version=upstream["version"])
    assert store.get_ticket(project["id"], "DEP-2")["dependency_blocked"] is False


def test_dependency_state_is_not_persistable(store, project):
    """The CHECK constraint refuses it even by direct SQL."""
    store.create_ticket(project["id"], "DEP-3", "t")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "UPDATE tickets SET state = 'dependency_blocked' WHERE id = 'DEP-3'"
        )


def test_direct_cycle_is_422_and_creates_nothing(store, project):
    """A refused cycle must leave no trace -- the contract is explicit."""
    store.create_ticket(project["id"], "CYC-1", "A")
    store.create_ticket(project["id"], "CYC-2", "B", dependencies=["CYC-1"])
    before = store.get_ticket(project["id"], "CYC-1")

    with pytest.raises(DependencyCycle) as caught:
        store.set_dependencies(project["id"], "CYC-1", ["CYC-2"],
                               expected_version=before["version"])

    assert caught.value.status == 422
    assert caught.value.code == "dependency_cycle"
    after = store.get_ticket(project["id"], "CYC-1")
    assert after["dependencies"] == [], "rolled back"
    assert after["version"] == before["version"], "no version bump on a refusal"
    edges = store.conn.execute(
        "SELECT COUNT(*) FROM ticket_dependencies WHERE ticket_id = 'CYC-1'"
    ).fetchone()[0]
    assert edges == 0


def test_transitive_cycle_is_detected(store, project):
    """A -> B -> C, then C -> A must be refused."""
    store.create_ticket(project["id"], "CHAIN-1", "A")
    store.create_ticket(project["id"], "CHAIN-2", "B", dependencies=["CHAIN-1"])
    store.create_ticket(project["id"], "CHAIN-3", "C", dependencies=["CHAIN-2"])
    first = store.get_ticket(project["id"], "CHAIN-1")
    with pytest.raises(DependencyCycle) as caught:
        store.set_dependencies(project["id"], "CHAIN-1", ["CHAIN-3"],
                               expected_version=first["version"])
    cycle = caught.value.details["cycle"]
    assert cycle[0] == "CHAIN-1" and cycle[-1] == "CHAIN-1"


def test_self_dependency_is_a_cycle(store, project):
    store.create_ticket(project["id"], "SELF-1", "A")
    t = store.get_ticket(project["id"], "SELF-1")
    with pytest.raises(DependencyCycle):
        store.set_dependencies(project["id"], "SELF-1", ["SELF-1"],
                               expected_version=t["version"])


def test_creating_a_ticket_with_a_cycle_creates_no_ticket(store, project):
    """The rollback covers creation too, not just later edits."""
    store.create_ticket(project["id"], "MAKE-1", "A")
    store.create_ticket(project["id"], "MAKE-2", "B", dependencies=["MAKE-1"])
    # MAKE-3 depends on MAKE-2; then point MAKE-2 at MAKE-3 -> cycle.
    store.create_ticket(project["id"], "MAKE-3", "C", dependencies=["MAKE-2"])
    two = store.get_ticket(project["id"], "MAKE-2")
    with pytest.raises(DependencyCycle):
        store.set_dependencies(project["id"], "MAKE-2", ["MAKE-3"],
                               expected_version=two["version"])
    assert store.get_ticket(project["id"], "MAKE-2")["dependencies"] == ["MAKE-1"]


def test_claim_refuses_while_dependencies_are_unmet(store, project, agent):
    store.create_ticket(project["id"], "UP-1", "Upstream")
    store.create_ticket(project["id"], "DOWN-1", "Downstream",
                        dependencies=["UP-1"])
    down = store.get_ticket(project["id"], "DOWN-1")
    with pytest.raises(DependencyUnmet) as caught:
        store.claim_ticket(project["id"], "DOWN-1", agent["id"],
                           expected_version=down["version"])
    assert caught.value.details["unmet"] == ["UP-1"]
    assert caught.value.status == 422

    up = store.get_ticket(project["id"], "UP-1")
    store.transition(project["id"], "UP-1", "done", expected_version=up["version"])
    down = store.get_ticket(project["id"], "DOWN-1")
    claimed = store.claim_ticket(project["id"], "DOWN-1", agent["id"],
                                 expected_version=down["version"])
    assert claimed["state"] == "claimed"


def test_unknown_dependency_counts_as_unmet(store, project, agent):
    """A dependency on a ticket that does not exist must not read as satisfied."""
    store.create_ticket(project["id"], "GHOST-1", "Depends on a ghost")
    t = store.get_ticket(project["id"], "GHOST-1")
    store.set_dependencies(project["id"], "GHOST-1", ["NOPE-99"],
                           expected_version=t["version"])
    assert store.get_ticket(project["id"], "GHOST-1")["dependency_blocked"] is True
    t = store.get_ticket(project["id"], "GHOST-1")
    with pytest.raises(DependencyUnmet):
        store.claim_ticket(project["id"], "GHOST-1", agent["id"],
                           expected_version=t["version"])
