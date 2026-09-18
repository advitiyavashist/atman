"""T-1074: warm board.json on a 200-run board must stay under 1s.

The UI refreshes /board.json every 5s. On origin/main a 200-run board spent
~50s inside pending_work because each seat re-parsed every ticket file and
the whole messages.jsonl. Snapshot-scoped reuse plus one address index is
the fix; this file keeps both the timing and the pin load-bearing.
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
RUNS = 200
MSGS = 800
WARM_LIMIT_S = 1.0


def _load_tk():
    spec = importlib.util.spec_from_file_location("tickets_t1074_perf", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_200_run_board(root: Path) -> Path:
    """200 seats, 200 tickets, 200 run pairs, and a fat message log."""
    board = root / ".tickets"
    (board / "agents").mkdir(parents=True)
    workforce = {}
    tickets = []
    messages = []
    traj = []
    for i in range(RUNS):
        name = "run-%03d" % i
        tid = "T-%03d" % (i + 1)
        workforce[name] = {"harness": "cursor", "can": [], "cost": "low", "wake_mode": "task-only"}
        (board / "agents" / ("%s.json" % name)).write_text(json.dumps({
            "owner": name,
            "harness": "cursor",
            "joined_at": "2026-09-01T00:00:00Z",
            "inbox_seen": "2026-09-01T00:00:00Z",
            "seen": "2026-09-16T00:00:00Z",
            "ticket": tid if i % 7 == 0 else "",
        }))
        tickets.append({
            "id": tid,
            "title": "run work %03d" % i,
            "body": "",
            "role": "backend",
            "status": "claimed" if i % 7 == 0 else "open",
            "deps": [],
            "priority": 2,
            "epic": "",
            "sprint": "",
            "needs": [],
            "owner": name if i % 7 == 0 else "",
            "created": "2026-09-01T00:00:00Z",
            "updated": "2026-09-16T00:00:00Z",
            "notes": [],
        })
        (board / ("%s.json" % tid)).write_text(json.dumps(tickets[-1]))
        at0 = "2026-09-16T%02d:%02d:00Z" % (i // 60, i % 60)
        at1 = "2026-09-16T%02d:%02d:30Z" % (i // 60, i % 60)
        rid = "r-%s-1" % name
        traj.append({"v": 1, "at": at0, "kind": "run_start", "agent": name,
                     "run_id": rid, "ticket": tid})
        traj.append({"v": 1, "at": at1, "kind": "run_end", "agent": name,
                     "run_id": rid, "ticket": tid, "exit_code": 0})
    (board / "workforce.json").write_text(json.dumps(workforce))
    (board / "master.json").write_text(json.dumps({
        "owner": "run-000", "cos": "run-001", "taken_at": "2026-09-01T00:00:00Z",
    }))
    for n in range(MSGS):
        if n == 0:
            messages.append({
                "id": "m-direct-000",
                "at": "2026-09-16T12:00:00Z",
                "from": "run-000",
                "to": "run-005",
                "text": "task: finish the 200-run snapshot",
                "kind": "task",
                "mentions": [],
            })
            continue
        messages.append({
            "id": "m-b-%03d" % n,
            "at": "2026-09-15T%02d:%02d:00Z" % (n % 24, n % 60),
            "from": "run-%03d" % (n % RUNS),
            "to": "",
            "text": "broadcast %d on the 200-run board" % n,
            "kind": "message",
            "mentions": [],
        })
    (board / "messages.jsonl").write_text("".join(json.dumps(m) + "\n" for m in messages))
    (board / "trajectories.jsonl").write_text("".join(json.dumps(e) + "\n" for e in traj))
    return board


def test_reuse_pins_message_and_ticket_reads_once(tmp_path, monkeypatch):
    tk = _load_tk()
    board = _write_200_run_board(tmp_path)
    counts = {"messages": 0, "tickets": 0}
    real_msgs = tk._load_messages_from_disk
    real_tickets = tk._load_all_from_disk

    def wrap_msgs(path, include_archives=False):
        counts["messages"] += 1
        return real_msgs(path, include_archives)

    def wrap_tickets(path):
        counts["tickets"] += 1
        return real_tickets(path)

    monkeypatch.setattr(tk, "_load_messages_from_disk", wrap_msgs)
    monkeypatch.setattr(tk, "_load_all_from_disk", wrap_tickets)
    snap = tk.board_snapshot(str(board))
    assert counts["messages"] <= 2, counts
    assert counts["tickets"] == 1, counts
    assert snap["counts"]["total"] == RUNS
    target = [a for a in snap["agents"] if a["name"] == "run-005"]
    assert target and target[0]["wake_pending"] is True
    assert target[0]["wake_reason"] == "task_messages"


def test_200_run_board_json_warm_under_1s(tmp_path):
    tk = _load_tk()
    board = _write_200_run_board(tmp_path)
    tk.board_snapshot(str(board))
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        snap = tk.board_snapshot(str(board))
        times.append(time.perf_counter() - t0)
        assert snap["counts"]["total"] == RUNS
        assert len(snap["agents"]) == RUNS
    assert all(t < WARM_LIMIT_S for t in times), "warm board_snapshot: %s" % times


def test_inbox_rules_unchanged_outside_snapshot(board):
    """Watch/inbox still re-read disk; the pin is snapshot-only."""
    tk = _load_tk()
    run(board, "join", "alice", agent="alice")
    run(board, "join", "bob", agent="bob")
    run(board, "msg", "--to", "bob", "task: ping", agent="alice")
    pending = tk.pending_work(str(board), "bob")
    assert pending.get("task_messages") or pending.get("messages_to_me")
    unread = tk.unread(str(board), "bob")
    assert any("task: ping" in (m.get("text") or "") for m in unread)


def _pin_leak_agent(root: Path, *, limit):
    board = root / ".tickets"
    (board / "agents").mkdir(parents=True)
    path = board / "agents" / "alice.json"
    path.write_text(json.dumps({
        "owner": "alice",
        "inbox_seen": "2026-09-01T00:00:00Z",
        "limit": limit,
    }))
    return board, path


def test_write_during_pin_survives_agent_update(tmp_path):
    """A concurrent writer mid-snapshot must not be rolled back by _agent_update.

    board_snapshot pins agent records. If the in-lock read uses that pin, a
    later expire/write restamps the snapshot-time pre-image and drops the
    writer's watermark — the T-1074 reject.
    """
    tk = _load_tk()
    expired = {"reset_at": "2020-01-01T00:00:00+00:00", "note": "expired"}
    board, path = _pin_leak_agent(tmp_path, limit=expired)
    with tk._reuse_board_reads(str(board)):
        pinned = tk._agent_rec(str(board), "alice")
        assert pinned["inbox_seen"] == "2026-09-01T00:00:00Z"
        live = json.loads(path.read_text())
        live["inbox_seen"] = "2099-01-01T00:00:00Z"
        live["other"] = "from-writer"
        path.write_text(json.dumps(live))
        tk._active_seat_limit(str(board), "alice", pinned)

        def stamp(rec):
            rec["stamped"] = True

        assert tk._agent_update(str(board), "alice", stamp) is not None
    rec = json.loads(path.read_text())
    assert rec["inbox_seen"] == "2099-01-01T00:00:00Z"
    assert rec["other"] == "from-writer"
    assert rec["stamped"] is True
    assert "limit" not in rec
    assert rec.get("limit_expired_at") == expired["reset_at"]


def test_active_seat_limit_fallback_bypasses_pin(tmp_path):
    """After an aborted expire write, the fallback limit must come from disk."""
    tk = _load_tk()
    expired = {"reset_at": "2020-01-01T00:00:00+00:00", "note": "expired"}
    fresh = {"reset_at": "2099-01-01T00:00:00+00:00", "note": "new"}
    board, path = _pin_leak_agent(tmp_path, limit=expired)
    with tk._reuse_board_reads(str(board)):
        pinned = tk._agent_rec(str(board), "alice")
        live = json.loads(path.read_text())
        live["inbox_seen"] = "2099-01-01T00:00:00Z"
        live["limit"] = fresh
        path.write_text(json.dumps(live))
        got = tk._active_seat_limit(str(board), "alice", pinned)
    assert got == fresh
    rec = json.loads(path.read_text())
    assert rec["inbox_seen"] == "2099-01-01T00:00:00Z"
    assert rec["limit"] == fresh
