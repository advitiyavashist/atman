"""Legacy import + backup rollback against committed fixtures. No live board."""

import shutil
from pathlib import Path

from ticket_board.storage import BoardStore
from ticket_board.storage.backup import backup_database, integrity_check, restore_database
from ticket_board.storage.legacy import import_legacy_board, verify_round_trip

SAMPLE_BOARD = Path(__file__).resolve().parents[2] / "data" / "legacy_board"


def test_legacy_sample_imports_without_loss(tmp_path):
    copy = tmp_path / "sample"
    shutil.copytree(SAMPLE_BOARD, copy)
    store = BoardStore(tmp_path / "imported.sqlite3")
    try:
        report = import_legacy_board(store, copy, project_name="T185-sample")
        assert verify_round_trip(store, report, copy) == []
    finally:
        store.close()


def test_backup_rollback_drill_drops_post_snapshot_writes(tmp_path):
    db_path = tmp_path / "board.sqlite3"
    store = BoardStore(db_path)
    project = store.create_project("T185-rollback")
    agent = store.create_agent(project["id"], "backend-1")
    good = store.create_ticket(project["id"], "DRILL-1", "Known good")
    store.claim_ticket(project["id"], "DRILL-1", agent["id"],
                       expected_version=good["version"])
    good_state = store.get_ticket(project["id"], "DRILL-1")
    snapshot = backup_database(store.conn, tmp_path / "board.bak")
    store.create_ticket(project["id"], "DRILL-2", "Regrettable")
    store.close()

    result = restore_database(snapshot, db_path)
    assert result["replaced_copy"]
    store = BoardStore(db_path)
    try:
        assert integrity_check(store.conn) == "ok"
        tickets = store.list_tickets(project["id"])
        assert [t["id"] for t in tickets] == ["DRILL-1"]
        assert store.get_ticket(project["id"], "DRILL-1") == good_state
    finally:
        store.close()
