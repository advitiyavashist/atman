"""T-1486: the app's write routes and the two read additions behind the one-screen view.

POST /api/v1/ticket/{id}/accept, /reject and /assign are the CLI functions
behind `atm accept`, `atm reject` and `atm assign --owner` (accept_ticket,
reject_ticket, assign_ticket), so the gate is one function: the operator can
no more accept their own work or a stale sha here than at the terminal. The
200 body is what was recorded, and that is what the app's result line shows.

Reads: GET /api/v1/thread?re=<id> (the ticket panel's thread) and the two new
needs-you kinds (`review`, `seat`) that make the strip's count one field.

Fixture boards only, same harness as T-1103 (tmp HOME, no session env).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("t1103_for_t1486", HERE / "test_t1103_app_api.py")
t1103 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(t1103)

OP = t1103.OP
HEAD = t1103.HEAD
HEAD2 = "5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b"
env = t1103.env  # the fixture, re-exported for pytest
cli, msg, seat, stamp, ticket, valid, write = (
    t1103.cli, t1103.msg, t1103.seat, t1103.stamp, t1103.ticket, t1103.valid, t1103.write)


def in_review(board, tid, owner, head=HEAD, notes="schema.sql; 6 tests pass"):
    ticket(board, tid, "Review me %s" % tid, status="review", owner=owner, review_head=head,
           review_at=stamp(5), commit="%s/branch@%s" % (owner, head), branch="%s/branch" % owner,
           notes=[{"by": owner, "at": stamp(5), "text": "REVIEW: " + notes}])


def read(board, tid):
    return json.loads((board / (tid + ".json")).read_text())


# --- accept -------------------------------------------------------------------

def test_accept_binds_to_the_full_submitted_head_and_records_the_operator(env):
    in_review(env.alpha, "T-20", "coder")
    srv = env.serve()
    # the same refusals as `atm accept`, in its words
    st, out = srv.post("/api/v1/ticket/T-20/accept", {"sha": HEAD[:7], "notes": "looks right"})
    assert st == 409 and "full 40-character" in out["error"], out
    st, out = srv.post("/api/v1/ticket/T-20/accept", {"sha": HEAD, "notes": ""})
    assert st == 409 and "notes" in out["error"], out
    st, out = srv.post("/api/v1/ticket/T-20/accept", {"sha": HEAD2, "notes": "wrong head"})
    assert st == 409 and "not the submitted review head" in out["error"], out
    assert "review_events" not in read(env.alpha, "T-20")
    # the real thing: the body is the recorded event, not the request
    st, out = srv.post("/api/v1/ticket/T-20/accept", {"sha": HEAD, "notes": "read schema.sql; tests pass"})
    assert st == 200, out
    valid("action-response.json", out)
    assert out["action"] == "accept" and out["ticket"] == "T-20" and out["by"] == OP and out["sha"] == HEAD
    assert out["owner"] == "coder" and out["project"] == "alpha"
    ev = read(env.alpha, "T-20")["review_events"][-1]
    assert ev == {"kind": "accept", "by": OP, "at": ev["at"], "sha": HEAD, "notes": "read schema.sql; tests pass"}
    assert out["at"] == ev["at"]
    # the drill-down now shows the verdict as applying at this head
    tk = srv.get("/api/v1/ticket/T-20")[1]
    assert [v for v in tk["review"]["verdicts"] if v["kind"] == "accept"][0]["applies"] is True
    # a second accept at the same head is still an accept (the CLI allows it); a stale one is not
    st, out = srv.post("/api/v1/ticket/T-20/accept", {"sha": HEAD2, "notes": "stale"})
    assert st == 409


def test_author_cannot_accept_their_own_work_through_the_app(env):
    in_review(env.alpha, "T-21", OP)
    srv = env.serve()
    st, out = srv.post("/api/v1/ticket/T-21/accept", {"sha": HEAD, "notes": "my own"})
    assert st == 409 and "cannot accept their own work" in out["error"], out
    assert "review_events" not in read(env.alpha, "T-21")


def test_accept_refuses_unknown_fields_tickets_and_the_read_only_app(env):
    in_review(env.alpha, "T-22", "coder")
    srv = env.serve()
    st, out = srv.post("/api/v1/ticket/T-22/accept", {"sha": HEAD, "notes": "x", "latest": True})
    assert st == 400 and "accepts only" in out["error"]
    st, out = srv.post("/api/v1/ticket/T-99/accept", {"sha": HEAD, "notes": "x"})
    assert st == 404
    st, out = srv.post("/api/v1/ticket/T-22/accept", {"sha": HEAD, "notes": "x"}, token=False)
    assert st == 400 and "token" in out["error"]
    ro = env.serve(operator="")
    st, out = ro.post("/api/v1/ticket/T-22/accept", {"sha": HEAD, "notes": "x"})
    assert st == 400 and out["error"].startswith("Read-only. Start with atm ui --operator <name> to act.")
    assert "review_events" not in read(env.alpha, "T-22")


# --- reject -------------------------------------------------------------------

def test_reject_returns_the_ticket_to_its_author_as_claimed(env):
    in_review(env.alpha, "T-23", "coder")
    srv = env.serve()
    st, out = srv.post("/api/v1/ticket/T-23/reject", {"sha": HEAD, "reason": ""})
    assert st == 409 and "reason" in out["error"]
    st, out = srv.post("/api/v1/ticket/T-23/reject", {"sha": HEAD[:12], "reason": "edges overlap at 390"})
    assert st == 200, out
    valid("action-response.json", out)
    assert out["action"] == "reject" and out["by"] == OP and out["returned_to"] == "coder"
    assert out["owner"] == "coder" and out["status"] == "claimed"
    t = read(env.alpha, "T-23")
    assert t["status"] == "claimed" and t["owner"] == "coder"
    assert t["review_events"][-1]["kind"] == "reject" and t["review_events"][-1]["reason"] == "edges overlap at 390"
    assert any("REJECT" in n["text"] and "returned to coder" in n["text"] for n in t["notes"])
    task = [m for m in t1103.lines(env.alpha) if m.get("re") == "T-23" and m.get("to") == "coder"]
    assert task and "rejected" in task[-1]["text"] and task[-1]["from"] == OP


# --- assign -------------------------------------------------------------------

def test_assign_claims_on_the_seats_behalf_and_never_picks_one(env):
    ticket(env.alpha, "T-24", "Free ticket")
    srv = env.serve()
    st, out = srv.post("/api/v1/ticket/T-24/assign", {"owner": ""})
    assert st == 400 and "pick a seat" in out["error"]
    st, out = srv.post("/api/v1/ticket/T-24/assign", {"owner": "nobody"})
    assert st == 400 and "not a registered seat" in out["error"]
    st, out = srv.post("/api/v1/ticket/T-24/assign", {"owner": "scout", "notes": "scout is free"})
    assert st == 200, out
    valid("action-response.json", out)
    assert out["action"] == "assign" and out["owner"] == "scout" and out["by"] == OP
    assert out["status"] == "claimed" and any("claimed for scout" in c for c in out["changed"])
    t = read(env.alpha, "T-24")
    assert t["status"] == "claimed" and t["owner"] == "scout"
    note = t["notes"][-1]
    assert note["by"] == OP and note["text"].startswith("assign: claimed for scout") and "scout is free" in note["text"]
    # a seat that already holds active work is reserved, not double-claimed (T-979)
    ticket(env.alpha, "T-25", "Second free ticket")
    st, out = srv.post("/api/v1/ticket/T-25/assign", {"owner": "scout"})
    assert st == 200 and read(env.alpha, "T-25")["reserved_for"] == "scout" and read(env.alpha, "T-25")["status"] == "open"
    # a dependency that is not released refuses, in the CLI's words
    ticket(env.alpha, "T-26", "Waits on open work", deps=["T-3"])
    st, out = srv.post("/api/v1/ticket/T-26/assign", {"owner": "dave"})
    assert st == 409 and out["ok"] is False and out["error"]
    assert read(env.alpha, "T-26")["status"] == "open"


# --- the CLI is the same function --------------------------------------------

def test_cli_accept_reject_assign_still_print_and_refuse_the_same(env):
    in_review(env.alpha, "T-27", "coder")
    r = cli(env, env.alpha, "accept", "T-27", "--sha", HEAD[:7], "--notes", "ok")
    assert r.returncode != 0 and "full 40-character" in (r.stderr + r.stdout)
    r = cli(env, env.alpha, "accept", "T-27", "--sha", HEAD, "--notes", "ok")
    assert r.returncode == 0 and "T-27 accepted %s by" % HEAD in r.stdout, r.stderr
    in_review(env.alpha, "T-28", "coder")
    r = cli(env, env.alpha, "reject", "T-28", "--sha", HEAD, "--reason", "no")
    assert r.returncode == 0 and "returned to coder as claimed for revision" in r.stdout, r.stderr
    assert read(env.alpha, "T-28")["status"] == "claimed"
    ticket(env.alpha, "T-29", "Free ticket")
    r = cli(env, env.alpha, "assign", "T-29", "--owner", "dave")
    assert r.returncode == 0 and r.stdout.strip() == "T-29: claimed for dave", r.stderr


# --- thread?re= -----------------------------------------------------------------

def test_thread_re_filter_is_every_message_about_the_ticket(env):
    msg(env.alpha, id="m20", at=stamp(2), **{"from": "rev"}, to="coder", re="T-3", text="looked at T-3")
    srv = env.serve()
    st, out = srv.get("/api/v1/thread?re=T-3")
    assert st == 200, out
    valid("thread.json", out)
    assert out["re"] == "T-3" and out["with"] == ""
    assert [m["id"] for m in out["messages"]] == ["m3", "m20"]
    assert all(m["re"] == "T-3" for m in out["messages"])
    assert srv.get("/api/v1/thread?re=T-99")[0] == 404
    assert srv.get("/api/v1/thread?re=junk")[0] == 400
    plain = srv.get("/api/v1/thread")[1]
    assert plain["re"] == "" and plain["with"] == "planner"
    valid("thread.json", plain)


# --- needs-you: review and seat items -------------------------------------------

def test_needs_you_lists_reviews_without_a_verdict_and_seats_that_cannot_run(env):
    in_review(env.alpha, "T-30", "coder", notes="rollups table added")
    in_review(env.alpha, "T-31", OP)
    ticket(env.alpha, "T-32", "Held by a limited seat", status="claimed", owner="rev", claimed_at=stamp(50))
    srv = env.serve()
    out = srv.get("/api/v1/needs-you")[1]
    valid("needs-you.json", out)
    by_id = {i["id"]: i for i in out["items"]}
    assert out["count"] == len(out["items"])
    r = by_id["T-30"]
    assert r["kind"] == "review" and r["label"] == "review submitted" and r["state"] == "asked"
    assert r["from"] == "coder" and r["re"] == "T-30" and r["text"] == "rollups table added"
    assert r["why"] == "waiting for your accept"
    assert by_id["T-31"]["why"] == "you wrote this; another seat has to accept it"
    s = by_id["seat:rev"]
    assert s["kind"] == "seat" and s["label"] == "seat needs attention" and s["re"] == "T-32"
    assert s["why"] == "limited until 17:40" and "T-32 is waiting on it" in s["text"]
    assert "seat:" + OP not in by_id
    # a verdict at the head takes the review out of the queue; a stale one does not
    assert srv.post("/api/v1/ticket/T-30/accept", {"sha": HEAD, "notes": "fine"})[0] == 200
    ids = {i["id"] for i in srv.get("/api/v1/needs-you")[1]["items"]}
    assert "T-30" not in ids and "T-31" in ids
    # prose asks are untouched
    assert "m4" in ids and "m2" in ids
