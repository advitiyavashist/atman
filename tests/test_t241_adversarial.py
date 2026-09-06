"""T-241: cross-agent attack on T-212's D1 fix (unread()'s archive-consulting
branch in tickets.py, commit 131d27e) -- the one piece of the T-212 branch
neither original author nor cos-opus's own review had a second pair of eyes on,
since cos-opus wrote this patch itself.

Cases attacked, one test each:

  test_never_checked_in_agent_loses_archived_mail_forever
      REAL BUG (BLOCKING). `if since and (...)` short-circuits on since=="",
      which is exactly what every agent's FIRST-EVER `tickets inbox` call has
      (checkin()/join() deliberately never sets inbox_seen -- see tickets.py:484-486,
      "must not erase it" implies it also never initializes it). A DM addressed
      to an agent, archived by rotation before that agent's first inbox check,
      is silently and permanently invisible -- the exact failure class this
      patch exists to prevent, reintroduced for the one case nobody chose to
      attack: an agent with no history yet.

  test_since_equals_live_oldest_reduces_to_t228
      Attacked and SOLID (not a new bug). since == msgs[0].at (the boundary the
      strict '<' excludes) never hides mail that reading the archive would have
      surfaced: any archived message is chronologically <= the live file's
      oldest survivor, so if since ties the live oldest, every archived message
      also fails the final `m.get("at") > since` filter regardless of whether
      the archive was read. This tie is the pre-existing, already-filed T-228
      same-second hole, not a distinct D1 defect -- named here, not re-filed.

  test_caught_up_agent_never_reads_archives (the cost claim)
      Attacked and SOLID. Instruments load_messages() directly: a caught-up
      agent against 3 large archives triggers zero include_archives=True calls.
      The bounded-growth point of T-212 is not undone at the read side.

  test_empty_live_file_falls_through_to_archives
      Attacked and SOLID. `not msgs` correctly forces the archive branch when
      the live file is empty, independent of the since comparison.

  test_empty_and_truncated_archive_files_do_not_crash
      Attacked and SOLID. A zero-byte archive and one with a truncated JSON
      line are both silently skipped (per-line ValueError/IOError handling
      already in load_messages); the live message is still returned.

NOT ATTACKED, deliberately, per the review brief -- naming them so a future
reader does not read their absence as an oversight:
  - The T-228 same-second hole itself (a message posted in the same wall-clock
    second as an agent's inbox check is invisible to that agent forever). Real,
    already filed, already P1, out of scope for a D1-only pass.
  - The poster-vs-archive-swap race documented in c592632's own code comment
    (a poster mid-append during the rotation swap can still land its message
    in the file being replaced). Rotation-time race, not a read-side (unread())
    defect, and explicitly flagged by the rotation author as known.
  - The three original T-212 deliverables (messages.jsonl rotation, watch-log
    cap, MASTER.md trim) already survived cos-opus's own adversarial pass
    (352 passed, contracts required) -- not re-verified here per the brief.

Reproduction of the author's numbers: `/usr/bin/python3 -m pytest -q` on
sonnet-tickets/t212-tickets-bounds@510c496 (this worktree) => 559 passed,
matching the 559 claimed post-merge. Independently confirmed (not just
re-read) that removing the 7-line D1 patch turns both of the author's own
tests in test_rotation_unread.py red (stash-and-restore against
131d27e^:tickets.py), rather than trusting the author's own claim of having
done so.
"""
import importlib.util
import json
import os

from test_wakeup import board, run  # noqa: F401

TINY = {"TICKETS_MESSAGES_MAX_BYTES": "2000"}

_TICKETS_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tickets.py")


