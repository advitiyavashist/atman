"""T-228: a message posted in the same second as an agent's inbox check was
invisible to that agent forever.

`now()` stamps whole seconds and `unread()` filtered on a strict `at > since`,
so a message whose `at` tied the reader's `inbox_seen` was excluded on that
read and on every later one -- it sat on disk while the agent was told "inbox
empty". This is the same family as T-212's D1 and T-230: the board returning a
confident wrong answer rather than a missing one, which is what makes it
expensive -- nobody goes looking.

These are READER-level tests (they assert what unread()/`tickets inbox`
actually hand back), per the standing lesson from the T-212 review that a
file-level test proves nothing about what an agent sees.

The naive repair -- `>=` -- is what these tests exist to rule out as much as
the original bug: it redelivers the boundary message on every poll forever,
which on a board whose watch loops wake on unread mail is a wake storm.
"""
import importlib.util
import json
import os
import time

import pytest

from test_wakeup import board, run  # noqa: F401

_TICKETS_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tickets.py")


def _mod():
    spec = importlib.util.spec_from_file_location("tickets_t228", _TICKETS_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _board(tmp_path, since, msgs, seen_ids=None):
    """A board pinned to an exact `since`, so the boundary case is tested
    deterministically instead of by racing the wall clock."""
    b = tmp_path / ".tickets"
    (b / "agents").mkdir(parents=True)
    rec = {"name": "bob", "inbox_seen": since}
    if seen_ids is not None:
        rec["inbox_seen_ids"] = seen_ids
    (b / "agents" / "bob.json").write_text(json.dumps(rec))
    (b / "messages.jsonl").write_text(
        "".join(json.dumps(m) + "\n" for m in msgs))
    return b


TIE = "2026-01-05T12:00:00Z"


def test_message_tying_the_read_second_is_delivered(tmp_path):
    """The defect itself, stated as the reader sees it."""
    mod = _mod()
    b = _board(tmp_path, TIE, [
        {"at": TIE, "from": "alice", "to": "bob", "re": "", "text": "SAME-SECOND-DM"},
    ])
    assert [m["text"] for m in mod.unread(str(b), "bob")] == ["SAME-SECOND-DM"]


def test_boundary_message_is_delivered_exactly_once(tmp_path):
    """The other half of the contract: fixing the drop must not create a
    wake storm. This is why `>=` on its own was rejected."""
    mod = _mod()
    b = _board(tmp_path, TIE, [
        {"at": TIE, "from": "alice", "to": "bob", "re": "", "text": "SAME-SECOND-DM"},
    ])
    assert len(mod.unread(str(b), "bob")) == 1
    mod._mark_inbox_read(str(b), "bob", mod._inbox_scan(str(b), "bob"))
    for _ in range(5):
        assert mod.unread(str(b), "bob") == [], "redelivered -- wake storm"


def test_a_later_message_in_that_same_second_still_arrives(tmp_path):
    """The case that makes identity necessary rather than just a bigger
    timestamp: after the boundary message is consumed, a *different* message
    stamped with that very same second must still be delivered."""
    mod = _mod()
    b = _board(tmp_path, TIE, [
        {"at": TIE, "from": "alice", "to": "bob", "re": "", "text": "FIRST"},
    ])
    mod._mark_inbox_read(str(b), "bob", mod._inbox_scan(str(b), "bob"))
    with open(os.path.join(str(b), "messages.jsonl"), "a") as f:
        f.write(json.dumps({"at": TIE, "from": "alice", "to": "bob",
                            "re": "", "text": "SECOND"}) + "\n")
    assert [m["text"] for m in mod.unread(str(b), "bob")] == ["SECOND"]


def test_two_identical_messages_in_one_second_are_both_delivered(tmp_path):
    """Identity is content-derived, so two byte-identical messages collide.
    They are counted as a multiset for exactly this reason: consuming one
    must not silently swallow the other."""
    mod = _mod()
    dup = {"at": TIE, "from": "alice", "to": "bob", "re": "", "text": "ping"}
    b = _board(tmp_path, TIE, [dict(dup), dict(dup)])
    assert len(mod.unread(str(b), "bob")) == 2
    # consume only the first, as a reader that saw one copy would have
    scan = mod._inbox_scan(str(b), "bob")
    mod._mark_inbox_read(str(b), "bob", (scan[0][:1], scan[1], [mod._msg_id(dup)]))
    assert [m["text"] for m in mod.unread(str(b), "bob")] == ["ping"]


def test_watermark_never_advances_past_what_was_observed(tmp_path):
    """The reason the stamp is the newest message seen and not now():
    stamping wall-clock time reopens this same hole one second later, for
    anything posted between the read and the stamp."""
    mod = _mod()
    b = _board(tmp_path, TIE, [
        {"at": TIE, "from": "alice", "to": "bob", "re": "", "text": "m"},
    ])
    mod._mark_inbox_read(str(b), "bob", mod._inbox_scan(str(b), "bob"))
    rec = json.loads((b / "agents" / "bob.json").read_text())
    assert rec["inbox_seen"] == TIE, (
        "watermark ran ahead of the newest observed message: %r" % rec["inbox_seen"])


def test_message_arriving_between_the_read_and_the_mark_is_not_lost(tmp_path):
    """The race the old code turned into permanent loss: unread() is
    computed, a message lands, and only then is the watermark written."""
    mod = _mod()
    b = _board(tmp_path, TIE, [
        {"at": TIE, "from": "alice", "to": "bob", "re": "", "text": "FIRST"},
    ])
    scan = mod._inbox_scan(str(b), "bob")          # reader computes its view
    later = "2026-01-05T12:00:01Z"
    with open(os.path.join(str(b), "messages.jsonl"), "a") as f:  # ...then this lands
        f.write(json.dumps({"at": later, "from": "alice", "to": "bob",
                            "re": "", "text": "SLIPPED-IN"}) + "\n")
    mod._mark_inbox_read(str(b), "bob", scan)      # mark only what was seen
    assert [m["text"] for m in mod.unread(str(b), "bob")] == ["SLIPPED-IN"]


def test_boundary_ids_do_not_grow_with_history(tmp_path):
    """The stored identity list is bounded by one second of traffic, not by
    the size of the board's history -- otherwise this fix would trade a
    silent drop for an agent record that grows without limit."""
    mod = _mod()
    msgs = [{"at": "2026-01-05T11:59:%02dZ" % i, "from": "alice", "to": "bob",
             "re": "", "text": "old %d" % i} for i in range(50)]
    msgs.append({"at": TIE, "from": "alice", "to": "bob", "re": "", "text": "tie"})
    b = _board(tmp_path, "", msgs)
    mod._mark_inbox_read(str(b), "bob", mod._inbox_scan(str(b), "bob"))
    rec = json.loads((b / "agents" / "bob.json").read_text())
    assert len(rec.get("inbox_seen_ids", [])) == 1, (
        "expected only the boundary second to need identities, got %r"
        % rec.get("inbox_seen_ids"))


def test_end_to_end_same_second_dm_through_the_real_cli(board):
    """Reader-level through the actual CLI a real agent runs, not the
    in-process module: read the inbox, then DM in the same second."""
    run(board, "join", "alice", agent="alice")
    run(board, "join", "bob", agent="bob")
    run(board, "inbox", agent="bob")
    seen = json.loads((board / "agents" / "bob.json").read_text())["inbox_seen"]
    r = run(board, "msg", "SAME-SECOND-VIA-CLI", "--to", "bob", agent="alice")
    assert r.returncode == 0, r.stderr
    posted_at = json.loads(
        (board / "messages.jsonl").read_text().strip().splitlines()[-1])["at"]
    if posted_at != seen:
        pytest.skip("clock rolled between the read and the post (%s vs %s); "
                    "the deterministic cases above cover the tie" % (seen, posted_at))
    out = run(board, "inbox", agent="bob").stdout
    assert "SAME-SECOND-VIA-CLI" in out, out


def test_stuck_message_in_the_read_second_still_wakes_the_master(tmp_path):
    """The same watermark is read by the master's stuck-message wake, which
    had the identical strict-`>` hole. A 'stuck' posted in the second the
    master last read its mail is the one it can least afford to miss."""
    mod = _mod()
    b = _board(tmp_path, TIE, [
        {"at": TIE, "from": "alice", "to": "", "re": "", "text": "stuck: need a decision"},
    ])
    rec = json.loads((b / "agents" / "bob.json").read_text())
    remaining = mod._seen_counts(rec.get("inbox_seen_ids"))
    hits = [m for m in mod.load_messages(str(b))
            if m.get("from") != "bob" and mod._is_unread(m, TIE, remaining)
            and str(m.get("text", "")).lower().startswith(("stuck", "blocked"))]
    assert len(hits) == 1, "a same-second 'stuck' must still wake the master"
