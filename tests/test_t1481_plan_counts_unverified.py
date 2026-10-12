"""T-1481: the plan's done-unverified count agrees with the node label.

Found in the #298 review: after ``done --force`` then ``accept --sha``, the
counts row said ``0 done unverified`` while the node in the same plan read
``done, not accepted``. ``review_of()["verified"]`` counts an accept event
that matches the artifact even when it is not bound to ``review_head``.

CEO decision: keep that (legacy short-sha accepts stay verified, off the
active graph) and make the LABEL agree: such a node or ticket reads
``done, accepted (legacy short sha)``. It is still not ``structured_accept``,
so dependents stay gated. Also: the summary's next owner is ``none yet``
rather than ``@?`` (UI).
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


def test_short_sha_accept_without_review_head_is_labelled_legacy(tmp_path):
    """A 7-char accept event that matches the commit counts, and says it is legacy."""
    board = _board(tmp_path, ("T-001", "Write CSV statistics", dict(
        status="done", owner="alice", commit=FULL,
        review_events=[{"kind": "accept", "by": "bob", "at": stamp(5), "sha": FULL[:7], "notes": "ok"}],
    )), ("T-002", "Consume statistics", dict(deps=["T-001"])))
    t = tk.load(str(board), "T-001")
    assert not t.get("review_head")
    review = wv.review_of(t)
    assert review["latest"] and review["latest"]["kind"] == "ACCEPT"
    assert review["verified"] is True, "main's _review_verified is kept"
    assert wv.structured_accept(t) is False
    assert wv.legacy_accept(t) is True
    assert "T-001" not in wv.unverified_done_ids([t])

    plan = tk.ui_plan(str(board), "normal")
    node = _node(plan, "T-001")
    assert node["phase"] == "done"
    assert node["unverified"] is False
    assert node["accepted"] is False
    assert node["legacy_accept"] is True
    assert node["status_label"] == "done, accepted (legacy short sha)"
    assert plan["counts"]["done"] == 1
    assert plan["counts"]["done_unverified"] == 0, plan["counts"]
    assert node["blockers"] == []
    # Not bound to a head, so the dependent is still gated and says why.
    child = _node(plan, "T-002")
    assert [b["kind"] for b in child["blockers"]] == ["dep_unaccepted"]
    assert "not bound to a review head" in child["blockers"][0]["text"]
    assert "atm reopen T-001" in child["blockers"][0]["cmd"]
    tv = tk.ui_ticket(str(board), "T-001", "boss", "normal")
    assert tv["legacy_accept"] is True and tv["accepted"] is False
    assert tv["status_label"] == node["status_label"]


def test_done_force_then_accept_via_real_cli_label_agrees_with_counts(tmp_path):
    """CLI-seeded repro: done --force then accept --sha leaves no review_head."""
    board = _board(tmp_path, ("T-001", "Write CSV statistics", {}),
                   ("T-002", "Consume statistics", dict(deps=["T-001"])))
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True).strip()
    assert len(head) == 40
    for agent, args in (("alice", ("claim", "T-001")),
                        ("alice", ("done", "T-001", "--force", "--notes", "shipped")),
                        ("bob", ("accept", "T-001", "--sha", head[:7], "--notes", "ok"))):
        r = _cli(board, agent, *args)
        assert r.returncode == 0, r.stdout + r.stderr
    raw = json.loads((board / "T-001.json").read_text())
    assert not raw.get("review_head")

    plan = tk.ui_plan(str(board), "normal")
    node = _node(plan, "T-001")
    tv = tk.ui_ticket(str(board), "T-001", "boss", "normal")
    unverified = plan["counts"]["done_unverified"]
    # The invariant: a node is labelled "not accepted" exactly when it is counted unverified.
    assert (node["status_label"] == "done, not accepted") == bool(node["unverified"])
    assert unverified == sum(1 for n in plan["nodes"] if n["status_label"] == "done, not accepted")
    assert node["unverified"] is False and unverified == 0, plan["counts"]
    assert node["status_label"] == tv["status_label"] == "done, accepted (legacy short sha)"
    assert tv["accepted"] is False and tv["review"]["verified"] is True

    # Binding a head turns it into a plain accept.
    for agent, args in (("bob", ("reopen", "T-001", "--notes", "bind a review head")),
                        ("alice", ("claim", "T-001")),
                        ("alice", ("review", "T-001", "--force", "--notes", "paths"))):
        r = _cli(board, agent, *args)
        assert r.returncode == 0, r.stdout + r.stderr
    new_head = json.loads((board / "T-001.json").read_text())["review_head"]
    r = _cli(board, "bob", "accept", "T-001", "--sha", new_head, "--notes", "bound")
    assert r.returncode == 0, r.stdout + r.stderr
    raw3 = json.loads((board / "T-001.json").read_text())
    assert wv.structured_accept(raw3) and not wv.legacy_accept(raw3)
    node2 = _node(tk.ui_plan(str(board), "normal"), "T-001")
    assert node2["accepted"] is True and node2["legacy_accept"] is False
    assert node2["status_label"] == "done, accepted"


def test_done_without_any_accept_stays_not_accepted_and_unverified(tmp_path):
    board = _board(tmp_path, ("T-001", "Write CSV statistics", dict(status="done", owner="alice", commit=FULL)))
    plan = tk.ui_plan(str(board), "normal")
    node = _node(plan, "T-001")
    assert node["unverified"] is True and node["legacy_accept"] is False
    assert node["status_label"] == "done, not accepted"
    assert plan["counts"]["done_unverified"] == 1


def test_next_owner_is_empty_when_nobody_is_reserved(tmp_path):
    """summary.next.who is "" (the app renders "none yet"), never "?"."""
    board = _board(tmp_path, ("T-001", "Write CSV statistics", {}),
                   ("T-002", "Consume statistics", dict(deps=["T-001"])))
    plan = tk.ui_plan(str(board), "normal")
    nxt = plan["summary"]["next"]
    assert nxt and nxt["id"] == "T-001"
    assert nxt["who"] == "" and nxt["who_kind"] == ""
    assert "?" not in json.dumps(nxt)
