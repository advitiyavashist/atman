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


# ---- composition with T-278's agent-record lock -------------------------
#
# Added when this branch was merged onto main 5200f9d, which had gained T-278
# (every agent-record write serialised behind a per-agent flock). T-228 was
# built on f7b5d0c, which predates that, so _mark_inbox_read here was still the
# old unlocked read / json.dump / os.replace. Merging kept both sides textually
# clean while leaving the inbox as the ONE writer still racing outside the
# flock -- and the field it drops on a lost race is inbox_seen itself.
#
# T-278's own suite does not catch this: its `interleave` gates the HEARTBEAT's
# read, and a heartbeat re-reads inside its lock, so it passes whether or not
# the inbox writer is locked. The uncovered direction is the mirror image --
# the inbox reader holding a stale pre-image and clobbering what a concurrent
# writer committed in between. Verified toothed: this test FAILS against the
# unlocked _mark_inbox_read and passes with it routed through _agent_update.

class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_inbox_read_does_not_clobber_a_limit_written_while_it_scanned(board, monkeypatch):
    """A `tickets limit` landing between the inbox's read and its write must
    survive. Losing it marks a usage-limited agent as available and routes
    work to an agent that cannot answer -- the 03:36Z incident, reached
    through the inbox instead of through a heartbeat.
    """
    import threading

    monkeypatch.setenv("TICKET_AGENT", "bob")
    mod = _mod()
    b = str(board)
    mod.checkin(b, "bob")

    gated = threading.Event()
    writer_done = threading.Event()
    real_rec = mod._agent_rec
    armed = {"on": True}

    def gated_rec(bd, own):
        # Gate only the FIRST read taken by the inbox thread, and gate it
        # AFTER the read: the point is to hand back a genuinely stale
        # pre-image, which is what an unlocked implementation writes back on
        # top of the concurrent writer. Gating before the read instead makes
        # this test pass against the broken code -- it re-reads after the
        # writer commits and sees the limit it was supposed to lose.
        rec = real_rec(bd, own)
        if threading.current_thread().name == "inbox" and armed["on"]:
            armed["on"] = False
            gated.set()
            writer_done.wait(1.5)  # bounded: the fixed code blocks on the lock
        return rec

    mod._agent_rec = gated_rec
    try:
        t = threading.Thread(target=lambda: mod._mark_inbox_read(b, "bob"), name="inbox")
        t.start()
        assert gated.wait(5), "inbox thread never reached its read"
        mod.cmd_limit(_Args(agent="bob", clear=False, until="2026-09-12 19:41",
                            note="credits out"), b)
        writer_done.set()
        t.join(10)
        assert not t.is_alive(), "inbox thread hung"
    finally:
        mod._agent_rec = real_rec

    rec = json.loads(open(os.path.join(b, "agents", "bob.json")).read())
    assert "limit" in rec, "the inbox read silently discarded the limit"
    assert rec["limit"]["until"] == "2026-09-12 19:41"
    assert rec.get("inbox_seen") is not None, "the inbox's own write must land too"


# ---- FIX-FIRST (cos-opus): a future-stamped record must not blind an agent --
#
# The first version of this fix clamped nothing: _inbox_scan set the watermark
# to max(`at`) over the messages it loaded, so a single record stamped ahead of
# wall-clock dragged the agent's inbox_seen into the future and every message
# posted afterwards was "older than the watermark" and therefore invisible --
# not for a second, but for the length of the skew. T-228 exists because "a
# message is invisible to that agent forever"; closing a one-second window of
# that harm while opening an unbounded one is not a fix.
#
# The skew does not need malice: T-213 is lossless legacy import, so foreign
# records with foreign clocks are in scope, and messages.jsonl is plain JSON
# that several tools append to. A fast clock is enough.
#
# These tests use no sleep. cos-opus needed a 2.2s gap for their A/B because
# origin/main has BOTH defects and the same-second one masks this one; on this
# branch the same-second hole is already closed, so the future-stamp defect is
# isolated without one.


