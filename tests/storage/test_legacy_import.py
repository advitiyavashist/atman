"""Legacy import: counts, dependencies, originals, and the ownership lock."""

import json
import shutil
from pathlib import Path

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

# The real board this project runs on. Copied, never opened for writing.
REAL_BOARD = Path("/Users/kavana/Downloads/steer/.tickets")


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


@pytest.mark.skipif(not REAL_BOARD.is_dir(), reason="live board not present")
def test_imports_the_real_steer_board_without_loss(store, tmp_path):
    """Import the actual 100+ ticket board this project runs on.

    A synthetic three-ticket board proves very little about an import; this one
    has real ids, real dependency chains and states written by six different
    agents over two sprints. Copied first -- the live board is never opened for
    writing by a test.
    """
    copy = tmp_path / "real"
    shutil.copytree(REAL_BOARD, copy)

    source = read_legacy_board(copy)
    assert len(source) > 50, "expected the real board to be substantial"

    report = import_legacy_board(store, copy, project_name="Steer")

    assert report.imported_tickets == report.source_tickets
    assert report.skipped == [], "no ticket may be silently dropped"
    assert report.counts_preserved is True

    imported = store.list_tickets(report.project_id)
    assert len(imported) == len(source)

    # Dependency edges survive: check a chain we know exists (T-180 -> T-179).
    by_id = {t["id"]: t for t in imported}
    assert "LEG-180" in by_id and "LEG-179" in by_id
    assert "LEG-179" in by_id["LEG-180"]["dependencies"], \
        "the T-180 -> T-179 edge must survive the import"
    # And that blocking is derived correctly from the imported edges.
    assert by_id["LEG-180"]["dependency_blocked"] is (
        by_id["LEG-179"]["state"] != "done"
    )
