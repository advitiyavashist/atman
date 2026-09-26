"""T-992: launch repair on PR #156 -- done-unverified stays visible, transport is
"Board synced", and the 390px first screen folds duplicate chrome.

Throwaway boards only (the ``board`` fixture). No redesign: these pin wording,
counts and the fold, not layout pixels -- the browser evidence does that.
"""
import json
import sys
from pathlib import Path

from test_wakeup import board, run  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
sys.path.insert(0, str(ROOT / "src"))
import tickets as tk  # noqa: E402
from ticket_board import work_view  # noqa: E402

UNVERIFIED = "Marked done; verification not recorded"


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    return text[start:text.index('"""', start)]


def _snap(board):
    r = run(board, "ui", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _force(board, tid, **fields):
    path = board / (tid + ".json")
    rec = json.loads(path.read_text())
    rec.update(fields)
    path.write_text(json.dumps(rec, indent=2) + "\n")


# --- (1) done without a structured ACCEPT is shown, named, and not counted as accepted


def test_unverified_done_ids_is_review_evidence_not_status():
    chat = {"id": "T-001", "status": "done", "commit": "br@abc1234",
            "notes": [{"by": "bob", "at": "2026-09-15T00:01:00Z", "text": "I completed it in chat"}]}
    accepted = {"id": "T-002", "status": "done", "commit": "br@def5678",
                "review_events": [{"kind": "accept", "by": "cos", "at": "2026-09-15T00:02:00Z",
                                   "sha": "def5678", "notes": "ok"}]}
    merged = {"id": "T-003", "status": "done", "commit": "br@0123abc",
              "notes": [{"by": "planner", "at": "2026-09-15T00:03:00Z",
                         "text": "merged into main as 9999999 (tickets merge; pinned 0123abc)"}]}
    # an ACCEPT on an older artifact does not verify the current one
    stale = {"id": "T-004", "status": "done", "commit": "br@bbbbbbb",
             "review_events": [{"kind": "accept", "by": "cos", "at": "2026-09-15T00:04:00Z",
                                "sha": "aaaaaaa", "notes": "old"}]}
    still_open = {"id": "T-005", "status": "review", "commit": "br@ccccccc"}
    ids = work_view.unverified_done_ids([chat, accepted, merged, stale, still_open])
    assert ids == {"T-001", "T-003", "T-004"}
    # a chat-style ACCEPT or a prose merge note is not a verdict (T-944 / T-1111):
    # only review_events written by atm accept, or a merge_record, verify
    msg = {"id": "m1", "re": "T-001", "from": "cos", "to": "bob", "at": "2026-09-15T00:05:00Z",
           "text": "ACCEPT abc1234 -- looks good to me"}
    assert "T-001" in work_view.unverified_done_ids([chat], [msg])


def test_workflow_graph_keep_done_keeps_only_named_done_tickets():
    tickets = [
        {"id": "T-001", "title": "a", "status": "open", "deps": []},
        {"id": "T-002", "title": "b", "status": "done", "deps": []},
        {"id": "T-003", "title": "c", "status": "done", "deps": []},
    ]
    g = tk.workflow_graph(tickets)
    assert [n["id"] for n in g["nodes"]] == ["T-001"]
    g = tk.workflow_graph(tickets, keep_done={"T-002", "T-001", "T-404"})
    assert [n["id"] for n in g["nodes"]] == ["T-001", "T-002"]
    assert g["view"] == "active" and g["counts"] == {"open": 1, "done": 1}


def test_done_without_accept_stays_on_work_graph_and_list(board):
    assert run(board, "create", "Finished without review", "--role", "docs").returncode == 0
    assert run(board, "create", "Finished and accepted", "--role", "docs").returncode == 0
    _force(board, "T-002", status="done", owner="bob", done_at="2026-09-15T00:01:00Z",
           notes=[{"by": "bob", "at": "2026-09-15T00:01:00Z", "text": "I completed it in chat"}])
    _force(board, "T-003", status="done", owner="bob", commit="bob@def5678",
           review_events=[{"kind": "accept", "by": "cos", "at": "2026-09-15T00:02:00Z",
                           "sha": "def5678", "notes": "verified"}])
    d = _snap(board)
    graph_ids = [n["id"] for n in d["graph"]["nodes"]]
    assert "T-002" in graph_ids, "done-without-ACCEPT must stay on the graph"
    assert "T-003" not in graph_ids, "accepted done work leaves the active view as before"
    work = d["work"]
    by = dict((n["id"], n) for n in work["nodes"])
    assert "T-002" in by and "T-002" in work["order"]
    node = by["T-002"]
    assert node["phase"] == "done" and node["unverified"] is True
    assert node["review"]["label"] == UNVERIFIED
    assert node["review"]["verified"] is False
    assert node["verdict"] == UNVERIFIED
    assert work["counts"]["done"] == 1 and work["counts"]["done_unverified"] == 1
    # the header count is accepted work; the unverified flag is named beside it
    counts = d["counts"]
    assert counts["total"] == 3 and counts["done"] == 2
    assert counts["accepted"] == 1 and counts["done_unverified"] == 1
    # a legacy board with no structured verdicts reports zero accepted, not zero done
    _force(board, "T-003", review_events=[])
    counts = _snap(board)["counts"]
    assert counts["done"] == 2 and counts["accepted"] == 0 and counts["done_unverified"] == 2


def test_shell_renders_unverified_done_in_node_legend_and_chip():
    ui = _ui_html()
    chips = ui[ui.index("document.getElementById('chips').innerHTML"):ui.index("const s=d.sprint")]
    assert "' accepted</span>'" in chips
    assert "done, unverified" in chips
    assert "' done</span>'" not in chips, "the header count must not read as done=accepted"
    js = work_view.WORK_JS
    assert "const UNVERIFIED='%s'" % UNVERIFIED in js
    node = js[js.index("function nodeBtn"):js.index("function graphHtml")]
    assert "n.unverified?UNVERIFIED" in node
    assert "Done · unverified" in node
    assert "unverified" in js[js.index("const legend="):js.index("HOST.innerHTML=")]
    assert ".wv-node.ph-done.unverified{opacity:1" in work_view.WORK_CSS


# --- (2) transport label


def test_page_transport_reads_board_synced_never_connected():
    ui = _ui_html()
    conn = ui[ui.index("function setConn"):ui.index("let firstLoad")]
    assert "'Board synced'" in conn
    assert "'connected'" not in conn
    head = ui[ui.index('id="connStatus"'):ui.index("</span>", ui.index('id="connStatus"'))]
    assert ">live<" not in head and ">connected<" not in head


# --- (3) 390px first screen: one header fold, no duplicate objective strip on Work


def test_mobile_chrome_folds_into_one_header_status():
    ui = _ui_html()
    assert '<details class="hdr-status" id="hdrStatus" open>' in ui
    for el in ('id="chips"', 'id="promiseChips"', 'id="sprint"', 'id="pulse"'):
        assert ui.index('id="hdrStatus"') < ui.index(el) < ui.index('id="connBar"'), el
    css = ui[ui.index("@media(max-width:600px){"):]
    css = css[:css.index("\n}\n") + 3]
    assert ".hdr-status>summary{display:flex}" in css
    assert "body[data-tab=board] .promise-strip{display:none}" in css
    assert ".next-step{display:none}" not in css, "the action strip and its unreachable state stay"
    desk = ui[ui.index(".hdr-status{"):ui.index(".chips{")]
    assert "summary{display:none" in desk and ".hdr-status-body{display:contents}" in desk
    js = ui[ui.index("const hdr=document.getElementById('hdrStatus')"):]
    assert "matchMedia('(max-width:600px)')" in js and "hdr.open=!mq.matches" in js
    assert 'id="hdrStatusBrief"' in ui and "hdrStatusBrief" in ui[ui.index("<script>"):]
