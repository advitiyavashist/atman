"""T-1481: the plan's done-unverified count agrees with the node label.

Found in the #298 review: after ``done --force`` then ``accept --sha``, the
counts row said ``0 done unverified`` while the node in the same plan read
``done, not accepted``. ``review_of()["verified"]`` counted any accept event
that matched the artifact, even one not bound to ``review_head``, so
``node.unverified`` stayed false and ``blockers_of`` never named the gap.

An accept counts only when bound to the review head (``structured_accept``).
Also: the summary's next owner is ``none yet`` rather than ``@?`` (UI), and
the gate note says reopen, because re-accepting does not bind.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tickets as tk  # noqa: E402
from src.ticket_board import work_view as wv  # noqa: E402

FULL = "6cf57003447931cf822f50ee8aeca2389700507b"


def stamp(mins_ago=0):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - mins_ago * 60))


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def ticket(board, tid, title, **kw):
    write(board / (tid + ".json"),
          dict(dict(id=tid, title=title, status="open", role="backend", deps=[], notes=[], priority=2), **kw))


def _board(tmp_path, *tids_titles):
    board = tmp_path / "proj" / ".tickets"
    board.mkdir(parents=True)
    for name in ("alice", "bob"):
        write(board / "agents" / (name + ".json"), {"owner": name, "seen": stamp(1)})
    write(board / "workforce.json", {"alice": {"harness": "codex"}, "bob": {"harness": "claude"}})
    for tid, title, extra in tids_titles:
        ticket(board, tid, title, **extra)
    return board


def _cli(board, agent, *args):
    env = os.environ.copy()
    env["TICKETS_DIR"] = str(board)
    env["TICKET_AGENT"] = agent
    for k in ("TICKET_SEAT", "TICKETS_WATCH_PINNED", "TICKET_SESSION_ID"):
        env.pop(k, None)
    return subprocess.run([sys.executable, str(ROOT / "tickets.py"), *args],
                          cwd=str(ROOT), env=env, capture_output=True, text=True, check=False)


def _node(plan, tid):
    return next(n for n in plan["nodes"] if n["id"] == tid)


def test_short_sha_accept_without_review_head_is_unverified(tmp_path):
    """A 7-char accept event that matches the commit is not verification."""
    board = _board(tmp_path, ("T-001", "Write CSV statistics", dict(
        status="done", owner="alice", commit=FULL,
        review_events=[{"kind": "accept", "by": "bob", "at": stamp(5), "sha": FULL[:7], "notes": "ok"}],
    )), ("T-002", "Consume statistics", dict(deps=["T-001"])))
    t = tk.load(str(board), "T-001")
    assert not t.get("review_head")
    review = wv.review_of(t)
    assert review["latest"] and review["latest"]["kind"] == "ACCEPT"
    assert review["latest"]["applies"] == "exact", "the label may still name the accept"
    assert review["verified"] is False, "verified must agree with structured_accept"
    assert wv.structured_accept(t) is False
    assert "T-001" in wv.unverified_done_ids([t])

    plan = tk.ui_plan(str(board), "normal")
    node = _node(plan, "T-001")
    assert node["phase"] == "done"
    assert node["unverified"] is True
    assert node["accepted"] is False
    assert node["status_label"] == "done, not accepted"
    assert plan["counts"]["done"] == 1
    assert plan["counts"]["done_unverified"] == 1, plan["counts"]
    kinds = [b["kind"] for b in node["blockers"]]
    assert kinds == ["unaccepted"], node["blockers"]
    assert "not bound to a review head" in node["blockers"][0]["text"]
    assert "@bob" in node["blockers"][0]["text"]
    assert "atm reopen T-001" in node["blockers"][0]["cmd"]
    # The dependent names the same gap, with the same reopen path.
    child = _node(plan, "T-002")
    assert [b["kind"] for b in child["blockers"]] == ["dep_unaccepted"]
    assert child["blockers"][0]["cmd"] == node["blockers"][0]["cmd"]


def test_done_force_then_accept_via_real_cli_counts_as_unverified(tmp_path):
    """CLI-seeded repro: done --force then accept --sha leaves no review_head."""
    board = _board(tmp_path, ("T-001", "Write CSV statistics", {}),
                   ("T-002", "Consume statistics", dict(deps=["T-001"])))
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True).strip()
    assert len(head) == 40
    for agent, args in (("alice", ("claim", "T-001")),
                        ("alice", ("done", "T-001", "--force", "--notes", "shipped")),
                        ("bob", ("accept", "T-001", "--sha", head, "--notes", "ok"))):
        r = _cli(board, agent, *args)
        assert r.returncode == 0, r.stdout + r.stderr
    raw = json.loads((board / "T-001.json").read_text())
    assert not raw.get("review_head")

    plan = tk.ui_plan(str(board), "normal")
    node = _node(plan, "T-001")
    tv = tk.ui_ticket(str(board), "T-001", "boss", "normal")
    assert tv["accepted"] is False
    assert tv["status_label"] == "done, not accepted"
    assert node["unverified"] is True, "node and ticket view must agree"
    assert node["status_label"] == tv["status_label"]
    assert plan["counts"]["done_unverified"] == 1, plan["counts"]
    assert plan["counts"]["done"] == 1
    assert [b["kind"] for b in node["blockers"]] == ["unaccepted"]
    assert "accept by @bob on %s is not bound to a review head" % head[:7] in node["blockers"][0]["text"]
    # The dependent's block note tells the operator to reopen, not to accept again.
    child = json.loads((board / "T-002.json").read_text())
    gate = [n.get("text") or "" for n in child.get("notes") or [] if "without verification" in (n.get("text") or "")]
    assert gate, child.get("notes")
    assert gate[-1] == wv.unverified_block_reason("T-001")
    assert "reopen" in gate[-1] and "accept it" not in gate[-1]

    # The suggested path binds a head and the counts follow.
    for agent, args in (("bob", ("reopen", "T-001", "--notes", "bind a review head")),
                        ("alice", ("claim", "T-001")),
                        ("alice", ("review", "T-001", "--force", "--notes", "paths"))):
        r = _cli(board, agent, *args)
        assert r.returncode == 0, r.stdout + r.stderr
    new_head = json.loads((board / "T-001.json").read_text())["review_head"]
    r = _cli(board, "bob", "accept", "T-001", "--sha", new_head, "--notes", "bound")
    assert r.returncode == 0, r.stdout + r.stderr
    raw3 = json.loads((board / "T-001.json").read_text())
    assert wv.structured_accept(raw3)
    assert wv.review_of(raw3)["verified"] is True
    plan2 = tk.ui_plan(str(board), "normal")
    node2 = _node(plan2, "T-001")
    assert node2["unverified"] is False and not node2["blockers"]
    assert plan2["counts"]["done_unverified"] == 0
    assert tk.ui_ticket(str(board), "T-001", "boss", "normal")["review"]["verified"] is True


def test_next_owner_is_empty_when_nobody_is_reserved(tmp_path):
    """summary.next.who is "" (the app renders "none yet"), never "?"."""
    board = _board(tmp_path, ("T-001", "Write CSV statistics", {}),
                   ("T-002", "Consume statistics", dict(deps=["T-001"])))
    plan = tk.ui_plan(str(board), "normal")
    nxt = plan["summary"]["next"]
    assert nxt and nxt["id"] == "T-001"
    assert nxt["who"] == "" and nxt["who_kind"] == ""
    assert "?" not in json.dumps(nxt)


def test_gate_note_says_reopen_and_legacy_wording_still_recognised():
    reason = wv.unverified_block_reason("T-002")
    assert reason.endswith("reopen it, then review and accept")
    assert "accept it or reopen" not in reason
    legacy = "T-002 marked done without verification; accept it or reopen"
    for text in (reason, legacy):
        assert wv.is_live_unverified_gate_note(text, "T-002") is True
        assert wv.is_live_unverified_gate_note(text) is True
        assert wv.is_live_unverified_gate_note("resolved: " + text, "T-002") is False
    child = {"notes": [{"by": "boss", "text": legacy}, {"by": "boss", "text": reason},
                       {"by": "alice", "text": "unrelated"}]}
    wv.drop_unverified_gate_notes(child, "T-002")
    assert [n["text"] for n in child["notes"]] == ["unrelated"]
