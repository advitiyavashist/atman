"""Legacy import: whole-board fidelity, one transaction, and the ownership lock.

The bar this file holds the importer to is not "the counts match". It is that
every field of every ticket can be rebuilt from the database and compared to the
file it came from, which is what `verify_round_trip` does and what
`test_imports_the_sample_board_without_loss` asserts. Counts alone were the old
bar, and the old importer passed them while dropping ten fields per ticket and
collapsing 1020 note timestamps onto the import minute.

The board under test is `tests/data/legacy_board`, committed and synthetic --
see `scripts/make_sample_legacy_board.py` for what each ticket in it is for.
"""

import json
import shutil
from pathlib import Path

import pytest

from ticket_board.storage import BoardStore, LegacyWriterActive
from ticket_board.storage import legacy as legacy_module
from ticket_board.storage.legacy import (
    assert_writable,
    import_legacy_board,
    legacy_documents,
    legacy_fields,
    legacy_messages,
    legacy_notes,
    ownership,
    read_legacy_board,
    reconstruct_legacy_ticket,
    release_ownership,
    take_ownership,
    verify_round_trip,
)

SAMPLE_BOARD = Path(__file__).resolve().parents[1] / "data" / "legacy_board"

# The board this project actually runs on. Present only on the machine it runs
# on, so it is a supplement to the committed sample board, never the only
# evidence -- a skipped test proves nothing on anyone else's checkout.
REAL_BOARD = Path("/Users/kavana/Downloads/steer/.tickets")


def _legacy_board(tmp_path, tickets):
    directory = tmp_path / "legacy"
    directory.mkdir()
    for ticket in tickets:
        (directory / "{}.json".format(ticket["id"])).write_text(
            json.dumps(ticket, indent=2)
        )
    return directory


@pytest.fixture()
def sample(tmp_path):
    """A writable copy of the committed sample board."""
    copy = tmp_path / "sample"
    shutil.copytree(SAMPLE_BOARD, copy)
    return copy


@pytest.fixture()
def imported(store, sample):
    return import_legacy_board(store, sample, project_name="Sample")


# --------------------------------------------------------------- whole board

def test_imports_the_sample_board_without_loss(store, sample, imported):
    """Every field of every ticket, rebuilt from the database, equals the file.

    This is the acceptance criterion for the whole ticket. An empty difference
    list means no key of no ticket was dropped, reshaped or re-timestamped --
    and the two edges this board loses on purpose are not silent, they are named
    in `report.dropped_dependencies`, which is why they do not appear here.
    """
    assert verify_round_trip(store, imported, sample) == []


def test_the_board_around_the_tickets_is_imported_too(store, imported):
    """Agents, epics, sprints, briefs, coordination, board files, messages.

    `read_legacy_board` globbed only top-level `*.json`, so everything in this
    test was previously invisible to the import.
    """
    project = imported.project_id
    kinds = {k: len(legacy_documents(store, project, kind=k))
             for k in ("agent", "epic", "sprint", "brief", "coordination",
                       "board_file")}
    assert kinds == {"agent": 4, "epic": 2, "sprint": 2, "brief": 1,
                     "coordination": 1, "board_file": 3}

    # A `.lock` is a mutex held by a running CLI, not board content.
    names = {d["name"] for d in legacy_documents(store, project)}
    assert not any(n.endswith(".lock") for n in names)

    assert imported.imported_messages == 5
    on_ticket = legacy_messages(store, project, ticket_id="LEG-1")
    assert [m["text"] for m in on_ticket] == ["claimed the schema ticket"]
    # A message about a ticket that is not on the board keeps its citation but
    # resolves to no row, rather than being dropped or pointed somewhere wrong.
    stray = [m for m in legacy_messages(store, project) if m["re"] == "T-999"]
    assert stray and stray[0]["ticket_id"] is None


def test_fields_with_no_contract_home_are_archived_not_dropped(store, imported):
    """priority/epic/sprint/needs/suggested/done_at/review_at/commit/branch/pr.

    None of these exist in the frozen T-178 `Ticket`. The old importer read
    `title/role/outcome/files/status/owner/created/updated/notes/deps` and
    nothing else, so all ten vanished with no record that they had.
    """
    archived = legacy_fields(store, imported.project_id, "LEG-1")
    assert archived["priority"] == 1
    assert archived["epic"] == "E-001"
    assert archived["sprint"] == "S-01"
    assert archived["done_at"] == "2026-01-03T17:20:00Z"
    assert archived["review_at"] == "2026-01-03T16:00:00Z"
    assert archived["commit"] == "agent-alpha/schema@a1b2c3d"
    assert archived["branch"] == "agent-alpha/schema"
    assert archived["pr"] == "12"
    assert archived["suggested"] == "agent-alpha"
    assert archived["legacy_id"] == "T-001", "the id a human cites is kept"

    assert legacy_fields(store, imported.project_id, "LEG-3")["needs"] == \
        ["own-machine"]

    # And the report names every one of them, which is the input to T-211.
    for field in ("priority", "epic", "sprint", "needs", "suggested", "done_at",
                  "review_at", "commit", "branch", "pr"):
        assert field in imported.archived_fields, field