def _load_tickets_module():
    """Import tickets.py directly (it is a standalone script, not a package)
    so unread()/load_messages() can be instrumented in-process."""
    spec = importlib.util.spec_from_file_location("tickets_under_test", _TICKETS_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_never_checked_in_agent_loses_archived_mail_forever(board):
    """The case none of the D1 tests chose: an agent whose agent record has
    no 'inbox_seen' key at all, because it has never called `tickets inbox`
    before. `if since and (...)` treats since=="" as "skip the archive check"
    -- the opposite of correct, since an agent that has never read anything
    should see the most history, not the least."""
    run(board, "join", "alice", agent="alice")
    run(board, "join", "dave", agent="dave")  # dave never calls "inbox" -> no inbox_seen key yet
    run(board, "msg", "IMPORTANT-FOR-DAVE", "--to", "dave", agent="alice", env=TINY)
    for i in range(60):
        run(board, "msg", "filler%03d %s" % (i, "x" * 80), agent="alice", env=TINY)
    import glob
    assert glob.glob(os.path.join(str(board), "messages.*.jsonl")), "precondition: must have rotated"

    r = run(board, "inbox", "--limit", "500", agent="dave")
    assert "IMPORTANT-FOR-DAVE" in r.stdout, (
        "dave's very first ever inbox check (no prior inbox_seen) must not silently "
        "drop a DM addressed to him just because it was archived before he first "
        "checked in -- got: %r" % r.stdout
    )


def test_since_equals_live_oldest_reduces_to_t228(tmp_path):
    mod = _load_tickets_module()
    b = tmp_path / ".tickets"
    (b / "agents").mkdir(parents=True)
    tie = "2026-01-05T12:00:00Z"
    (b / "messages.2026-01-05.jsonl").write_text(
        json.dumps({"at": tie, "from": "alice", "to": "bob", "text": "ARCHIVED-TIE"}) + "\n"
    )
    (b / "messages.jsonl").write_text(
        json.dumps({"at": tie, "from": "alice", "to": "all", "text": "live-tie"}) + "\n"
    )
    (b / "agents" / "bob.json").write_text(json.dumps({"name": "bob", "inbox_seen": tie}))

    actual = mod.unread(str(b), "bob")
    forced_msgs = mod.load_messages(str(b), include_archives=True)
    forced = [
        m for m in forced_msgs
        if m.get("from") != "bob"
        and (not m.get("to") or m.get("to") == "bob" or m.get("to") == "all")
        and m.get("at", "") > tie
    ]
    assert actual == forced == [], (
        "if this ever diverges, the since==msgs[0].at boundary is a real, distinct "
        "D1 defect rather than a restatement of T-228: actual=%r forced=%r" % (actual, forced)
    )


def test_caught_up_agent_never_reads_archives(tmp_path):
    """The cost claim: 'the fast path is unchanged for a caught-up agent' is
    an assertion about both performance and correctness of the branch
    predicate. Instrument load_messages() to prove the archive read is
    skipped, not merely that the final answer happens to be right."""
    mod = _load_tickets_module()
    b = tmp_path / ".tickets"
    (b / "agents").mkdir(parents=True)
    for d in ("2026-01-01", "2026-01-02", "2026-01-03"):
        with open(b / ("messages.%s.jsonl" % d), "w") as f:
            for i in range(500):
                f.write(json.dumps({"at": d + "T00:00:00Z", "from": "alice", "to": "all", "text": "x" * 200}) + "\n")
    (b / "messages.jsonl").write_text(
        json.dumps({"at": "2026-01-04T00:00:00Z", "from": "alice", "to": "all", "text": "live"}) + "\n"
    )
    (b / "agents" / "bob.json").write_text(json.dumps({"name": "bob", "inbox_seen": "2026-01-04T00:00:01Z"}))

    calls = {"include_archives_true": 0}
    real_load = mod.load_messages

    def spy(board_, include_archives=False):
        if include_archives:
            calls["include_archives_true"] += 1
        return real_load(board_, include_archives=include_archives)

    mod.load_messages = spy
    try:
        result = mod.unread(str(b), "bob")
    finally:
        mod.load_messages = real_load

    assert result == []
    assert calls["include_archives_true"] == 0, (
        "COST CLAIM FALSE: a caught-up agent triggered an archive read anyway -- "
        "the D1 fix's bounded-growth point is undone at the read side."
    )


def test_empty_live_file_falls_through_to_archives(board):
    """Live messages.jsonl rotated down to nothing: `not msgs` must still let
    an agent who slept through it recover mail from the archive."""
    run(board, "join", "alice", agent="alice")
    run(board, "join", "bob", agent="bob")
    (board / "messages.2026-02-01.jsonl").write_text(
        json.dumps({"at": "2026-02-01T00:00:00Z", "from": "alice", "to": "bob", "text": "ONLY-IN-ARCHIVE"}) + "\n"
    )
    open(board / "messages.jsonl", "w").close()
    rec_path = board / "agents" / "bob.json"
    rec = json.loads(rec_path.read_text())
    rec["inbox_seen"] = "2025-01-01T00:00:00Z"
    rec_path.write_text(json.dumps(rec))

    r = run(board, "inbox", "--limit", "500", agent="bob")
    assert "ONLY-IN-ARCHIVE" in r.stdout, r.stdout


def test_empty_and_truncated_archive_files_do_not_crash(tmp_path):
    mod = _load_tickets_module()
    b = tmp_path / ".tickets"
    (b / "agents").mkdir(parents=True)
    (b / "messages.2026-02-01.jsonl").write_text("")
    (b / "messages.2026-02-02.jsonl").write_text('{"at": "2026-02-02T00:00:00Z", "from": "a", "to": "bob"')
    (b / "messages.jsonl").write_text(
        json.dumps({"at": "2026-02-03T00:00:00Z", "from": "alice", "to": "all", "text": "live"}) + "\n"
    )
    (b / "agents" / "bob.json").write_text(json.dumps({"name": "bob", "inbox_seen": "2025-01-01T00:00:00Z"}))

    result = mod.unread(str(b), "bob")  # must not raise
    assert any(m["text"] == "live" for m in result)
