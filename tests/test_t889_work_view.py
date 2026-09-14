"""T-889: Work view -- objective above a real dependency graph with node detail.

Throwaway boards only. The payload lives in src/ticket_board/work_view.py and
reaches the page through three named hooks in tickets.py (_board_snapshot_body
"work", _ui_page placeholders, renderGraph delegation) plus the reopened_at
stamp in cmd_reopen. The evidence-separation cases follow the T-892 review
(docs/reviews/t892-atman-work-ux.md) as adopted by the CEO on T-889.
"""
import json
import sys
from pathlib import Path

from test_wakeup import board, run  # noqa: F401
from ui_server_harness import make_ui_server_fixture

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
ui_server = make_ui_server_fixture("t889-work")
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import work_view  # noqa: E402


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


def _pure(tickets, msgs=(), agents=None, acked=None, edges=None):
    graph = {"nodes": [{"id": t["id"]} for t in tickets], "edges": list(edges or []), "roots": []}
    w = work_view.work_payload(tickets, graph, list(msgs), objective={"text": "o"},
                               acked=acked, agents=agents)
    return w, dict((n["id"], n) for n in w["nodes"])


T0 = "2026-09-13T00:00:00Z"


def _t(tid, status="open", **kw):
    t = {"id": tid, "title": "t " + tid, "status": status, "deps": [], "notes": []}
    t.update(kw)
    return t


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
    # the shell (T-810) owns the standing-objective strip when it renders #workObjective,
    # but the module still renders Done when unless the strip marks its own [data-done-when]
    assert "getElementById('workObjective')" in page and "[data-done-when]" in page
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


def test_ready_reserved_working_are_distinct_evidence(board):
    _chain(board)
    by = _nodes(board)
    assert by["T-001"]["phase"] == "ready"
    assert by["T-001"]["evidence"] == "Unblocked · no reservation, no task posted · tickets next claims it"
    assert by["T-001"]["who_kind"] == ""
    assert by["T-002"]["phase"] == "waiting"
    assert by["T-002"]["wait"] == {"kind": "deps", "on": ["T-001"], "text": "waits on T-001",
                                   "cmd": "tickets show T-001"}
    assert run(board, "next", agent="alice").returncode == 0
    by = _nodes(board)
    assert by["T-001"]["phase"] == "working"
    assert by["T-001"]["owner"] == "alice" and by["T-001"]["who_kind"] == "claimed"
    assert by["T-001"]["evidence"].startswith("Claimed by @alice")
    assert "last update" in by["T-001"]["evidence"]
    assert by["T-001"]["stale"] is False


def test_completed_parent_posts_task_and_child_shows_receipts_not_told(board):
    _chain(board)
    assert run(board, "next", agent="alice").returncode == 0
    # --force: the throwaway repo sits on its trunk branch (same as the T-781 tests)
    r = run(board, "done", "T-001", "--notes", "docs at docs/x.md; tests pass", "--force", agent="alice")
    assert r.returncode == 0, r.stdout + r.stderr
    by = _nodes(board)
    parent, child = by["T-001"], by["T-002"]
    assert parent["phase"] == "done"
    # a done flag alone is not acceptance
    assert parent["review"]["label"] == "Marked done; verification not recorded"
    assert parent["review"]["verified"] is False
    # T-781 trigger posted a task message to the reserved seat: posted, with receipts kept separate
    assert child["phase"] == "posted"
    assert child["who"] == "bob" and child["who_kind"] == "posted"
    assert child["dispatch"]["to"] == "bob"
    assert child["dispatch"]["seen"] is False and child["dispatch"]["wake"] is None
    assert child["evidence"].startswith("Task posted to @bob")
    assert "not read, wake unconfirmed" in child["evidence"] and "not claimed" in child["evidence"]
    p = child["progress"]
    assert p["parent"] == "T-001" and p["all_done"] is True and p["became_ready"] is True
    assert p["trigger"]["to"] == "bob" and p["trigger"]["seen"] is False
    assert p["claim"] is None
    # inherited handoff = the finished dep's last note
    assert child["handoff"][0]["from"] == "T-001"
    assert "docs at docs/x.md" in child["handoff"][0]["text"]
    # the parent side of the same story: posted, not told, not begun
    s = parent["starts"]
    assert [(c["id"], c["freed"], c["began"], c["who"], c["who_kind"]) for c in s] == [
        ("T-002", True, False, "bob", "posted")]
    assert s[0]["delivery"] == "not read, wake unconfirmed"
    # grandchild still waits and is not freed
    assert by["T-003"]["phase"] == "waiting"
    # bob reads the inbox: that is a read receipt, not an acknowledgement and not work
    assert run(board, "inbox", agent="bob").returncode == 0
    by = _nodes(board)
    assert by["T-002"]["phase"] == "posted"
    assert by["T-002"]["dispatch"]["seen"] is True
    assert "inbox read, not acknowledged" in by["T-002"]["evidence"]
    # child claims -> both sides agree it began, after the trigger
    assert run(board, "next", agent="bob").returncode == 0
    by = _nodes(board)
    assert by["T-002"]["phase"] == "working"
    assert by["T-002"]["progress"]["claim"]["current"] is True
    assert by["T-002"]["progress"]["claim"]["causality"] == "after_trigger"
    assert by["T-001"]["starts"][0]["began"] is True and by["T-001"]["starts"][0]["who_kind"] == "claimed"


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
    w = _snap(board)["work"]
    counts = w["counts"]
    assert counts["capture"] == 1 and counts["hold"] == 1 and counts["blocked"] == 1
    # the first-screen summary names blockers with titles, kinds and the clearing command
    kinds = dict((b["id"], (b["kind"], b["title"])) for b in w["summary"]["blocked"])
    assert kinds == {"T-004": ("blocked", "Broken"), "T-003": ("hold", "Parked"), "T-002": ("capture", "Sketch idea")}