def test_a_legacy_key_the_importer_has_never_seen_is_kept_and_counted(
        store, imported):
    """The guard against this code aging badly.

    A field added to the CLI after this importer was written must not be able to
    disappear quietly. `squad` is that field on the sample board.
    """
    assert imported.unknown_fields == {"squad": 1}
    assert legacy_fields(store, imported.project_id, "LEG-17")["squad"] == \
        "platform"


def test_the_body_is_read_from_body_not_from_a_key_that_does_not_exist(
        store, imported):
    """`legacy.py` read `item.get("outcome")`; the legacy board writes `body`.

    130 of 133 tickets on the real board have a non-empty body, and every one of
    them imported as an empty outcome.
    """
    assert store.get_ticket(imported.project_id, "LEG-1")["outcome"] == \
        "Tables, indexes, triggers."


# ------------------------------------------------------------------ timings

def test_notes_keep_their_own_timestamps(store, imported):
    """Progress history, not a single import-time smear.

    Every note went through `add_update`, which stamps `ids.now()`. On the real
    board that collapsed 1062 notes written over two days onto one minute --
    which destroys the one thing a progress trail exists for.
    """
    updates = store.list_updates(imported.project_id, "LEG-1")
    assert [u["created_at"] for u in updates] == [
        "2026-01-02T10:05:00Z", "2026-01-03T16:00:00Z",
        "2026-01-03T16:00:00Z", "2026-01-03T17:20:00Z",
    ]


def test_notes_sharing_a_timestamp_keep_their_source_order(store, imported):
    """59 note pairs on the real board share an `at` with a sibling.

    `ticket_updates` has no ordinal and its ids carry a random suffix, so
    `ORDER BY created_at, id` cannot recover source order on its own.
    """
    notes = legacy_notes(store, imported.project_id, "LEG-1")
    assert [n["text"] for n in notes[1:3]] == [
        "review requested", "handing to review"]


def test_a_note_longer_than_the_contract_allows_survives_intact(store, imported):
    """`TicketUpdate.body` is capped at 4000; two real notes exceed it.

    Truncating loses evidence and storing the whole thing in one row produces a
    record that fails the published schema the first time it is served. So it is
    split into contract-legal rows and rejoined on read.
    """
    notes = legacy_notes(store, imported.project_id, "LEG-13")
    assert len(notes) == 1
    assert notes[0]["text"] == "A very long handover note. " * 200
    rows = store.list_updates(imported.project_id, "LEG-13")
    assert len(rows) == 2, "stored as two contract-legal rows"
    assert all(len(r["body"]) <= 4000 for r in rows)
    assert "note longer than the 4000-char update body" in \
        imported.contract_mismatches


def test_a_zero_offset_timestamp_is_not_thrown_away(store, imported):
    """`2026-01-10T11:22:33.456789+00:00` is valid RFC 3339 and it is UTC.

    Two notes on the real board carry exactly this shape. Rejecting it moved
    them to the ticket's `updated` time, 83 minutes away, silently.
    """
    notes = legacy_notes(store, imported.project_id, "LEG-16")
    assert notes[0]["at"] == "2026-01-10T11:22:33.456789+00:00"


def test_claimed_at_survives(store, imported):
    assert store.get_ticket(imported.project_id, "LEG-1")["claimed_at"] == \
        "2026-01-02T10:05:00Z"


# ------------------------------------------------------------------- owners

def test_an_owned_ticket_has_a_real_owner_not_a_name_in_the_handoff(
        store, imported):
    """`tickets.owner` is a foreign key to `agents(id)`; a name is not one.

    The old importer put the legacy owner in `handoff` and left `owner` null, so
    an imported board rendered ~110 tickets in progress with nobody on them.
    """
    tickets = store.list_tickets(imported.project_id)
    active = [t for t in tickets if t["state"] in ("claimed", "review")]
    assert active, "the sample board has claimed and in-review tickets"
    assert all(t["owner"] for t in active), \
        "no active ticket may render without an owner"

    owner_id = store.get_ticket(imported.project_id, "LEG-1")["owner"]
    assert store.get_agent(owner_id)["name"] == "agent-alpha"


