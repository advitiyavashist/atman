"""T-1053: a dependency needs a real artifact boundary; manufactured edges get flagged.

From the Polylane critique -- handoffs lose more than they save. A ``--deps``
edge is justified only when the predecessor produces a concrete artifact (an
accepted commit, a file, a decision recorded on its ticket) that the successor
reads. Splitting one artifact across two seats so they run in parallel is a
manufactured handoff.

This is a visible nudge and a measurement, never a gate: nothing here refuses
an edge, blocks a claim or changes a status, and an edge without enough
recorded evidence to judge stays ``unknown`` rather than being counted either
way.

Throwaway boards. Two-copy: tickets.py and src/ticket_board/cli.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import work_view  # noqa: E402

TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]

SHA = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
SHORT = SHA[:7]


def run(tool, board, *args, agent="", stdin=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"),
             TICKETS_GC_OPEN_PRS="none")
    e.pop("TICKETS_STOP_HOOK", None)
    e.pop("TICKET_SEAT", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
                "CURSOR_SESSION_ID", "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        e.pop(var, None)
    if agent:
        e["TICKET_SESSION_ID"] = "test-session-" + agent
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(tool), *args], capture_output=True, text=True,
        env=e, cwd=str(board.parent), input=stdin)


def load_ticket(board, tid):
    return json.loads((board / (tid + ".json")).read_text())


def save_ticket(board, t):
    (board / (t["id"] + ".json")).write_text(json.dumps(t, indent=2) + "\n")


# --- pure helpers: the artifact boundary ------------------------------------

def _accepted(tid="T-101", notes="paths src/ticket_board/work_view.py, tests added"):
    """A predecessor that went through review and holds a structured accept."""
    return {
        "id": tid, "status": "done", "role": "backend",
        "review_head": SHA, "commit": "fix/t1053@" + SHORT,
        "review_events": [{"kind": "accept", "by": "reviewer",
                           "at": "2026-09-21T00:01:00Z", "sha": SHA,
                           "notes": notes}],
        "notes": [{"by": "alice", "at": "2026-09-21T00:00:00Z",
                   "text": "REVIEW: fix/t1053@%s -- %s" % (SHORT, notes)}],
    }


def _succ(tid="T-102", pred="T-101", recorded="", body=""):
    t = {"id": tid, "status": "done", "role": "backend", "deps": [pred],
         "body": body, "notes": []}
    if recorded:
        t["notes"].append({"by": "bob", "at": "2026-09-21T01:00:00Z",
                           "text": recorded})
    return t


def test_successor_referencing_the_accepted_sha_is_not_flagged():
    pred = _accepted()
    succ = _succ(recorded="built on %s handed over by T-101" % SHORT)
    row = work_view.classify_handoff(pred, succ)
    assert row["verdict"] == "linked"
    assert row["kind"] == "sha"
    assert row["evidence"] == SHA[:12]
    assert row["reason"] == ""


def test_successor_referencing_a_recorded_path_is_not_flagged():
    pred = _accepted()
    succ = _succ(recorded="extended src/ticket_board/work_view.py with the audit")
    row = work_view.classify_handoff(pred, succ)
    assert row["verdict"] == "linked"
    assert row["kind"] == "path"
    assert row["evidence"] == "src/ticket_board/work_view.py"


def test_successor_citing_the_predecessors_recorded_decision_is_not_flagged():
    pred = _accepted(notes="decided: verdicts are records only, never prose")
    succ = _succ(recorded="following the decision recorded on T-101")
    row = work_view.classify_handoff(pred, succ)
    assert row["verdict"] == "linked"
    assert row["kind"] == "decision"
    assert row["evidence"] == "T-101"


def test_edge_with_no_artifact_linkage_is_flagged():
    pred = _accepted()
    succ = _succ(recorded="wrote the whole thing here from scratch")
    row = work_view.classify_handoff(pred, succ)
    assert row["verdict"] == "manufactured"
    assert row["kind"] == ""
    assert "T-101" in row["reason"]
    assert SHA[:12] in row["evidence"]


def test_a_planners_body_mention_is_not_the_successor_reading_anything():
    """The body is the planner's text. "after T-101" written into a successor
    body is what a manufactured handoff looks like, not evidence of a read."""
    pred = _accepted()
    succ = _succ(recorded="wrote it here", body="Follow-on work after T-101 lands.")
    assert work_view.classify_handoff(pred, succ)["verdict"] == "manufactured"


def test_the_release_note_atm_accept_writes_is_not_the_successor_reading_it():
    """`atm accept` appends "<pred> accepted at <sha> by <seat> -- unblocked"
    to every child it releases. That note quotes the predecessor's id AND its
    accepted SHA, so counting it as linkage would link every properly gated
    edge and the audit could never flag anything."""
    pred = _accepted()
    succ = _succ(recorded="wrote the whole thing here")
    succ["notes"].append({
        "by": "boss", "at": "2026-09-21T00:02:00Z",
        "text": work_view.accepted_release_note("T-101", SHA, "ceo")})
    succ["notes"].append({
        "by": "boss", "at": "2026-09-21T00:03:00Z",
        "text": work_view.unverified_block_reason("T-101")})
    assert work_view.classify_handoff(pred, succ)["verdict"] == "manufactured"
    # a seat's own sentence quoting the same SHA still links it
    succ["notes"].append({"by": "bob", "at": "2026-09-21T02:00:00Z",
                          "text": "rebased onto %s as T-101 handed over" % SHORT})
    assert work_view.classify_handoff(pred, succ)["verdict"] == "linked"


def test_a_shared_repo_url_is_not_a_shared_file_path():
    """Review notes routinely carry the repo URL. Treating it as a recorded
    path would link two tickets that share nothing but the remote."""
    pred = _accepted(notes="pushed to https://github.com/example/atman.git")
    succ = _succ(recorded="pushed to https://github.com/example/atman.git")
    assert work_view.classify_handoff(pred, succ)["verdict"] == "manufactured"


def test_unjudgeable_edges_stay_unknown_and_are_never_folded_in():
    pred = _accepted()
    unstarted = _succ(tid="T-103")
    unstarted["status"] = "open"
    row = work_view.classify_handoff(pred, unstarted)
    assert row["verdict"] == "unknown"
    assert row["reason"] == "successor has recorded no work yet"

    empty_pred = {"id": "T-104", "status": "open", "notes": []}
    row = work_view.classify_handoff(empty_pred, _succ(tid="T-105", pred="T-104",
                                                       recorded="started here"))
    assert row["verdict"] == "unknown"
    assert row["reason"] == "predecessor has produced no recorded artifact yet"


def test_audit_counts_are_the_run_report_contract():
    pred = _accepted()
    linked = _succ(tid="T-102", recorded="built on %s" % SHORT)
    flagged = _succ(tid="T-103", recorded="wrote it here")
    unknown = _succ(tid="T-104")
    unknown["status"] = "open"
    audit = work_view.handoff_audit([pred, linked, flagged, unknown])
    assert audit["handoffs"] == 3
    assert (audit["linked"], audit["manufactured"], audit["unknown"]) == (1, 1, 1)
    # unknown is reported, never folded into either side
    assert audit["linked"] + audit["manufactured"] + audit["unknown"] == audit["handoffs"]
    assert [e["succ"] for e in audit["edges"] if e["verdict"] == "manufactured"] == ["T-103"]
    # the caveat travels with the number so a report cannot quote it bare
    assert audit["manufactured_is_lower_bound"] is True


def test_scope_filters_on_the_successor_and_skips_dangling_edges():
    pred = dict(_accepted(), epic="E-1")
    inside = _succ(tid="T-102", recorded="wrote it here")
    inside["epic"] = "E-1"
    outside = _succ(tid="T-103", recorded="wrote it here")
    outside["epic"] = "E-2"
    ghost = _succ(tid="T-104", pred="T-999", recorded="wrote it here")
    tickets = [pred, inside, outside, ghost]
    assert work_view.handoff_audit(tickets)["handoffs"] == 2  # ghost skipped
    scoped = work_view.handoff_audit(tickets, epic="E-1")
    assert scoped["handoffs"] == 1
    assert scoped["edges"][0]["succ"] == "T-102"
    assert scoped["epic"] == "E-1"


def test_plan_nudge_is_silent_without_edges():
    assert work_view.handoff_plan_nudge(0) == []
    lines = work_view.handoff_plan_nudge(2)
    assert "2 dependency edge(s) created" in lines[0]
    assert any("docs/handoff-contract.md" in ln for ln in lines)


def test_report_lines_name_the_rule_only_when_something_is_flagged():
    clean = work_view.handoff_audit([_accepted(), _succ(recorded="built on %s" % SHORT)])
    assert not any("--deps edge is justified" in ln
                   for ln in work_view.handoff_report_lines(clean))
    dirty = work_view.handoff_audit([_accepted(), _succ(recorded="wrote it here")])
    lines = work_view.handoff_report_lines(dirty)
    assert any("manufactured T-101 -> T-102" in ln for ln in lines)
    assert any("not a gate -- nothing is blocked" in ln for ln in lines)
    assert any("a lower bound" in ln for ln in lines)


# --- both entry points surface it -------------------------------------------

PLAN = json.dumps([
    {"key": "api", "title": "Build API", "role": "backend", "deps": []},
    {"key": "ui", "title": "Build UI", "role": "backend", "deps": ["api"]},
    {"key": "doc", "title": "Write doc", "role": "backend", "deps": ["api"]},
])


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_plan_prints_the_handoff_count_and_the_rule(tool, board):  # noqa: F811
    r = run(tool, board, "plan", stdin=PLAN, agent="boss")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "handoffs: 2 dependency edge(s) created" in r.stdout
    assert "docs/handoff-contract.md" in r.stdout
    # a plan with no edges says nothing about handoffs
    r = run(tool, board, "plan", agent="boss",
            stdin=json.dumps([{"title": "Standalone", "role": "backend", "deps": []}]))
    assert r.returncode == 0, r.stderr + r.stdout
    assert "handoffs:" not in r.stdout


def _two_successors(tool, board):
    """T-002 accepted; T-003 references its SHA, T-004 references nothing."""
    for name in ("alice", "bob", "carol"):
        assert run(tool, board, "join", name, "--roles", "backend",
                   agent=name).returncode == 0
    for title in ("impl work", "reads the artifact", "parallel split"):
        r = run(tool, board, "create", title, "--role", "backend", agent="alice")
        assert r.returncode == 0, r.stderr

    pred = load_ticket(board, "T-002")
    pred.update(_accepted("T-002"))
    pred["id"] = "T-002"
    pred["title"] = "impl work"
    save_ticket(board, pred)

    for tid, recorded in (("T-003", "built on %s from T-002" % SHORT),
                          ("T-004", "wrote the whole thing here")):
        t = load_ticket(board, tid)
        t["deps"] = ["T-002"]
        t["status"] = "done"
        t["notes"] = [{"by": "bob", "at": "2026-09-21T01:00:00Z", "text": recorded}]
        save_ticket(board, t)


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_graph_flags_the_manufactured_edge_and_gates_nothing(tool, board):  # noqa: F811
    _two_successors(tool, board)

    r = run(tool, board, "graph", agent="boss")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "Handoffs: 2 dependency edges" in r.stdout
    assert "manufactured T-002 -> T-004" in r.stdout
    assert "T-002 -> T-003" not in r.stdout
    assert "not a gate -- nothing is blocked" in r.stdout

    r = run(tool, board, "graph", "--json", agent="boss")
    assert r.returncode == 0, r.stderr + r.stdout
    audit = json.loads(r.stdout)
    assert audit["handoffs"] == 2
    assert (audit["linked"], audit["manufactured"], audit["unknown"]) == (1, 1, 0)
    verdicts = dict((e["succ"], e["verdict"]) for e in audit["edges"])
    assert verdicts == {"T-003": "linked", "T-004": "manufactured"}

    # the nudge changed nothing: no status, no owner, no note
    for tid in ("T-002", "T-003", "T-004"):
        t = load_ticket(board, tid)
        assert t["status"] == "done", tid
        assert not any("manufactured" in (n.get("text") or "")
                       for n in t.get("notes") or []), tid


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_graph_handoffs_only_and_objective_label(tool, board):  # noqa: F811
    _two_successors(tool, board)
    (board / "objective.json").write_text(json.dumps(
        {"text": "Ship the handoff rule", "state": "active"}))
    r = run(tool, board, "graph", "--handoffs", agent="boss")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "Objective: Ship the handoff rule" in r.stdout
    assert "Handoffs: 2 dependency edges" in r.stdout
    assert "Dependency graph" not in r.stdout  # --handoffs is the section alone