def test_acceptance_artifact_and_review_surface(board):
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
    # the author's own REVIEW: submission note is an artifact, never a verdict
    assert run(board, "update", "T-001", "REVIEW: branch@abc1234 -- paths: docs/", agent="alice").returncode == 0
    by = _nodes(board)
    assert by["T-001"]["review"]["label"] == "" and by["T-001"]["review"]["latest"] is None
    assert by["T-001"]["last_note"]["by"] == "alice"


def test_layers_follow_dependency_depth_and_summary_names_next(board):
    _chain(board)
    w = _snap(board)["work"]
    assert w["layers"] == [["T-001"], ["T-002"], ["T-003"]]
    assert w["order"] == ["T-001", "T-002", "T-003"]
    assert {"from": "T-001", "to": "T-002", "waiting": True} in w["edges"]
    nxt = w["summary"]["next"]
    assert nxt["id"] == "T-001" and nxt["phase"] == "ready" and nxt["title"] == "Write docs"
    assert nxt["who"] == "" and nxt["who_kind"] == "" and nxt["of"] == 1
    assert w["summary"]["waiting"] == 2
    assert w["summary"]["waiting_on"][0] == {"id": "T-002", "title": "Review docs", "on": ["T-001"]}
    assert w["summary"]["reserved"] == [] and w["summary"]["posted"] == []
    assert w["empty"] is None


def test_empty_states_never_invent_dependencies(board):
    for p in board.glob("T-*.json"):
        p.unlink()
    w = _snap(board)["work"]
    assert w["empty"]["kind"] == "no_tickets"
    assert w["empty"]["cmd"].startswith("tickets plan")
    assert run(board, "create", "First", "--role", "docs", agent="planner").returncode == 0
    w = _snap(board)["work"]
    # one ticket: independent work is valid; no self-dependency example
    assert w["empty"]["kind"] == "no_edges" and w["empty"]["lead"] == "No dependencies yet."
    assert w["empty"]["cmd"] == "tickets plan" and "example" not in w["empty"]
    assert run(board, "create", "Second", "--role", "docs", agent="planner").returncode == 0
    w = _snap(board)["work"]
    ids = sorted(n["id"] for n in w["nodes"])
    assert w["empty"]["cmd"] == "tickets plan"
    assert w["empty"]["example"] == "tickets dep %s --after %s" % (ids[1], ids[0])


# --- T-892 item 2: reservation / posting / read / wake / claim are distinct ----

def test_reservation_only_is_reserved_not_posted():
    w, by = _pure([_t("T-001", reserved_for="bob")])
    n = by["T-001"]
    assert n["phase"] == "reserved" and n["dispatch"] is None
    assert n["evidence"] == "Reserved for @bob · no task posted · not claimed"
    assert n["who"] == "bob" and n["who_kind"] == "reserved"
    assert w["summary"]["next"]["who_kind"] == "reserved"


