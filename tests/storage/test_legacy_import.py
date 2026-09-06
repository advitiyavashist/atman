"""Legacy import: counts, dependencies, originals, and the ownership lock."""

import json

import pytest

from ticket_board.storage import BoardStore, LegacyWriterActive
from ticket_board.storage.legacy import (
    assert_writable,
    import_legacy_board,
    ownership,
    read_legacy_board,
    release_ownership,
    take_ownership,
)


def _legacy_board(tmp_path, tickets):
    directory = tmp_path / "legacy"
    directory.mkdir()
    for ticket in tickets:
        (directory / "{}.json".format(ticket["id"])).write_text(
            json.dumps(ticket, indent=2)
        )
    return directory


def test_import_preserves_ticket_and_dependency_counts(store, tmp_path):
    legacy = _legacy_board(tmp_path, [
        {"id": "T-001", "title": "First", "status": "done", "deps": [],
         "role": "backend"},
        {"id": "T-002", "title": "Second", "status": "open", "deps": ["T-001"]},
        {"id": "T-003", "title": "Third", "status": "blocked",
         "deps": ["T-001", "T-002"]},
    ])
    report = import_legacy_board(store, legacy, project_name="Legacy")

    assert report.imported_tickets == 3
    assert report.source_tickets == 3
    assert report.imported_dependencies == 3
    assert report.counts_preserved is True

    tickets = store.list_tickets(report.project_id)
    assert len(tickets) == 3
    by_id = {t["id"]: t for t in tickets}
    assert by_id["LEG-2"]["dependencies"] == ["LEG-1"]
    assert sorted(by_id["LEG-3"]["dependencies"]) == ["LEG-1", "LEG-2"]


def test_import_maps_legacy_states(store, tmp_path):
    legacy = _legacy_board(tmp_path, [
        {"id": "T-001", "title": "a", "status": "done", "deps": []},
        {"id": "T-002", "title": "b", "status": "in progress", "deps": []},
        {"id": "T-003", "title": "c", "status": "review", "deps": []},
        {"id": "T-004", "title": "d", "status": "blocked", "deps": []},
    ])
    report = import_legacy_board(store, legacy)
    states = {t["id"]: t["state"] for t in store.list_tickets(report.project_id)}
    assert states == {"LEG-1": "done", "LEG-2": "claimed",
                      "LEG-3": "review", "LEG-4": "blocked"}


def test_unknown_state_is_reported_not_guessed(store, tmp_path):
    legacy = _legacy_board(tmp_path, [
        {"id": "T-001", "title": "a", "status": "percolating", "deps": []},
    ])
    report = import_legacy_board(store, legacy)
    assert report.unmapped_states == {"percolating": 1}
    assert store.list_tickets(report.project_id)[0]["state"] == "open"


def test_dependency_on_a_missing_ticket_is_reported_not_silently_dropped(
        store, tmp_path):
    legacy = _legacy_board(tmp_path, [
        {"id": "T-001", "title": "a", "status": "open", "deps": ["T-999"]},
    ])
    report = import_legacy_board(store, legacy)
    assert report.dropped_dependencies == [
        {"ticket": "T-001", "depends_on": "T-999",
         "reason": "dependency not present in source board"}
    ]
    assert report.counts_preserved is True, \
        "counts reconcile once the drop is accounted for"


def test_originals_are_never_modified(store, tmp_path):
    legacy = _legacy_board(tmp_path, [
        {"id": "T-001", "title": "a", "status": "open", "deps": []},
        {"id": "T-002", "title": "b", "status": "open", "deps": ["T-001"]},
    ])
    before = {p.name: p.read_bytes() for p in sorted(legacy.glob("*.json"))}
    import_legacy_board(store, legacy)
    after = {p.name: p.read_bytes()
             for p in sorted(legacy.glob("*.json"))
             if p.name != ".server-owned.json"}
    assert after == before, "import must not touch the source files"