def test_an_owner_with_no_runtime_record_still_gets_an_agent_row(store, imported):
    """`agent-echo` owns LEG-19 but has no `agents/agent-echo.json`."""
    owner_id = store.get_ticket(imported.project_id, "LEG-19")["owner"]
    assert store.get_agent(owner_id)["name"] == "agent-echo"


def test_imported_agents_are_offline_not_connected(store, imported):
    """They have never connected to *this* server.

    Importing them as anything else puts a green dot on the dashboard for a
    process that is not there.
    """
    owner_id = store.get_ticket(imported.project_id, "LEG-1")["owner"]
    agent = store.get_agent(owner_id)
    assert agent["state"] == "offline"
    assert agent["worktree"] == "/work/board/.worktrees/agent-alpha"
    assert agent["runtime"] == {}, "Agent.runtime is a closed contract schema"


# ------------------------------------------------------------- one transaction

def test_a_failure_midway_leaves_no_half_imported_project(store, sample,
                                                          monkeypatch):
    """The import is one transaction, so a crash imports nothing.

    It used to be one `write_txn` per ticket, per state change and per note. A
    failure two thirds of the way through committed everything before it, and a
    re-run then created a *second* project holding a second copy of the board.
    """
    calls = {"n": 0}
    real = legacy_module._import_ticket

    def explode(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(legacy_module, "_import_ticket", explode)
    with pytest.raises(RuntimeError):
        import_legacy_board(store, sample)

    assert store.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0
    assert store.conn.execute(
        "SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0

    # And the retry, once the cause is fixed, imports exactly one board.
    monkeypatch.setattr(legacy_module, "_import_ticket", real)
    report = import_legacy_board(store, sample)
    assert store.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    assert report.imported_tickets == 20


def test_re_importing_an_owned_directory_writes_nothing(store, sample):
    """The sentinel check runs before the first write, not after the last.

    Previously the second import created a project and imported every ticket
    into it, and only then hit `take_ownership` and raised -- leaving the
    duplicate behind.
    """
    import_legacy_board(store, sample)
    before = store.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    with pytest.raises(LegacyWriterActive):
        import_legacy_board(store, sample)
    assert store.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    assert store.conn.execute(
        "SELECT COUNT(*) FROM tickets").fetchone()[0] == before


# --------------------------------------------------------------- the counting

def test_counts_preserved_can_actually_fail(store, tmp_path):
    """It could not before, and a check that cannot fail is not a check.

    `import_legacy_board` ended with `source_dependencies -= dropped`, so the
    two sides of the comparison were made equal after the fact and
    `counts_preserved` was True by construction. The old test asserted that.
    """
    legacy = _legacy_board(tmp_path, [
        {"id": "T-001", "title": "a", "status": "open", "deps": ["T-999"]},
    ])
    report = import_legacy_board(store, legacy)

    assert report.dropped_dependencies == [
        {"ticket": "T-001", "depends_on": "T-999",
         "reason": "dependency not present in source board"}
    ]
    assert report.counts_preserved is False, \
        "an edge was dropped, so nothing was preserved"
    assert report.counts_reconcile is True, \
        "but every source edge is accounted for: imported or dropped"
    assert report.source_dependencies == 1, "the source count is not rewritten"


def test_the_sample_board_reconciles_exactly(imported):
    assert imported.counts_reconcile is True
    assert imported.source_tickets == 21, "20 readable plus the truncated file"
    assert imported.imported_tickets == 20
    assert imported.source_notes == imported.imported_updates == 10
    assert imported.imported_update_rows == 11, "one note needed splitting"


def test_a_file_that_cannot_be_read_is_reported(store, imported):
    """`read_legacy_board` used to `continue` on a bad file and never touch
    `report.skipped`, contradicting its own docstring. A half-written ticket
    then vanished from both sides of the count at once."""
    assert {"kind": "ticket", "id": "T-021",
            "reason": "unreadable json: JSONDecodeError"} in imported.skipped
    assert imported.counts_preserved is False


def test_board_files_are_not_reported_as_skipped_tickets(imported):
    """`master.json` and `roles.json` are board state, not failed tickets.

    Filing them under `skipped` would make an operator hunt for two tickets that
    were never there; ignoring them would lose the files.
    """
    assert sorted(imported.non_ticket_files) == ["master.json", "roles.json"]
    assert [s["id"] for s in imported.skipped_tickets] == ["T-021"]


def test_a_bad_line_in_messages_is_reported_not_a_lost_ticket(imported):
    assert {"kind": "message", "id": "messages.jsonl:4",
            "reason": "unreadable json line"} in imported.skipped
    assert imported.imported_messages == 5


def test_two_ids_that_collide_are_reported_not_silently_overwritten(
        store, tmp_path):
    """`T-179` and `X-179` both re-prefix to `LEG-179`.

    The second insert would have overwritten the first with no record at all.
    """
    legacy = _legacy_board(tmp_path, [
        {"id": "T-007", "title": "first", "status": "open"},
        {"id": "X-007", "title": "second", "status": "open"},
    ])
    report = import_legacy_board(store, legacy)
    assert report.imported_tickets == 1
    assert report.skipped_tickets == [
        {"kind": "ticket", "id": "X-007",
         "reason": "id collides with T-007 (both map to LEG-7)"}
    ]


# ------------------------------------------------------------- dependencies

def test_a_cycle_drops_its_edges_and_keeps_the_rest_of_the_board(imported):
    """One bad pair must not cost the other eighteen tickets their import.

    The old importer wired edges through per-ticket transactions, so a cycle
    raised mid-loop and left everything before it committed.
    """
    cycles = [d for d in imported.dropped_dependencies
              if "cycle" in d["reason"]]
    assert cycles and cycles[0]["ticket"] == "T-007"
    assert imported.imported_tickets == 20


def test_a_duplicate_edge_is_accounted_for_rather_than_absorbed(imported):
    """LEG-5 lists `T-001` twice: two source edges, one imported.

    The surplus is recorded as a drop. An edge that disappears between the two
    totals with nothing to point at is the accounting `counts_preserved` used
    to hide.
    """
    duplicates = [d for d in imported.dropped_dependencies
                  if "duplicate edge" in d["reason"]]
    assert len(duplicates) == 1 and duplicates[0]["ticket"] == "T-005"


def test_dependency_blocking_is_derived_from_the_imported_edges(store, imported):
    by_id = {t["id"]: t for t in store.list_tickets(imported.project_id)}
    assert by_id["LEG-1"]["state"] == "done"
    assert by_id["LEG-3"]["dependencies"] == ["LEG-1", "LEG-2"]
    assert by_id["LEG-3"]["dependency_blocked"] is False
    assert by_id["LEG-4"]["dependency_blocked"] is False, \
        "its only edge pointed off the board and was dropped, not invented"


# -------------------------------------------------------- contract-shaped-ness

def test_review_evidence_is_not_forced_into_a_shape_it_does_not_fit(
        store, imported):
    """The contract's `Sha` is 40 hex characters. Legacy commits are not.

    All 106 `commit` values on the real board are `branch@shortsha`, and `pr` is
    a bare number where the contract wants a URI. Writing those into
    `GitEvidence` produces records that fail the published schema the moment
    T-180 serves them, and inventing a sha would be worse. So they are archived
    verbatim and the mismatch is reported with counts, for T-211.
    """
    assert store.get_ticket(imported.project_id, "LEG-1")["evidence"] is None
    assert legacy_fields(store, imported.project_id, "LEG-1")["commit"] == \
        "agent-alpha/schema@a1b2c3d"
    assert imported.contract_mismatches["commit is not a 40-hex Sha"] == 2

    # The one ticket whose commit *is* a full sha gets real evidence and a real
    # review row -- so this is a data mismatch, not a missing code path.
    conforming = store.get_ticket(imported.project_id, "LEG-2")
    assert conforming["evidence"]["sha"] == \
        "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c"
    assert conforming["evidence"]["branch"] == "agent-bravo/contract"
    assert conforming["evidence"]["pr_url"].startswith("https://")
    assert imported.imported_reviews == 1


def test_a_sha_resolver_turns_a_short_commit_into_real_evidence(store, sample):
    """The escape hatch for an operator who has the repository to hand."""
    def resolver(short, branch):
        return "a1b2c3d" + "0" * 33 if short == "a1b2c3d" else None

    report = import_legacy_board(store, sample, sha_resolver=resolver)
    evidence = store.get_ticket(report.project_id, "LEG-1")["evidence"]
    assert evidence["sha"] == "a1b2c3d" + "0" * 33
    assert report.imported_reviews == 2


def test_values_over_a_contract_limit_are_truncated_and_the_original_kept(
        store, imported):
    """The column takes what the API can legally serve; the archive takes the rest."""
    long_title = store.get_ticket(imported.project_id, "LEG-14")["title"]
    assert len(long_title) == 200
    archived = legacy_fields(store, imported.project_id, "LEG-14")
    assert archived["title_full"] == "Rework the ingest pipeline " * 10

    long_body = store.get_ticket(imported.project_id, "LEG-15")["outcome"]
    assert len(long_body) == 4000
    assert legacy_fields(store, imported.project_id, "LEG-15")["body_full"] == \
        "Background paragraph explaining the change. " * 120
    assert imported.truncated_fields == {"title": 1, "body": 1}


def test_an_unknown_status_is_reported_not_guessed(store, imported):
    assert imported.unmapped_states == {"percolating": 1}
    assert store.get_ticket(imported.project_id, "LEG-11")["state"] == "open"
    # ...and the word itself is kept, so the guess is reversible.
    assert legacy_fields(store, imported.project_id,
                         "LEG-11")["legacy_status"] == "percolating"


def test_every_legacy_status_word_maps(store, imported):
    states = {t["id"]: t["state"] for t in store.list_tickets(imported.project_id)}
    assert states["LEG-1"] == "done"
    assert states["LEG-3"] == "claimed"        # 'in progress'
    assert states["LEG-8"] == "blocked"
    assert states["LEG-9"] == "open"           # 'to do'
    assert states["LEG-10"] == "open"          # 'todo'
    assert states["LEG-12"] == "review"        # 'in review'
    assert states["LEG-13"] == "claimed"


# ------------------------------------------------------------------ the lock

def test_originals_are_never_modified(store, sample):
    before = {p.name: p.read_bytes() for p in sorted(sample.rglob("*"))
              if p.is_file()}
    import_legacy_board(store, sample)
    after = {p.name: p.read_bytes() for p in sorted(sample.rglob("*"))
             if p.is_file() and p.name != ".server-owned.json"}
    assert after == before, "import must not touch the source files"


def test_import_takes_ownership_and_blocks_legacy_writers(store, sample):
    assert assert_writable(sample) is True, "writable before the import"

    import_legacy_board(store, sample, owner="ticket-board-test")

    with pytest.raises(LegacyWriterActive) as caught:
        assert_writable(sample)
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


def test_rollback_releases_the_board_to_the_legacy_cli(store, sample):
    """The documented rollback: release, and the old CLI works again."""
    import_legacy_board(store, sample)
    with pytest.raises(LegacyWriterActive):
        assert_writable(sample)

    assert release_ownership(sample) is True
    assert assert_writable(sample) is True, "legacy writes work again"
    assert ownership(sample) is None
    # The source of truth is intact, so the rollback is real.
    assert len(read_legacy_board(sample)) == 20


def test_import_without_take_over_leaves_the_board_writable(store, sample):
    report = import_legacy_board(store, sample, take_over=False)
    assert report.imported_tickets == 20
    assert assert_writable(sample) is True


# ------------------------------------------------------------ reconstruction

def test_a_ticket_can_be_rebuilt_from_the_database_alone(store, imported):
    """What makes the archive useful rather than a place things go to die."""
    rebuilt = reconstruct_legacy_ticket(store, imported.project_id, "LEG-1")
    source = json.loads((SAMPLE_BOARD / "T-001.json").read_text())
    assert rebuilt["id"] == "T-001"
    assert rebuilt["priority"] == source["priority"]
    assert rebuilt["notes"] == source["notes"]
    assert rebuilt["status"] == "done"


# ------------------------------------------------- the board this repo runs on

@pytest.mark.skipif(not REAL_BOARD.is_dir(), reason="live board not present")
def test_the_live_board_also_round_trips(store, tmp_path):
    """A supplement to the sample board, not a substitute for it.

    The sample board is designed to be awkward; this one is merely real -- 133
    tickets, 56 edges and 1062 notes written by two dozen agents over two days.
    Copied first: the live board is never opened for writing by a test.
    """
    copy = tmp_path / "real"
    shutil.copytree(REAL_BOARD, copy)

    report = import_legacy_board(store, copy, project_name="Steer")
    assert report.skipped == [], "no ticket may be silently dropped"
    assert report.counts_preserved is True
    assert report.unknown_fields == {}, \
        "a new legacy field appeared; map or archive it deliberately"
    assert verify_round_trip(store, report, copy) == []

    tickets = store.list_tickets(report.project_id)
    assert len(tickets) == report.source_tickets > 100
    active = [t for t in tickets if t["state"] in ("claimed", "review")]
    assert all(t["owner"] for t in active)