def test_posted_read_woken_and_claimed_carry_different_labels():
    msg = {"id": "m1", "kind": "task", "re": "T-001", "to": "bob", "from": "planner",
           "at": "2026-09-13T00:00:01Z", "text": "please take T-001"}
    # queued message, no receipts at all (ack callback missing -> unknown)
    _, by = _pure([_t("T-001")], [msg])
    assert by["T-001"]["phase"] == "posted"
    assert "Task posted to @bob by planner" in by["T-001"]["evidence"]
    assert "delivery unknown" in by["T-001"]["evidence"]
    # inbox not read
    _, by = _pure([_t("T-001")], [msg], acked=lambda who, m: False)
    assert "not read, wake unconfirmed" in by["T-001"]["evidence"]
    # inbox read is a read receipt, not an ACK, not a wake, not work
    _, by = _pure([_t("T-001")], [msg], acked=lambda who, m: True)
    assert "inbox read, not acknowledged" in by["T-001"]["evidence"]
    assert by["T-001"]["phase"] == "posted" and by["T-001"]["dispatch"]["wake"] is None
    # confirmed wake: the seat's wake receipt names this message id
    agents = {"bob": {"owner": "bob", "wake_delivery": {"message_id": "m1", "label": "woken",
                                                        "at": "2026-09-13T00:00:05Z"}}}
    _, by = _pure([_t("T-001")], [msg], acked=lambda who, m: True, agents=agents)
    assert by["T-001"]["dispatch"]["wake"]["confirmed"] is True
    assert "wake confirmed" in by["T-001"]["evidence"] and "not claimed" in by["T-001"]["evidence"]
    # a queued-offline wake label is shown as what it is, not as success
    agents["bob"]["wake_delivery"]["label"] = "queued-offline"
    _, by = _pure([_t("T-001")], [msg], acked=lambda who, m: True, agents=agents)
    assert by["T-001"]["dispatch"]["wake"]["confirmed"] is False
    assert "wake: queued-offline" in by["T-001"]["evidence"]
    assert "inbox read, not acknowledged" in by["T-001"]["evidence"]
    # a receipt for a different message proves nothing about this one
    agents["bob"]["wake_delivery"] = {"message_id": "other", "label": "woken", "at": T0}
    _, by = _pure([_t("T-001")], [msg], acked=lambda who, m: True, agents=agents)
    assert by["T-001"]["dispatch"]["wake"] is None
    # claimed: the claim is the evidence, nothing about a running process
    _, by = _pure([_t("T-001", status="claimed", owner="bob", claimed_at=T0)], [msg])
    assert by["T-001"]["phase"] == "working"
    assert by["T-001"]["evidence"].startswith("Claimed by @bob")
    assert by["T-001"]["dispatch"] is None


def test_inbox_read_stays_visible_beside_unconfirmed_wake():
    msg = {"id": "m9", "kind": "task", "re": "T-009", "to": "carol", "from": "planner",
           "at": "2026-09-13T00:00:01Z", "text": "please take T-009"}
    agents = {"carol": {"owner": "carol", "wake_delivery": {
        "message_id": "m9", "label": "no live endpoint", "at": "2026-09-13T00:00:02Z"}}}
    _, before = _pure([_t("T-009")], [msg], acked=lambda who, m: False, agents=agents)
    _, after = _pure([_t("T-009")], [msg], acked=lambda who, m: True, agents=agents)
    assert "wake: no live endpoint" in before["T-009"]["evidence"]
    assert "inbox read, not acknowledged" not in before["T-009"]["evidence"]
    assert "not read" in before["T-009"]["evidence"]
    ev = after["T-009"]["evidence"]
    assert "inbox read, not acknowledged" in ev
    assert "wake: no live endpoint" in ev
    assert ev != before["T-009"]["evidence"]
    assert work_view.delivery_text({"seen": True, "wake": {
        "label": "no live endpoint", "confirmed": False}}) == (
        "inbox read, not acknowledged · wake: no live endpoint")


def test_stale_task_post_before_reopen_or_to_another_seat_is_ignored():
    old = {"id": "m0", "kind": "task", "re": "T-001", "to": "carol", "from": "planner",
           "at": "2026-09-12T00:00:00Z", "text": "take T-001"}
    # reopened after the post: the post belongs to the previous life
    _, by = _pure([_t("T-001", reopened_at="2026-09-13T00:00:00Z", claimed_at="2026-09-12T01:00:00Z")], [old])
    n = by["T-001"]
    assert n["phase"] == "ready" and n["dispatch"] is None and n["stale_posts"] == 1
    assert "1 earlier task post ignored" in n["evidence"]
    # same UTC second, no event-order cutoff: unknown, not posted and not "no task posted"
    same = dict(old, id="m-eq", at="2026-09-13T00:00:00Z")
    _, by = _pure([_t("T-001", reopened_at="2026-09-13T00:00:00Z")], [same])
    n = by["T-001"]
    assert n["phase"] != "posted" and n["dispatch"] is None
    assert n["unknown_posts"] == 1 and n["stale_posts"] == 0
    assert "unknown order" in n["evidence"]
    assert "no task posted" not in n["evidence"]
    # reassigned by reservation: a post to another seat does not name the current recipient
    _, by = _pure([_t("T-001", reserved_for="bob")], [old])
    n = by["T-001"]
    assert n["phase"] == "reserved" and n["who"] == "bob" and n["stale_posts"] == 1
    assert n["evidence"].startswith("Reserved for @bob · no task posted")