def test_import_takes_ownership_and_blocks_legacy_writers(store, tmp_path):
    legacy = _legacy_board(tmp_path, [
        {"id": "T-001", "title": "a", "status": "open", "deps": []},
    ])
    assert assert_writable(legacy) is True, "writable before the import"

    import_legacy_board(store, legacy, owner="ticket-board-test")

    with pytest.raises(LegacyWriterActive) as caught:
        assert_writable(legacy)
    assert caught.value.code == "legacy_writer_active"
    assert caught.value.status == 409
    assert caught.value.details["owner"] == "ticket-board-test"


def test_ownership_is_visible_in_the_directory(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    take_ownership(legacy, owner="server-1")
    assert (legacy / ".server-owned.json").is_file(), \
        "an operator must be able to see why their CLI is refusing"
    assert ownership(legacy)["owner"] == "server-1"


def test_taking_ownership_twice_is_refused(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    take_ownership(legacy, owner="server-1")
    with pytest.raises(LegacyWriterActive):
        take_ownership(legacy, owner="server-2")


def test_rollback_releases_the_board_to_the_legacy_cli(store, tmp_path):
    """The documented rollback: release, and the old CLI works again."""
    legacy = _legacy_board(tmp_path, [
        {"id": "T-001", "title": "a", "status": "open", "deps": []},
    ])
    import_legacy_board(store, legacy)
    with pytest.raises(LegacyWriterActive):
        assert_writable(legacy)

    assert release_ownership(legacy) is True
    assert assert_writable(legacy) is True, "legacy writes work again"
    assert ownership(legacy) is None
    # The source of truth is intact, so the rollback is real.
    assert len(read_legacy_board(legacy)) == 1


def _large_multi_chain_board(tmp_path):
    """A synthetic ~60-ticket legacy board with the shape that matters for an
    import stress test: branching dependency chains (not just a linear one),
    every legacy status, and more than one owning agent -- without touching
    this project's own live board or any path specific to one machine.
    """
    directory = tmp_path / "legacy-large"
    directory.mkdir()
    agents = ["agent-a", "agent-b", "agent-c", "agent-d", "agent-e", "agent-f"]
    statuses = ["done", "open", "in progress", "review", "blocked"]
    tickets = []
    for i in range(1, 61):
        deps = []
        if i > 1:
            deps.append("T-{:03d}".format(i - 1))
        if i > 10 and i % 7 == 0:
            deps.append("T-{:03d}".format(i - 10))
        tickets.append({
            "id": "T-{:03d}".format(i),
            "title": "Synthetic ticket {}".format(i),
            "status": statuses[i % len(statuses)],
            "deps": deps,
            "role": agents[i % len(agents)],
        })
    for ticket in tickets:
        (directory / "{}.json".format(ticket["id"])).write_text(
            json.dumps(ticket, indent=2)
        )
    return directory, tickets


def test_imports_a_large_multi_chain_board_without_loss(store, tmp_path):
    """Import a board large and varied enough to stress the import path.

    A synthetic three-ticket board proves very little; this one has branching
    dependency chains (not just a line), every legacy status, and tickets
    owned by more than one agent -- built fresh under `tmp_path` on every run
    so the test never reads this project's own live board or depends on any
    one machine's filesystem layout.
    """
    legacy, source_tickets = _large_multi_chain_board(tmp_path)
    source = read_legacy_board(legacy)
    assert len(source) == len(source_tickets)

    report = import_legacy_board(store, legacy, project_name="Synthetic")

    assert report.imported_tickets == report.source_tickets
    assert report.skipped == [], "no ticket may be silently dropped"
    assert report.counts_preserved is True

    imported = store.list_tickets(report.project_id)
    assert len(imported) == len(source)

    # Dependency edges survive, including a branching one: T-014 depends on
    # both T-013 (the linear chain) and T-004 (the every-7th-ticket branch).
    by_id = {t["id"]: t for t in imported}
    assert "LEG-14" in by_id and "LEG-13" in by_id and "LEG-4" in by_id
    assert set(by_id["LEG-14"]["dependencies"]) == {"LEG-13", "LEG-4"}, \
        "the branching T-014 -> {T-013, T-004} edges must survive the import"
    # And that blocking is derived correctly from the imported edges.
    assert by_id["LEG-14"]["dependency_blocked"] is (
        by_id["LEG-13"]["state"] != "done"
        or by_id["LEG-4"]["state"] != "done"
    )
