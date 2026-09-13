"""T-810: honest channel receipts, reserved ≠ dispatched, shell verdict."""

import json
from pathlib import Path

import tickets as tk
from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    return text[start:text.index('"""', start)]


def _snap(board):
    r = run(board, "ui", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_delivery_tags_never_call_inbox_read_acked():
    ui = _ui_html()
    chunk = ui[ui.index("function deliveryTags"):ui.index("function setConn")]
    assert "acked</span>" not in chunk
    assert "partial ack" not in chunk
    assert "inbox read, not acknowledged" in chunk
    assert "wake confirmed" in chunk
    assert "not read, wake unconfirmed" in chunk
    assert "inbox read ≠ ack" in ui or "never an agent ACK" in ui


def test_direct_message_receipts_keep_acked_compat_and_label_seen(board):
    assert run(board, "join", "sender", agent="sender").returncode == 0
    assert run(board, "join", "target", agent="target").returncode == 0
    assert run(board, "msg", "hello seat", "--to", "target", agent="sender").returncode == 0
    d = _snap(board)
    msg = next(m for m in d["messages"] if "hello seat" in m.get("text", ""))
    assert msg["delivery"]["status"] == "direct"
    assert msg["delivery"]["acks"] == [{"agent": "target", "acked": False}]
    rec = msg["delivery"]["receipts"][0]
    assert rec["agent"] == "target"
    assert rec["seen"] is False
    assert rec["label"] == "not read, wake unconfirmed"
    assert run(board, "inbox", agent="target").returncode == 0
    d2 = _snap(board)
    msg2 = next(m for m in d2["messages"] if "hello seat" in m.get("text", ""))
    assert msg2["delivery"]["acks"] == [{"agent": "target", "acked": True}]
    assert msg2["delivery"]["receipts"][0]["seen"] is True
    assert msg2["delivery"]["receipts"][0]["label"] == "inbox read, not acknowledged"


def test_reservation_is_reserved_not_dispatched_on_graph(board):
    assert run(board, "master", "take", agent="boss").returncode == 0
    assert run(board, "join", "alice", "--roles", "backend", agent="alice").returncode == 0
    assert run(board, "create", "Parked for alice", "--role", "backend",
               agent="boss").returncode == 0
    assert run(board, "reserve", "T-001", "--for", "alice", agent="boss").returncode == 0
    d = _snap(board)
    node = next(n for n in d["graph"]["nodes"] if n["id"] == "T-001")
    assert node["phase"] == "reserved"
    assert node["reserved_for"] == "alice"
    assert node["phase"] != "dispatched"


def test_shell_verdict_is_evidence_not_status(board):
    assert run(board, "master", "take", agent="boss").returncode == 0
    assert run(board, "create", "Needs a verdict", "--role", "backend",
               agent="boss").returncode == 0
    t = json.loads((board / "T-001.json").read_text())
    t["status"] = "review"
    t["commit"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    t["notes"] = [{"by": "rev", "at": "2026-09-13T00:00:00Z",
                   "text": "REVIEW: verdict FIX on aaaaaaa"}]
    (board / "T-001.json").write_text(json.dumps(t))
    assert tk._ticket_verdict(t).startswith("REVIEW: verdict FIX")
    assert "awaiting review" not in tk._ticket_verdict(t)
    done = dict(t, status="done", notes=[])
    assert tk._ticket_verdict(done) == "Marked done; verification not recorded"
    stale = dict(t, commit="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    assert "historical" in tk._ticket_verdict(stale)


def test_reopen_stamps_reopened_at_for_work_view(board):
    assert run(board, "master", "take", agent="boss").returncode == 0
    assert run(board, "create", "Will reopen", "--role", "backend", agent="boss").returncode == 0
    assert run(board, "next", agent="boss").returncode == 0
    assert run(board, "reopen", "T-001", "--notes", "wrong owner",
               agent="boss").returncode == 0
    t = json.loads((board / "T-001.json").read_text())
    assert t.get("reopened_at")
    assert t["status"] == "open"