def test_same_second_task_post_as_reopen_is_pre_reopen_history():
    """T-955: now() is whole seconds, so m.at == reopened_at is never current
    dispatch. With the ``reopened_seen`` cutoff (T-810 Sol FIX) the post is the
    previous life; without a cutoff it is unknown order, not silently stale."""
    epoch = "2026-09-14T12:00:00Z"
    same = {"id": "m-same", "kind": "task", "re": "T-001", "to": "carol",
            "from": "planner", "at": epoch, "text": "take T-001"}
    later = {"id": "m-new", "kind": "task", "re": "T-001", "to": "bob",
             "from": "planner", "at": "2026-09-14T12:00:01Z", "text": "take T-001 now"}
    _, by = _pure([_t("T-001", reopened_at=epoch, reopened_seen=["m-same"])], [same])
    n = by["T-001"]
    assert n["dispatch"] is None and n["stale_posts"] == 1
    _, by = _pure([_t("T-001", reopened_at=epoch)], [same])
    n = by["T-001"]
    assert n["dispatch"] is None and n["stale_posts"] == 0 and n["unknown_posts"] == 1
    _, by = _pure([_t("T-001", reopened_at=epoch, reserved_for="bob")], [same, later])
    n = by["T-001"]
    assert n["dispatch"]["to"] == "bob"


def test_same_second_trigger_as_reopen_is_ignored_but_same_second_as_done_counts():
    """T-955: a trigger in the reopen second is never current (previous life
    with a cutoff, unknown without one); parent done_at keeps < so a success
    trigger posted in the completion second still counts."""
    done = "2026-09-13T01:00:00Z"
    a = _t("T-001", status="done", owner="x", done_at=done)
    same_done = {"id": "m-eq", "kind": "task", "re": "T-002", "to": "bob", "from": "x",
                 "at": done, "text": "unblocked T-002 after T-001 -- start (success trigger)"}
    c = _t("T-002", status="claimed", owner="bob", deps=["T-001"],
           claimed_at="2026-09-13T01:05:00Z")
    _, by = _pure([a, c], [same_done])
    assert by["T-002"]["progress"]["trigger"]["to"] == "bob"
    epoch = "2026-09-13T02:00:00Z"
    child = _t("T-002", deps=["T-001"], reserved_for="bob", reopened_at=epoch)
    same_epoch = dict(same_done, id="m-ep", at=epoch)
    _, by = _pure([a, child], [same_epoch])
    assert by["T-002"]["progress"]["trigger"] is None


def test_reopen_stamps_reopened_at_so_old_posts_drop_out(board):
    _team(board)
    assert run(board, "msg", "take T-001", "--to", "bob", "--re", "T-001", "--task", agent="planner").returncode == 0
    by = _nodes(board)
    assert by["T-001"]["phase"] == "posted" and by["T-001"]["dispatch"]["to"] == "bob"
    assert run(board, "next", agent="bob").returncode == 0
    r = run(board, "reopen", "T-001", "--notes", "bob lost the seat", agent="planner")
    assert r.returncode == 0, r.stdout + r.stderr
    t = json.loads((board / "T-001.json").read_text())
    assert t["reopened_at"] and t["status"] == "open"
    by = _nodes(board)
    n = by["T-001"]
    assert n["phase"] == "ready" and n["dispatch"] is None and n["stale_posts"] == 1
    assert n["reopened_at"] == t["reopened_at"]
    assert "reopened_seen" in t and isinstance(t["reopened_seen"], list)
    assert t["reopened_seen"]  # the pre-reopen task post is in the cutoff


