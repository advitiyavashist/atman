"""T-1111: review.verified is a structured accept at the current head.

A done ticket whose only evidence is a prose 'merged' or 'ACCEPT' note must
stay unverified. The work node carries an unaccepted blocker, and a dependent
stays gated. A structured accept (or merge_record) bound to the review head
is the only verification.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import work_view  # noqa: E402

HEAD = "a" * 40
PIN = "def5678"


def _done(notes=(), events=None, merge_record=None, commit="br@" + PIN, **kw):
    t = {
        "id": "T-1",
        "title": "done work",
        "status": "done",
        "role": "backend",
        "owner": "alice",
        "commit": commit,
        "review_head": HEAD,
        "review_at": "2026-09-19T00:00:00Z",
        "notes": list(notes),
        "deps": [],
    }
    if events:
        t["review_events"] = events
    if merge_record:
        t["merge_record"] = merge_record
    t.update(kw)
    return t


def _payload(tickets, msgs=()):
    graph = {"nodes": [{"id": t["id"]} for t in tickets], "edges": [], "roots": []}
    w = work_view.work_payload(tickets, graph, list(msgs), objective={"text": "o"})
    return w, {n["id"]: n for n in w["nodes"]}


def _unaccepted(node):
    return [b for b in (node.get("blockers") or []) if b["kind"] == "unaccepted"]


def test_prose_merged_note_is_not_verified_and_is_unaccepted():
    t = _done(notes=[{
        "by": "planner", "at": "2026-09-19T00:03:00Z",
        "text": "merged into main as %s (atm merge; pinned %s)" % (HEAD, PIN),
    }])
    r = work_view.review_of(t)
    assert r["verified"] is False
    assert work_view.structured_accept(t) is False
    assert work_view.structured_merge(t) is False
    child = {"id": "T-2", "status": "open", "deps": ["T-1"], "notes": []}
    _, by = _payload([t, child])
    assert by["T-1"]["review"]["verified"] is False
    assert by["T-1"]["unverified"] is True
    chips = _unaccepted(by["T-1"])
    assert chips and chips[0]["kind"] == "unaccepted"
    assert chips[0]["text"] == "done, not accepted"
    assert [b["kind"] for b in by["T-2"]["blockers"]] == ["dep_unaccepted"]
    assert work_view.unreleased_dep_id(child, [t, child]) == "T-1"
    assert "without verification" in work_view.refuse_unreleased_reason(child, [t, child])


def test_prose_accept_note_is_not_verified_and_is_unaccepted():
    t = _done(notes=[{
        "by": "rev", "at": "2026-09-19T00:03:00Z",
        "text": "ACCEPT %s -- looks good" % HEAD,
    }])
    r = work_view.review_of(t)
    assert r["verified"] is False
    assert "Accepted" not in (r["label"] or "")
    child = {"id": "T-2", "status": "open", "deps": ["T-1"], "notes": []}
    _, by = _payload([t, child])
    assert by["T-1"]["unverified"] is True
    chips = _unaccepted(by["T-1"])
    assert chips and chips[0]["kind"] == "unaccepted"
    assert [b["kind"] for b in by["T-2"]["blockers"]] == ["dep_unaccepted"]
    assert work_view.unreleased_dep_id(child, [t, child]) == "T-1"


def test_structured_accept_at_head_is_verified():
    t = _done(events=[{
        "kind": "accept", "by": "rev", "at": "2026-09-19T00:04:00Z",
        "sha": HEAD, "notes": "ok",
    }])
    assert work_view.review_of(t)["verified"] is True
    assert work_view.structured_accept(t) is True
    _, by = _payload([t])
    assert by["T-1"]["unverified"] is False
    assert _unaccepted(by["T-1"]) == []


def test_structured_merge_at_head_is_verified():
    t = _done(merge_record=work_view.make_merge_record(
        "master", "2026-09-19T00:05:00Z", "b" * 40, pin=HEAD, trunk="main"))
    assert work_view.review_of(t)["verified"] is True
    assert work_view.structured_merge(t) is True
    _, by = _payload([t])
    assert by["T-1"]["unverified"] is False
    assert _unaccepted(by["T-1"]) == []
