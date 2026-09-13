"""T-791: workflow dependency graph in tickets ui — edges, not a title dump.

tickets graph / tickets map already exist. The command board must show the
same --after waiting-on edges (ticket ids + status), not a list of titles.
Do not remake T-780/T-778/T-781. Throwaway boards only.
"""
import json
from pathlib import Path

from test_wakeup import board, run  # noqa: F401
from ui_server_harness import make_ui_server_fixture

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
ui_server = make_ui_server_fixture("t791-graph")


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    return text[start:text.index('"""', start)]


def test_ui_html_has_graph_view_not_title_dump():
    ui = _ui_html()
    assert 'id="workflowGraph"' in ui
    assert 'data-work-view="graph"' in ui
    assert "function renderGraph" in ui
    assert "waiting on " in ui
    assert "tickets graph" in ui
    assert "tickets map" in ui
    assert 'id="open"' not in ui
    tabs = ui[ui.index('<nav class="tabs"'):ui.index("</nav>", ui.index('<nav class="tabs"'))]
    assert all(label in tabs for label in [">Objective<", ">Team<", ">Work<", ">Intervene<"])
    assert 'data-tab-btn="graph"' not in ui  # Work pane toggle, not a fifth tab


def test_board_json_graph_exposes_waiting_edges(board):
    created = run(board, "create", "Build login UI", "--role", "frontend", "--deps", "T-001")
    assert created.returncode == 0, created.stderr
    snap = run(board, "ui", "--json")
    assert snap.returncode == 0, snap.stderr
    d = json.loads(snap.stdout)
    g = d["graph"]
    assert g["view"] == "active"
    assert {"from": "T-001", "to": "T-002", "waiting": True} in g["edges"]
    by_id = dict((n["id"], n) for n in g["nodes"])
    assert by_id["T-002"]["waiting"] == ["T-001"]
    assert by_id["T-002"]["deps"] == ["T-001"]
    assert "T-002" in by_id["T-001"]["children"]
    assert "T-001" in g["roots"]
    # Titles may appear, but the payload is ids + edges — not titles alone.
    assert all("id" in n and "waiting" in n and "deps" in n for n in g["nodes"])

    tree = run(board, "graph")
    assert tree.returncode == 0, tree.stderr
    assert "waiting on T-001" in tree.stdout
    assert "T-002" in tree.stdout


def test_live_board_json_serves_graph(board, ui_server):
    run(board, "create", "Build login UI", "--role", "frontend", "--deps", "T-001")
    d = ui_server.get("/board.json")
    assert "graph" in d
    assert any(e["from"] == "T-001" and e["to"] == "T-002" for e in d["graph"]["edges"])
    page = ui_server.get("/", raw=True).decode()
    assert 'id="workflowGraph"' in page
    assert 'data-work-view="graph"' in page
    assert 'function renderGraph' in page


def test_active_graph_omits_unrelated_done(board):
    run(board, "create", "Build login UI", "--role", "frontend", "--deps", "T-001")
    extra = run(board, "create", "Unrelated leftover")
    assert extra.returncode == 0, extra.stderr
    # Force T-003 done without going through review: edit the fixture file.
    path = board / "T-003.json"
    rec = json.loads(path.read_text())
    rec["status"] = "done"
    path.write_text(json.dumps(rec, indent=2) + "\n")
    d = json.loads(run(board, "ui", "--json").stdout)
    ids = [n["id"] for n in d["graph"]["nodes"]]
    assert "T-001" in ids and "T-002" in ids
    assert "T-003" not in ids