def test_same_second_post_order_uses_seen_cutoff_not_uuid():
    # ids chosen so lexical UUID order would get the chronology backwards
    before = {"id": "zzz-after-lexically", "kind": "task", "re": "T-001", "to": "carol",
              "from": "planner", "at": T0, "text": "take T-001 before reopen"}
    after = {"id": "aaa-before-lexically", "kind": "task", "re": "T-001", "to": "alice",
             "from": "planner", "at": T0, "text": "take T-001 after reopen"}
    # post-before-reopen: id recorded in the cutoff, even though it sorts last
    _, by = _pure([_t("T-001", reopened_at=T0, reopened_seen=["zzz-after-lexically"])], [before])
    n = by["T-001"]
    assert n["phase"] == "ready" and n["dispatch"] is None and n["stale_posts"] == 1
    assert n["unknown_posts"] == 0
    assert "1 earlier task post ignored" in n["evidence"]
    # post-after-reopen: id not in the cutoff, even though it sorts first
    _, by = _pure([_t("T-001", reopened_at=T0, reopened_seen=["zzz-after-lexically"])], [after])
    n = by["T-001"]
    assert n["phase"] == "posted" and n["dispatch"]["to"] == "alice"
    assert n["stale_posts"] == 0 and n["unknown_posts"] == 0
    assert "Task posted to @alice" in n["evidence"]
    # both lives in one log: current post wins; previous-life still counted
    _, by = _pure([_t("T-001", reopened_at=T0, reopened_seen=["zzz-after-lexically"])],
                  [before, after])
    n = by["T-001"]
    assert n["phase"] == "posted" and n["dispatch"]["to"] == "alice"
    assert n["stale_posts"] == 1 and n["unknown_posts"] == 0
    assert "1 earlier task post ignored" in n["evidence"]


def test_reopen_then_immediate_post_is_current_even_same_second(board):
    _team(board)
    assert run(board, "msg", "take T-001", "--to", "bob", "--re", "T-001", "--task",
               agent="planner").returncode == 0
    assert run(board, "reopen", "T-001", "--notes", "bob lost the seat",
               agent="planner").returncode == 0
    t = json.loads((board / "T-001.json").read_text())
    seen = list(t.get("reopened_seen") or [])
    assert seen
    assert run(board, "msg", "take T-001 now", "--to", "alice", "--re", "T-001", "--task",
               agent="planner").returncode == 0
    t2 = json.loads((board / "T-001.json").read_text())
    by = _nodes(board)
    n = by["T-001"]
    assert n["phase"] == "posted" and n["dispatch"]["to"] == "alice"
    assert n["stale_posts"] == 1
    # new post is not in the reopen cutoff even if at == reopened_at
    msgs = [json.loads(ln) for ln in (board / "messages.jsonl").read_text().splitlines() if ln.strip()]
    newest = [m for m in msgs if m.get("re") == "T-001" and m.get("to") == "alice"][-1]
    assert work_view._msg_id(newest) not in seen
    assert t2.get("reopened_at") == t["reopened_at"]


# --- T-892 item 3: the success-to-next story --------------------------------

def _parents_child(child_kw=None, msgs=(), agents=None):
    a = _t("T-001", status="done", owner="x", done_at="2026-09-13T01:00:00Z",
           notes=[{"by": "x", "at": "2026-09-13T01:00:00Z", "text": "a done"}])
    b = _t("T-002", status="open")
    c = _t("T-003", deps=["T-001", "T-002"], **(child_kw or {}))
    return _pure([a, b, c], msgs, agents=agents,
                 edges=[{"from": "T-001", "to": "T-003", "waiting": False},
                        {"from": "T-002", "to": "T-003", "waiting": True}])


def test_child_with_one_of_two_parents_done_is_never_freed():
    _, by = _parents_child()
    n = by["T-003"]
    assert n["phase"] == "waiting"
    p = n["progress"]
    assert p["parent"] == "T-001" and p["pending"] == ["T-002"]
    assert p["all_done"] is False and p["became_ready"] is False
    assert n["evidence"] == "Dependency T-001 completed %s; still waiting on T-002" % work_view._fmt_age(p["age_h"])
    # the parent side agrees
    starts = by["T-001"]["starts"]
    assert starts[0]["id"] == "T-003" and starts[0]["freed"] is False
    assert starts[0]["still_waiting_on"] == ["T-002"]
    assert "Freed" not in work_view.WORK_JS and "freed when" not in work_view.WORK_JS.lower()


