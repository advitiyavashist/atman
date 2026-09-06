"""T-246: `tickets reopen --notes ...` used to exit 2 and change NOTHING while
argparse's "unrecognized arguments" message echoed the note text back, so a
caller reading only stdout (or skimming) saw what looked like their own note
succeeding. Covers the three-part fix: reopen gets a real --notes/--by, the
same trap is closed on assign and block, and any remaining argparse failure
prints an explicit trailing "NO CHANGE WAS MADE" instead of failing quietly.
"""
import json

from test_wakeup import board, run  # noqa: F401


def test_reopen_with_notes_succeeds_and_records_attributed_note(board):
    run(board, "claim", "T-001", "--owner", "alice", agent="alice")
    r = run(board, "reopen", "T-001", "--notes", "reopening: found a real defect",
            "--by", "cos-opus", agent="cos-opus")
    assert r.returncode == 0, r.stderr
    assert "reopened" in r.stdout

    show = run(board, "show", "T-001", "--json", agent="cos-opus")
    t = json.loads(show.stdout)
    assert t["status"] == "open"
    assert t["owner"] == ""
    notes = [n for n in t["notes"] if n["text"] == "reopening: found a real defect"]
    assert notes, t["notes"]
    assert notes[-1]["by"] == "cos-opus", (
        "the note must be attributed to the agent doing the reopening, not the "
        "outgoing owner (alice) -- same misattribution class as T-238"
    )


def test_reopen_without_notes_is_unchanged(board):
    """Backward compatibility: a bare `tickets reopen <id>` (no --notes) must
    keep working exactly as before -- no note is fabricated for it."""
    run(board, "claim", "T-001", "--owner", "alice", agent="alice")
    r = run(board, "reopen", "T-001", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "T-001 reopened" in r.stdout

    show = run(board, "show", "T-001", "--json", agent="alice")
    t = json.loads(show.stdout)
    assert t["status"] == "open"
    assert t["owner"] == ""
    assert t["notes"] == []


def test_unrecognized_reopen_flag_is_loud_and_changes_nothing(board):
    """The original bug, generalized: any genuinely bad flag must still fail
    (argparse's job), but now say so unmistakably instead of just echoing the
    caller's own text back with no visible failure marker."""
    run(board, "claim", "T-001", "--owner", "alice", agent="alice")
    r = run(board, "reopen", "T-001", "--this-flag-does-not-exist", "some text that could be mistaken for success",
            agent="alice")
    assert r.returncode == 2
    assert "NO CHANGE WAS MADE" in r.stderr, r.stderr

    show = run(board, "show", "T-001", "--json", agent="alice")
    t = json.loads(show.stdout)
    assert t["status"] == "claimed", "the ticket must be untouched by the rejected call"
    assert t["owner"] == "alice"


def test_assign_notes_is_no_longer_rejected_and_is_appended(board):
    run(board, "claim", "T-001", "--owner", "alice", agent="alice")
    r = run(board, "assign", "T-001", "--priority", "1", "--notes", "bumping: blocks 3 tickets", agent="alice")
    assert r.returncode == 0, r.stderr

    show = run(board, "show", "T-001", "--json", agent="alice")
    t = json.loads(show.stdout)
    assert t["priority"] == 1
    assert any("bumping: blocks 3 tickets" in n["text"] for n in t["notes"]), t["notes"]


def test_block_accepts_notes_as_an_alias_for_reason(board):
    r = run(board, "block", "T-001", "--notes", "stuck: waiting on operator creds", agent="alice")
    assert r.returncode == 0, r.stderr

    show = run(board, "show", "T-001", "--json", agent="alice")
    t = json.loads(show.stdout)
    assert t["status"] == "blocked"
    assert any("stuck: waiting on operator creds" in n["text"] for n in t["notes"]), t["notes"]
