"""T-889: Work view -- objective above a real dependency graph with node detail.

Throwaway boards only. The payload lives in src/ticket_board/work_view.py and
reaches the page through three named hooks in tickets.py (_board_snapshot_body
"work", _ui_page placeholders, renderGraph delegation).
"""
import json
from pathlib import Path

from test_wakeup import board, run  # noqa: F401
from ui_server_harness import make_ui_server_fixture

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
ui_server = make_ui_server_fixture("t889-work")


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    return text[start:text.index('"""', start)]


def _snap(board):
    r = run(board, "ui", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _nodes(board):
    return dict((n["id"], n) for n in _snap(board)["work"]["nodes"])


def _team(board):
    assert run(board, "join", "planner", "--roles", "backend", agent="planner").returncode == 0
    assert run(board, "join", "alice", "--roles", "docs", agent="alice").returncode == 0
    assert run(board, "join", "bob", "--roles", "docs", agent="bob").returncode == 0
    assert run(board, "master", "take", "--owner", "planner", agent="planner").returncode == 0


def _chain(board):
    """T-001 (Write docs, from the fixture) <- T-002 <- T-003; T-002 reserved for bob."""
    _team(board)
    assert run(board, "create", "Review docs", "--role", "docs", "--deps", "T-001", agent="planner").returncode == 0
    assert run(board, "create", "Publish", "--role", "docs", "--deps", "T-002", agent="planner").returncode == 0
    assert run(board, "reserve", "T-002", "--for", "bob", agent="planner").returncode == 0


# --- shell hooks ------------------------------------------------------------

def test_shell_keeps_three_named_hooks_and_t791_ids():
    ui = _ui_html()
    assert ui.count("<!--WORK_VIEW:css-->") == 1
    assert ui.count("<!--WORK_VIEW:html-->") == 1
    assert ui.count("<!--WORK_VIEW:js-->") == 1
    assert "window.AtmanWork.render(d,host)" in ui
    assert "renderGraph(d.graph,d)" in ui
    # T-791 contract the shell still honours
    assert 'id="workflowGraph"' in ui and "function renderGraph" in ui and 'data-work-view="graph"' in ui
    # nothing of the module leaks into the shell source: it is spliced at serve time
    assert "wv-node" not in ui and "AtmanWork=(function" not in ui


def test_served_page_splices_module_and_json_carries_work(board, ui_server):
    _chain(board)
    page = ui_server.get("/", raw=True).decode()
    assert "<!--WORK_VIEW:" not in page
    assert "window.AtmanWork=(function" in page
    assert ".wv-node{" in page and 'id="workView"' in page
    assert "prefers-reduced-motion" in page
    # the shell (T-810) owns the standing-objective strip when it renders #workObjective
    assert "getElementById('workObjective')" in page
    assert "aria-label=\"Dependency graph" in page
    d = ui_server.get("/board.json")
    assert d["work"]["objective"]["text"] == ""
    assert [n["id"] for n in d["work"]["nodes"]] == ["T-001", "T-002", "T-003"]


# --- payload: objective + phases --------------------------------------------

def test_objective_and_exit_criterion_ride_above_graph(board):
    _team(board)
    assert run(board, "objective", "--set", "Ship the Work view", "--exit",
               "graph explains every node", agent="planner").returncode == 0
    w = _snap(board)["work"]
    assert w["objective"]["text"] == "Ship the Work view"
    assert w["objective"]["exit_criterion"] == "graph explains every node"
    assert w["objective"]["exit_missing"] is False
    assert w["objective"]["state"]


def test_ready_dispatched_working_are_distinct_evidence(board):
    _chain(board)
    by = _nodes(board)
    assert by["T-001"]["phase"] == "ready"
    assert "nobody dispatched" in by["T-001"]["evidence"]
    assert by["T-002"]["phase"] == "waiting"
    assert by["T-002"]["wait"] == {"kind": "deps", "on": ["T-001"], "text": "waits on T-001",
                                   "cmd": "tickets show T-001"}
    assert run(board, "next", agent="alice").returncode == 0
    by = _nodes(board)
    assert by["T-001"]["phase"] == "working"
    assert by["T-001"]["owner"] == "alice"
    assert by["T-001"]["evidence"].startswith("claimed by @alice")
    assert "last update" in by["T-001"]["evidence"]
    assert by["T-001"]["stale"] is False


def test_completed_parent_shows_dispatch_and_whether_child_began(board):
    _chain(board)
    assert run(board, "next", agent="alice").returncode == 0
    # --force: the throwaway repo sits on its trunk branch (same as the T-781 tests)
    r = run(board, "done", "T-001", "--notes", "docs at docs/x.md; tests pass", "--force", agent="alice")
    assert r.returncode == 0, r.stdout + r.stderr
    by = _nodes(board)
    parent, child = by["T-001"], by["T-002"]
    assert parent["phase"] == "done" and parent["verdict"] == "accepted"
    # T-781 trigger posted a task DM to the reserved seat: dispatched, not working
    assert child["phase"] == "dispatched"
    assert child["dispatch"]["to"] == "bob"
    assert child["dispatch"]["seen"] is False
    assert "task posted to @bob" in child["evidence"] and "not claimed" in child["evidence"]
    sb = child["started_by"]
    assert sb["parent"] == "T-001" and sb["began"] is False
    assert sb["trigger"]["to"] == "bob"
    # inherited handoff = the finished dep's last note
    assert child["handoff"][0]["from"] == "T-001"
    assert "docs at docs/x.md" in child["handoff"][0]["text"]
    # the parent side of the same story
    assert [(c["id"], c["freed"], c["began"], c["who"]) for c in parent["starts"]] == [("T-002", True, False, "bob")]
    # grandchild still waits and is not freed
    assert by["T-003"]["phase"] == "waiting"
    # child begins -> both sides agree it started
    assert run(board, "next", agent="bob").returncode == 0
    by = _nodes(board)
    assert by["T-002"]["phase"] == "working"
    assert by["T-002"]["started_by"]["began"] is True
    assert by["T-002"]["started_by"]["began_by"] == "bob"
    assert by["T-001"]["starts"][0]["began"] is True


def test_capture_hold_blocked_and_review_carry_reason_and_command(board):
    _team(board)
    assert run(board, "capture", "Sketch idea", "--role", "docs", agent="planner").returncode == 0
    assert run(board, "create", "Parked", "--role", "docs", "--body", "HOLD until tester week", agent="planner").returncode == 0
    assert run(board, "create", "Broken", "--role", "docs", agent="planner").returncode == 0
    assert run(board, "next", "--steal", "T-004", agent="bob").returncode == 0
    assert run(board, "block", "T-004", "--reason", "needs operator creds", agent="bob").returncode == 0
    by = _nodes(board)
    assert by["T-002"]["phase"] == "capture"
    assert by["T-002"]["wait"]["text"] == "waits in capture: run sound"
    assert by["T-002"]["wait"]["cmd"] == "tickets sound T-002"
    assert by["T-002"]["acceptance"]["proof"] == ""
    assert by["T-003"]["phase"] == "hold"
    assert by["T-003"]["wait"]["kind"] == "hold"
    assert by["T-004"]["phase"] == "blocked"
    assert by["T-004"]["wait"]["text"] == "blocked: needs operator creds"
    assert "needs operator creds" in by["T-004"]["evidence"]
    counts = _snap(board)["work"]["counts"]
    assert counts["capture"] == 1 and counts["hold"] == 1 and counts["blocked"] == 1


def test_acceptance_artifact_and_verdict_surface(board):
    _team(board)
    assert run(board, "capture", "Sounded task", "--role", "docs", agent="planner").returncode == 0
    r = run(board, "sound", "T-002", "--notes",
            "cause=docs are stale; change=rewrite quickstart; proof=pytest tests/test_quickstart.py; deps=none; questions=",
            agent="planner")
    assert r.returncode == 0, r.stdout + r.stderr
    by = _nodes(board)
    node = by["T-002"]
    assert node["phase"] == "ready"
    assert node["acceptance"]["proof"] == "pytest tests/test_quickstart.py"
    assert node["acceptance"]["cause"] == "docs are stale"
    assert node["acceptance"]["change"] == "rewrite quickstart"
    assert node["acceptance"]["sounded_by"] == "planner"
    assert run(board, "next", agent="alice").returncode == 0
    assert run(board, "update", "T-001", "REVIEW: branch@abc -- paths: docs/", agent="alice").returncode == 0
    by = _nodes(board)
    assert by["T-001"]["verdict"].startswith("REVIEW: branch@abc")
    assert by["T-001"]["last_note"]["by"] == "alice"


def test_layers_follow_dependency_depth_and_summary_names_next(board):
    _chain(board)
    w = _snap(board)["work"]
    assert w["layers"] == [["T-001"], ["T-002"], ["T-003"]]
    assert w["order"] == ["T-001", "T-002", "T-003"]
    assert {"from": "T-001", "to": "T-002", "waiting": True} in w["edges"]
    assert w["summary"]["next"]["id"] == "T-001" and w["summary"]["next"]["phase"] == "ready"
    assert w["summary"]["waiting"] == 2
    assert w["empty"] is None


def test_empty_states_offer_one_real_first_action(board):
    for p in board.glob("T-*.json"):
        p.unlink()
    w = _snap(board)["work"]
    assert w["empty"]["kind"] == "no_tickets"
    assert w["empty"]["cmd"].startswith("tickets plan")
    assert run(board, "create", "First", "--role", "docs", agent="planner").returncode == 0
    assert run(board, "create", "Second", "--role", "docs", agent="planner").returncode == 0
    w = _snap(board)["work"]
    assert w["empty"]["kind"] == "no_edges"
    ids = sorted(n["id"] for n in w["nodes"])
    assert w["empty"]["cmd"] == "tickets dep %s --after %s" % (ids[1], ids[0])


def test_work_payload_is_pure_and_survives_bad_ack(board):
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from ticket_board import work_view
    tickets = [
        {"id": "T-001", "title": "a", "status": "done", "owner": "x", "done_at": "2026-09-13T00:00:00Z",
         "notes": [{"by": "x", "at": "2026-09-13T00:00:00Z", "text": "handoff here"}], "deps": []},
        {"id": "T-002", "title": "b", "status": "open", "deps": ["T-001"], "notes": []},
    ]
    graph = {"nodes": [{"id": "T-001"}, {"id": "T-002"}],
             "edges": [{"from": "T-001", "to": "T-002", "waiting": False}], "roots": ["T-001"]}
    msgs = [{"id": "m1", "kind": "task", "re": "T-002", "to": "bob", "from": "x",
             "at": "2026-09-13T00:00:01Z", "text": "unblocked T-002 after T-001 -- start (success trigger)"}]

    def boom(who, msg):
        raise RuntimeError("no inbox")

    w = work_view.work_payload(tickets, graph, msgs, objective={"text": "o"}, acked=boom)
    child = dict((n["id"], n) for n in w["nodes"])["T-002"]
    assert child["phase"] == "dispatched"
    assert child["dispatch"]["seen"] is None
    assert "delivery unknown" in child["evidence"]
    assert child["started_by"]["trigger"]["seen"] is None
    assert child["handoff"][0]["text"] == "handoff here"
    assert w["layers"] == [["T-001"], ["T-002"]]