def test_capture_and_hold_gate_readiness_after_all_deps_finish():
    a = _t("T-001", status="done", owner="x", done_at="2026-09-13T01:00:00Z")
    held = _t("T-002", deps=["T-001"], body="HOLD until Monday")
    cap = _t("T-003", deps=["T-001"], lane="capture")
    _, by = _pure([a, held, cap])
    assert by["T-002"]["phase"] == "hold"
    assert by["T-002"]["progress"]["all_done"] is True
    assert by["T-002"]["progress"]["gate"] == "hold" and by["T-002"]["progress"]["became_ready"] is False
    assert by["T-003"]["phase"] == "capture"
    assert by["T-003"]["progress"]["gate"] == "capture" and by["T-003"]["progress"]["became_ready"] is False
    starts = dict((s["id"], s) for s in by["T-001"]["starts"])
    assert starts["T-002"]["freed"] is False and starts["T-002"]["gate"] == "hold"
    assert starts["T-003"]["freed"] is False and starts["T-003"]["gate"] == "capture"


def test_old_claim_after_reopen_is_history_not_current_work():
    a = _t("T-001", status="done", owner="x", done_at="2026-09-13T01:00:00Z")
    # reopened child keeps its old claimed_at (cmd_reopen clears owner only)
    c = _t("T-002", deps=["T-001"], claimed_at="2026-09-12T00:00:00Z", reopened_at="2026-09-13T02:00:00Z")
    _, by = _pure([a, c])
    n = by["T-002"]
    assert n["phase"] == "ready" and n["progress"]["became_ready"] is True
    assert n["progress"]["claim"] == {"at": "2026-09-12T00:00:00Z", "age_h": n["progress"]["claim"]["age_h"],
                                      "by": "", "current": False, "causality": "stale"}
    assert by["T-001"]["starts"][0]["began"] is False and by["T-001"]["starts"][0]["who"] == ""
    # a current claim whose stamp predates the completion is not attributed to it
    c2 = _t("T-002", status="claimed", owner="bob", deps=["T-001"], claimed_at="2026-09-13T00:30:00Z")
    _, by = _pure([a, c2])
    assert by["T-002"]["progress"]["claim"]["causality"] == "predates"
    s = by["T-001"]["starts"][0]
    assert s["began"] is False and s["claim_predates"] is True and s["who_kind"] == "claimed"


def test_trigger_causality_needs_matching_completion_then_claim():
    a = _t("T-001", status="done", owner="x", done_at="2026-09-13T01:00:00Z")
    trig = {"id": "m1", "kind": "task", "re": "T-002", "to": "bob", "from": "x",
            "at": "2026-09-13T01:00:01Z", "text": "unblocked T-002 after T-001 -- start (success trigger)"}
    # claim after the trigger -> attributed
    c = _t("T-002", status="claimed", owner="bob", deps=["T-001"], claimed_at="2026-09-13T01:05:00Z")
    _, by = _pure([a, c], [trig])
    p = by["T-002"]["progress"]
    assert p["trigger"]["to"] == "bob" and p["claim"]["causality"] == "after_trigger"
    # a historical trigger from an earlier completion of the same parent does not count
    old_trig = dict(trig, id="m0", at="2026-09-12T01:00:01Z")
    _, by = _pure([a, c], [old_trig])
    p = by["T-002"]["progress"]
    assert p["trigger"] is None and p["claim"]["causality"] == "unverified"
    # a trigger message text that names another parent is not this parent's trigger
    other = dict(trig, id="m2", text="unblocked T-002 after T-009 -- start (success trigger)")
    _, by = _pure([a, c], [other])
    assert by["T-002"]["progress"]["trigger"] is None
    # posted trigger, not yet claimed: not begun, and the parent never says told
    open_child = _t("T-002", deps=["T-001"], reserved_for="bob")
    _, by = _pure([a, open_child], [trig], acked=lambda who, m: False)
    assert by["T-002"]["phase"] == "posted" and by["T-002"]["progress"]["claim"] is None
    s = by["T-001"]["starts"][0]
    assert s["who_kind"] == "posted" and s["began"] is False
    assert "told" not in work_view.WORK_JS


