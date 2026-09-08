"""T-611: objective exit criteria, deterministic wake gates, one-run watchers."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from test_wakeup import board, pending, run, stop  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


# ---- 1. objectives: measurable exit + terminal state ----------------------

def test_objective_without_exit_is_flagged_not_rejected(board):
    r = run(board, "objective", "Ship V1", agent="boss")
    assert r.returncode == 0, r.stderr
    assert "FLAG" in r.stdout and "exit" in r.stdout.lower()
    rec = json.loads((board / "objective.json").read_text())
    assert rec["text"] == "Ship V1"
    assert rec.get("state") == "active"
    assert rec.get("exit_missing") is True
    out = run(board, "objective", agent="boss").stdout
    assert "FLAG" in out and "Ship V1" in out


def test_objective_with_exit_is_clean_and_legacy_done_maps_to_achieved(board):
    r = run(board, "objective", "Ship V1", "--exit", "demo recorded and review queue empty",
            agent="boss")
    assert r.returncode == 0, r.stderr
    assert "FLAG" not in r.stdout
    rec = json.loads((board / "objective.json").read_text())
    assert rec["exit_criterion"] == "demo recorded and review queue empty"
    assert rec.get("exit_missing") is False
    assert rec.get("state") == "active"
    assert run(board, "objective", "--done", "gates green", agent="boss").returncode == 0
    rec = json.loads((board / "objective.json").read_text())
    assert rec.get("state") == "achieved" and rec.get("done") is True
    assert "ACHIEVED" in run(board, "objective", agent="boss").stdout


def test_objective_blocked_and_replaced_are_terminal(board):
    run(board, "objective", "Ship V1", "--exit", "gates green", agent="boss")
    assert run(board, "objective", "--blocked", "waiting on creds", agent="boss").returncode == 0
    rec = json.loads((board / "objective.json").read_text())
    assert rec["state"] == "blocked" and rec.get("done") is not True
    run(board, "objective", "Ship V1.1", "--exit", "new demo", agent="boss")
    assert run(board, "objective", "--replaced", "cut scope to V1.1", agent="boss").returncode == 0
    rec = json.loads((board / "objective.json").read_text())
    assert rec["state"] == "replaced"


def test_legacy_objective_json_without_exit_still_loads(board):
    (board / "objective.json").write_text(json.dumps({
        "text": "old goal", "set_by": "boss", "at": "2026-01-01T00:00:00Z", "done": False,
    }))
    out = run(board, "objective", agent="boss").stdout
    assert "old goal" in out and "FLAG" in out
    (board / "objective.json").write_text(json.dumps({
        "text": "old goal", "set_by": "boss", "at": "2026-01-01T00:00:00Z",
        "done": True, "evidence": "shipped",
    }))
    out = run(board, "objective", agent="boss").stdout
    assert "ACHIEVED" in out or "MET" in out


# ---- 2. wake gates: notify vs task / stuck / holding / ready --------------

def test_ordinary_dm_and_ack_are_notification_only(board):
    run(board, "join", "bob", "--roles", "backend")
    time.sleep(1.1)
    run(board, "msg", "bob please look", "--to", "bob", agent="alice")
    rc, p = pending(board, "bob")
    assert rc == 1, p
    assert p.get("pending") is False
    assert p.get("messages_to_me"), p
    assert p.get("wake_reason") == "none"
    run(board, "inbox", agent="bob")
    time.sleep(1.1)
    run(board, "msg", "ACK got it", "--to", "bob", agent="alice")
    rc, p = pending(board, "bob")
    assert rc == 1 and p.get("pending") is False, p


def test_task_flag_and_task_prefix_wake(board):
    run(board, "join", "bob", "--roles", "backend")
    time.sleep(1.1)
    run(board, "msg", "please review the diff", "--to", "bob", "--task", agent="alice")
    rc, p = pending(board, "bob")
    assert rc == 0 and p.get("pending") is True, p
    assert p.get("task_messages") and p.get("wake_reason") == "task_messages"
    recs = [json.loads(ln) for ln in (board / "messages.jsonl").read_text().splitlines() if ln.strip()]
    task_rec = next(m for m in recs if "please review the diff" in m.get("text", ""))
    assert task_rec.get("kind") == "task"
    assert task_rec.get("task") is not True
    run(board, "inbox", agent="bob")
    time.sleep(1.1)
    run(board, "msg", "task: take T-001", "--to", "bob", agent="alice")
    rc, p = pending(board, "bob")
    assert rc == 0 and p.get("task_messages"), p
    (board / "messages.jsonl").write_text(
        (board / "messages.jsonl").read_text()
        + json.dumps({
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "from": "alice", "to": "bob", "re": "", "text": "legacy task flag",
            "task": True,
        }) + "\n")
    rc, p = pending(board, "bob")
    assert rc == 0 and p.get("task_messages"), p
    assert any("legacy task flag" in t for t in p["task_messages"])


def test_stuck_held_and_ready_assigned_still_wake(board):
    run(board, "join", "doc", "--roles", "docs")
    rc, p = pending(board, "doc")
    assert rc == 0 and p.get("ready_in_my_lane"), p
    assert p.get("wake_reason") == "ready_in_my_lane"
    assert run(board, "next", agent="doc").returncode == 0
    rc, p = pending(board, "doc")
    assert rc == 0 and p.get("holding"), p
    assert p.get("wake_reason") == "holding"
    run(board, "master", "take", agent="boss")
    run(board, "msg", "stuck: cannot commit", "--to", "boss", agent="doc")
    rc, p = pending(board, "boss")
    assert rc == 0 and p.get("stuck_messages"), p
    assert p.get("wake_reason") == "stuck_messages"


def test_review_queue_alone_does_not_wake_master(board):
    run(board, "master", "take", agent="boss")
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    repo = board.parent
    (repo / ".gitignore").write_text(".tickets/\n.worktrees/\n")
    genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "ignore board"],
                   check=True, env=genv)
    subprocess.run(["git", "-C", str(repo), "switch", "-q", "-c", "doc"], check=True)
    r = run(board, "review", "T-001", "--notes", "done", agent="doc", cwd=repo)
    assert r.returncode == 0, r.stderr
    rc, p = pending(board, "boss")
    assert "review_queue" in p, p
    assert p.get("pending") is False and rc == 1, p


def test_heartbeat_requires_exit_criterion(board):
    run(board, "master", "take", agent="boss")
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    run(board, "objective", "Ship V1", agent="boss")
    run(board, "watch", "--once", "--heartbeat", "30", "--dry-run", agent="boss")
    rc, p = pending(board, "boss")
    assert "drive" not in p, p
    run(board, "objective", "Ship V1", "--exit", "gates green", agent="boss")
    rec = json.loads((board / "agents" / "boss.json").read_text())
    rec.pop("drive_at", None)
    (board / "agents" / "boss.json").write_text(json.dumps(rec))
    run(board, "watch", "--once", "--heartbeat", "30", "--dry-run", agent="boss")
    rc, p = pending(board, "boss")
    assert "drive" in p and p["pending"] is True, p


def test_pending_force_overrides_empty_gates(board):
    run(board, "join", "bob", "--roles", "backend")
    r = run(board, "pending", "--agent", "bob", "--json")
    assert r.returncode == 1
    r = run(board, "pending", "--agent", "bob", "--force", "--json")
    p = json.loads(r.stdout)
    assert r.returncode == 0 and p["pending"] is True and p.get("forced") is True


# ---- 3. prompts: one bounded outcome --------------------------------------

def test_master_and_cos_prompts_are_one_batch(board):
    run(board, "master", "take", agent="planner")
    run(board, "master", "cos", "cos-x", agent="planner")
    mp = run(board, "prompt", "--master", "--agent", "planner").stdout
    assert "ONE concrete outcome" in mp
    assert "Do not invent" in mp or "ask for one" in mp.lower()
    cp = run(board, "prompt", "--cos", "--agent", "cos-x").stdout
    assert "CHIEF OF STAFF" in cp
    assert "Do not invent" in cp or "do not ask for, set, replace, or invent" in cp.lower()
    assert "one bounded" in cp.lower() or "ONE concrete" in cp
    low = cp.lower()
    assert "ask for one" not in low
    assert "tickets objective" not in low
    assert "--exit" not in cp
    assert "set, replace, or invent" in low


def test_stop_condition_names_unattended_watchers_only(board):
    run(board, "join", "bob", "--roles", "backend")
    p = json.loads(run(board, "pending", "--agent", "bob", "--json").stdout)
    sc = (p.get("stop_condition") or "").lower()
    assert "unattended" in sc and "watch" in sc
    assert "one model run" in sc
    assert "interactive" in sc and "codex" in sc and "claude" in sc


# ---- 4. watchers: default one model run; persist is explicit --------------

def test_watch_defaults_to_one_run_then_stops(board):
    run(board, "join", "doc", "--roles", "docs")
    r = run(board, "watch", "--agent", "doc", "--every", "5", "--exec", "true")
    assert r.returncode == 0, r.stderr
    assert "max-runs reached" in r.stdout
    assert "wake=" in r.stdout and "stop=" in r.stdout
    assert not (board / "agents" / "doc.watch.pid").exists()


def test_watch_persist_loops_until_sigterm(board):
    run(board, "join", "doc", "--roles", "docs")
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="doc",
               HOME=str(board.parent.parent / "home"))
    proc = subprocess.Popen(
        [sys.executable, str(TOOL), "watch", "--agent", "doc", "--every", "3600",
         "--persist", "--exec", "true"],
        env=env, cwd=str(board.parent), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(0.6)
    assert proc.poll() is None, "persist watcher must still be running"
    t0 = time.time()
    proc.terminate()
    proc.wait(timeout=5)
    assert time.time() - t0 < 2.0


def test_stop_hook_still_blocks_on_held_ticket_not_on_ack_mail(board):
    run(board, "join", "bob", "--roles", "backend")
    time.sleep(1.1)
    run(board, "msg", "ACK thanks", "--to", "bob", agent="alice")
    assert stop(board, "bob") == {}
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    out = stop(board, "doc", {"stop_hook_active": False})
    assert out.get("decision") == "block"


# ---- 5. CLI/UI surface wake + stop + objective state ----------------------

def test_ui_snapshot_exposes_objective_gates(board):
    run(board, "objective", "Ship V1", "--exit", "demo recorded", agent="boss")
    snap = json.loads(run(board, "ui", "--json").stdout)
    obj = snap.get("objective") or {}
    assert obj.get("text") == "Ship V1"
    assert obj.get("state") == "active"
    assert obj.get("exit_criterion") == "demo recorded"
    assert "task" in (obj.get("wake_gates") or "").lower()
    sc = (obj.get("stop_condition") or "").lower()
    assert "one model run" in sc and "unattended" in sc
    html = TOOL.read_text(encoding="utf-8")
    assert 'id="promiseStripLine"' in html
    assert "wakeGates" in html or "exit_criterion" in html or "objective.state" in html