def _skew_board(tmp_path, mod, records):
    """A board whose messages are stamped relative to the real clock, so the
    present-day clamp is actually exercised (the fixtures above are pinned to
    2026-01-05 and never reach it)."""
    import datetime
    b = tmp_path / ".tickets"
    (b / "agents").mkdir(parents=True)
    base = datetime.datetime.now(datetime.timezone.utc)

    def stamp(delta):
        return (base + datetime.timedelta(seconds=delta)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")

    (b / "messages.jsonl").write_text("".join(
        json.dumps(dict(r, at=stamp(r["at"]))) + "\n" for r in records))
    return b, stamp


def _append(b, mod, **rec):
    with open(str(b / "messages.jsonl"), "a") as f:
        f.write(json.dumps(dict(rec, at=rec.get("at") or mod.now())) + "\n")


def test_future_stamped_message_does_not_blind_the_agent(tmp_path):
    """cos-opus's FIX-FIRST repro, as the reader sees it.

    One record two hours ahead, an inbox read, then an ordinary message: the
    ordinary message must arrive. Before the clamp this returned [].
    """
    mod = _mod()
    b, _ = _skew_board(tmp_path, mod, [
        {"at": 7200, "from": "alice", "to": "dave", "re": "",
         "text": "FROM-THE-FUTURE"},
    ])
    mod._mark_inbox_read(str(b), "dave")
    _append(b, mod, **{"from": "alice", "to": "dave", "re": "",
                       "text": "AFTER-THE-READ"})
    # Containment, not equality: this test's claim is that the agent is not
    # BLIND. Whether the future-stamped record is also redelivered is a
    # separate property with its own test below, and folding both into one
    # assertion would leave neither able to say which one broke.
    assert "AFTER-THE-READ" in [m["text"] for m in mod.unread(str(b), "dave")]


def test_future_stamp_addressed_to_someone_else_does_not_blind_a_bystander(
        tmp_path):
    """The watermark is max(`at`) over the whole file, computed BEFORE the
    addressing filter, so the skewed record does not have to be the victim's
    mail -- or anyone's. One bad record blinds every agent that reads its
    inbox after it, which is why this is a fleet-wide failure and not a
    per-message one."""
    mod = _mod()
    b, _ = _skew_board(tmp_path, mod, [
        {"at": 7200, "from": "alice", "to": "carol", "re": "",
         "text": "NOT-FOR-DAVE"},
    ])
    mod._mark_inbox_read(str(b), "dave")
    _append(b, mod, **{"from": "alice", "to": "dave", "re": "",
                       "text": "AFTER-THE-READ"})
    assert [m["text"] for m in mod.unread(str(b), "dave")] == ["AFTER-THE-READ"]


def test_future_stamped_message_is_delivered_exactly_once(tmp_path):
    """The other half of the fix, and the half a bare clamp would get wrong.

    Clamping the watermark alone leaves the future-stamped message permanently
    above it, so it is redelivered on every poll for the length of the skew --
    the wake storm the boundary-identity multiset exists to prevent, and
    exactly what origin/main does today (three polls, three deliveries).
    The identity set covers everything at or ABOVE the watermark, not only the
    watermark second, so the message is delivered once and then stays quiet.
    """
    mod = _mod()
    b, _ = _skew_board(tmp_path, mod, [
        {"at": 7200, "from": "alice", "to": "dave", "re": "",
         "text": "FROM-THE-FUTURE"},
    ])
    polls = []
    for _ in range(3):
        polls.append([m["text"] for m in mod.unread(str(b), "dave")])
        mod._mark_inbox_read(str(b), "dave")
    assert polls == [["FROM-THE-FUTURE"], [], []], (
        "future-stamped mail must be delivered once, not on every poll: %r"
        % (polls,))


def test_a_watermark_already_in_the_future_heals_on_the_next_read(tmp_path):
    """An agent poisoned before this fix shipped must recover by itself.

    The clamp is applied when inbox_seen is READ, not only when it is written,
    precisely so that a record already carrying a future watermark does not
    stay blind until its skew expires. Clamping down can re-show a message
    that was already delivered; that is the safe direction, and it settles
    after one poll.
    """
    mod = _mod()
    import datetime
    b, _ = _skew_board(tmp_path, mod, [])
    poisoned = (datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(seconds=7200)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (b / "agents" / "dave.json").write_text(
        json.dumps({"name": "dave", "inbox_seen": poisoned}))
    _append(b, mod, **{"from": "alice", "to": "dave", "re": "",
                       "text": "AFTER-THE-POISONING"})
    assert [m["text"] for m in mod.unread(str(b), "dave")] == [
        "AFTER-THE-POISONING"]
    rec = json.loads((b / "agents" / "dave.json").read_text())
    assert rec["inbox_seen"] == poisoned, "read-side clamp must not rewrite yet"
    mod._mark_inbox_read(str(b), "dave")
    rec = json.loads((b / "agents" / "dave.json").read_text())
    assert rec["inbox_seen"] <= mod.now(), (
        "the watermark must be back in the present after a read, got %r"
        % rec["inbox_seen"])


def test_retained_ids_are_bounded_by_the_skew_not_by_history(tmp_path):
    """The disclosed cost of the above, pinned rather than asserted in prose.

    Widening the identity set from "the watermark second" to "at or above the
    watermark" means future-stamped records stay in it until the clock reaches
    them. What must NOT happen is ordinary history joining them.
    """
    mod = _mod()
    records = [{"at": -300 + i, "from": "alice", "to": "dave", "re": "",
                "text": "old %d" % i} for i in range(50)]
    records += [{"at": 7200 + i, "from": "alice", "to": "dave", "re": "",
                 "text": "ahead %d" % i} for i in range(3)]
    b, _ = _skew_board(tmp_path, mod, records)
    mod._mark_inbox_read(str(b), "dave")
    rec = json.loads((b / "agents" / "dave.json").read_text())
    assert len(rec.get("inbox_seen_ids", [])) == 3, (
        "only the 3 skewed records should need identities, got %r"
        % rec.get("inbox_seen_ids"))


def test_master_stuck_wake_is_not_blinded_by_a_future_stamp(tmp_path):
    """The master's stuck-message wake reads the same field with the same
    comparison, so it inherited the same blindness -- and it is the single
    worst place on this board to lose a message: a "stuck:" that does not
    wake the master is an agent parked until someone notices by hand.

    Driven through the real pending_work() rather than through the predicate,
    because the defect here is in which `since` the caller passes, and a test
    that calls _is_unread directly cannot see that.
    """
    mod = _mod()
    import datetime
    b, _ = _skew_board(tmp_path, mod, [])
    (b / "master.json").write_text(json.dumps({"owner": "boss", "cos": ""}))
    poisoned = (datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(seconds=7200)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (b / "agents" / "boss.json").write_text(
        json.dumps({"name": "boss", "inbox_seen": poisoned}))
    _append(b, mod, **{"from": "alice", "to": "", "re": "",
                       "text": "stuck: need a decision"})
    out = mod.pending_work(str(b), "boss")
    assert out.get("stuck_messages"), (
        "a 'stuck' posted after a future-stamped record must still wake the "
        "master, got %r" % (out,))