def test_same_second_trigger_order_uses_seen_cutoff_not_uuid():
    a = _t("T-001", status="done", owner="x", done_at=T0)
    # lexical order of these ids is the opposite of event order
    after = {"id": "aaa-before-lexically", "kind": "task", "re": "T-002", "to": "bob",
             "from": "x", "at": T0,
             "text": "unblocked T-002 after T-001 -- start (success trigger)"}
    before = dict(after, id="zzz-after-lexically")
    c = _t("T-002", status="claimed", owner="bob", deps=["T-001"],
           claimed_at="2026-09-13T00:00:01Z", reopened_at=T0,
           reopened_seen=["zzz-after-lexically"])
    _, by = _pure([a, c], [after])
    p = by["T-002"]["progress"]
    assert p["trigger"]["to"] == "bob" and p["trigger"]["msg_id"] == "aaa-before-lexically"
    assert p["trigger_unknown"] == 0
    assert p["claim"]["causality"] == "after_trigger"
    # same-second trigger already in the cutoff is previous-life, not current
    open_child = _t("T-002", deps=["T-001"], reopened_at=T0,
                    reopened_seen=["zzz-after-lexically"])
    _, by = _pure([a, open_child], [before])
    p = by["T-002"]["progress"]
    assert p["trigger"] is None and p["trigger_unknown"] == 0
    # equal timestamp, no cutoff: unknown, not a current trigger
    claimed = _t("T-002", status="claimed", owner="bob", deps=["T-001"],
                 claimed_at="2026-09-13T00:00:01Z", reopened_at=T0)
    _, by = _pure([a, claimed], [after])
    p = by["T-002"]["progress"]
    assert p["trigger"] is None and p["trigger_unknown"] == 1
    assert p["claim"]["causality"] == "unverified"


# --- T-892 item 4: review evidence for the exact artifact --------------------

def _rev(status, commit, notes):
    return _t("T-001", status=status, owner="alice", commit=commit,
              notes=[{"by": by, "at": at, "text": text} for by, at, text in notes])


def test_review_verdicts_fix_accept_none_superseded_and_marked_done():
    sub = ("alice", "2026-09-13T01:00:00Z", "REVIEW: br@abc1234 -- paths: x")
    # no verdict yet
    _, by = _pure([_rev("review", "br@abc1234", [sub])])
    r = by["T-001"]["review"]
    assert r["artifact"] == "abc1234" and r["latest"] is None
    assert r["label"] == "Awaiting review of abc1234 · no verdict recorded"
    # FIX on the exact artifact is shown, not hidden behind 'awaiting review'
    fix = ("cos", "2026-09-13T01:10:00Z", "Verdict: **REQUEST FIX** for T-001 (`abc1234`): tests missing")
    _, by = _pure([_rev("review", "br@abc1234", [sub, fix])])
    r = by["T-001"]["review"]
    assert r["latest"]["kind"] == "FIX" and r["latest"]["applies"] == "exact" and r["latest"]["by"] == "cos"
    assert r["label"] == "FIX requested by @cos on abc1234"
    assert by["T-001"]["verdict"] == r["label"]
    # resubmitted on a new SHA: the FIX is historical, the new artifact awaits review
    sub2 = ("alice", "2026-09-13T02:00:00Z", "REVIEW: br@def5678 -- fixed tests")
    _, by = _pure([_rev("review", "br@def5678", [sub, fix, sub2])])
    r = by["T-001"]["review"]
    assert r["latest"] is None
    assert r["history"][0]["kind"] == "FIX" and r["history"][0]["applies"] == "superseded"
    assert r["label"] == "Awaiting review of def5678 · no verdict recorded · earlier FIX by @cos on abc1234 superseded"
    # T-944: prose ACCEPT is an unstructured note, never a verdict
    acc = ("cos", "2026-09-13T02:10:00Z", "verdict: ACCEPT def5678 -- good")
    _, by = _pure([_rev("review", "br@def5678", [sub, fix, sub2, acc])])
    r = by["T-001"]["review"]
    assert r["verified"] is False
    assert "Accepted" not in r["label"]
    assert "unstructured note" in r["label"]
    # structured accept on the exact artifact
    t_acc = _rev("review", "br@def5678", [sub, fix, sub2])
    t_acc["review_events"] = [{"kind": "accept", "by": "cos", "at": "2026-09-13T02:10:00Z",
                               "sha": "def5678", "notes": "good"}]
    _, by = _pure([t_acc])
    r = by["T-001"]["review"]
    assert r["label"] == "Accepted by @cos on def5678" and r["verified"] is True
    # a verdict that names no SHA is recorded evidence of unknown applicability
    vague = ("cos", "2026-09-13T02:20:00Z", "Verdict: request fix -- see thread")
    _, by = _pure([_rev("review", "br@def5678", [sub2, vague])])
    r = by["T-001"]["review"]
    assert r["latest"]["applies"] == "unknown"
    assert r["label"] == "FIX requested by @cos on def5678 (artifact not identified in the note)"
    # marked done without acceptance/main evidence stays that way
    _, by = _pure([_rev("done", "br@def5678", [sub2])])
    assert by["T-001"]["review"]["label"] == "Marked done; verification not recorded"
    assert by["T-001"]["review"]["verified"] is False
    # tickets merge's own note is main evidence for that pin
    merged = ("planner", "2026-09-13T03:00:00Z", "merged into main as 9999999 (tickets merge; pinned def5678)")
    _, by = _pure([_rev("done", "br@def5678", [sub2, acc, merged])])
    assert by["T-001"]["review"]["label"] == "Merged into main as def5678"
    assert by["T-001"]["review"]["verified"] is True
    # prose mentioning acceptance criteria is not a verdict
    prose = ("alice", "2026-09-13T01:05:00Z", "acceptance criteria updated; not accepted yet by anyone")
    _, by = _pure([_rev("claimed", "", [prose])])
    assert by["T-001"]["review"]["latest"] is None and by["T-001"]["review"]["label"] == ""


def test_review_verdict_can_arrive_as_a_board_message_about_the_ticket():
    t = _rev("review", "br@abc1234", [("alice", "2026-09-13T01:00:00Z", "REVIEW: br@abc1234 -- x")])
    msg = {"id": "m1", "re": "T-001", "to": "alice", "from": "cos", "at": "2026-09-13T01:30:00Z",
           "text": "verdict: FIX abc1234 -- the test still fails"}
    _, by = _pure([t], [msg])
    r = by["T-001"]["review"]
    assert r["latest"]["kind"] == "FIX" and r["latest"]["source"] == "msg" and r["latest"]["by"] == "cos"
    assert r["label"] == "FIX requested by @cos on abc1234"
    # the same verdict recorded as a note and echoed as a message is one entry, not history
    t["notes"].append({"by": "cos", "at": "2026-09-13T01:30:00Z", "text": "verdict: FIX abc1234 -- the test still fails"})
    _, by = _pure([t], [msg])
    assert by["T-001"]["review"]["history"] == []


# --- T-892 item 5: module support for accessibility and shell integration -----

def test_module_uses_semantic_status_tokens_and_readable_light_focus():
    css = work_view.WORK_CSS
    # status colours never borrow brass; brass stays for actions and focus
    assert ".ph-posted{--wv-c:var(--progress)}" in css and ".ph-waiting{--wv-c:var(--mute)}" in css
    assert "--wv-c:var(--acc)" not in css
    # light theme gets a darker action/focus token (7.3:1 on the light card)
    assert "body[data-theme=light] .wv{--wv-acc:#6b4f14;--wv-focus:#6b4f14}" in css
    assert "outline:2px solid var(--wv-focus)" in css
    js = work_view.WORK_JS
    # polling preserves the Graph/List control focus, not just node/close focus
    assert "data-wv-mode=\"'+focusMode+'\"" in js and "active.dataset.wvMode" in js
    # no whole-detail live region; one small region announces selection changes only
    assert 'aria-live="polite" hidden' not in js and "data-wv-live" in js
    assert "if(SEL!==prev)announce(" in js
    # selection event carries who to address so the shell's composer can follow it
    assert "atman:work-select" in js and "to:n?(n.who_kind==='suggested'?'':n.who):''" in js
    # stacked layout reaches the detail and returns to the node
    assert "data-wv-back" in js and "scrollIntoView" in js
    assert "Follow the work. Select a ticket for its blockers, handoff, and review." in js


def test_initial_deeplink_or_stored_sel_notifies_shell_once():
    js = work_view.WORK_JS
    assert "q.get('work')" in js and "LS_SEL" in js
    assert "function emitWorkSelect" in js
    assert "emitWorkSelect({initial:true})" in js
    assert "if(SEL!==SHELL_SEL)" in js
    assert "initial:!!opts.initial" in js
    # click path still emits without the restore flag; restore does not focus
    assert "emitWorkSelect();" in js
    assert "opts.focus&&SEL" in js
    # wake label and inbox-read stay composed in the module copy
    assert "inbox read, not acknowledged" in js
    assert "hasWake?'not read':'not read, wake unconfirmed'" in js


def test_work_payload_is_pure_and_survives_bad_ack():
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

    w = work_view.work_payload(tickets, graph, msgs, objective={"text": "o"}, acked=boom,
                               agents={"bob": {"wake_delivery": "garbage"}})
    child = dict((n["id"], n) for n in w["nodes"])["T-002"]
    assert child["phase"] == "posted"
    assert child["dispatch"]["seen"] is None
    assert "delivery unknown" in child["evidence"]
    assert child["progress"]["trigger"]["seen"] is None
    assert child["handoff"][0]["text"] == "handoff here"
    assert w["layers"] == [["T-001"], ["T-002"]]
