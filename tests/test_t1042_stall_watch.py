"""T-1042: STALLED is a measured silence, not a threshold label."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import stall_watch as sw  # noqa: E402
sys.path.insert(0, str(ROOT))
import tickets as tool  # noqa: E402


def test_should_mark_uses_measured_duration_not_threshold_label():
    assert sw.should_mark_stalled(0.0, 9.0, 10.0) is False
    assert sw.should_mark_stalled(0.0, 10.0, 10.0) is True
    assert sw.measured_stall_s(0.0, 12.5) == 12.5
    assert sw.should_mark_stalled(0.0, 100.0, 10.0, limited=True) is False
    rec = sw.stall_record("2026-09-16T10:00:00Z", sw.utcnow(), 612.4, pid=9)
    assert rec["measured_s"] == 612.4
    note = sw.stall_note("boss", 612.4, "2026-09-16T10:00:00Z")
    assert "612" in note and "threshold" not in note.lower()


def test_limited_liveness_is_not_relabelled_stalled(tmp_path):
    board = tmp_path / ".tickets"
    board.mkdir()
    rec = {
        "owner": "boss",
        "limit": {"at": "2026-09-16T10:00:00Z", "until": "later",
                  "note": "You've hit your session limit", "source": "provider",
                  "reset_at": (datetime.now(timezone.utc) + timedelta(days=365))
                  .strftime("%Y-%m-%dT%H:%M:%SZ")},
        "stall": {"at": "2026-09-16T10:05:00Z", "measured_s": 400,
                  "last_output_at": "2026-09-16T09:58:00Z", "source": "watch"},
    }
    (board / "agents").mkdir()
    (board / "agents" / "boss.json").write_text(json.dumps(rec))
    live = tool.agent_liveness(str(board), rec)
    assert live["state"] == "limited"
    assert live["state"] != "stalled"


def test_stalled_liveness_and_blocks_retrigger(tmp_path):
    board = tmp_path / ".tickets"
    board.mkdir()
    rec = {
        "owner": "boss",
        "stall": {"at": "2026-09-16T10:10:00Z", "measured_s": 640,
                  "last_output_at": "2026-09-16T10:00:00Z", "source": "watch"},
    }
    (board / "agents").mkdir()
    (board / "agents" / "boss.json").write_text(json.dumps(rec))
    tool._run_begin(str(board), "boss", 1, str(tmp_path))
    live = tool.agent_liveness(str(board), rec)
    assert live["state"] == "stalled"
    assert "640" in live["detail"]
    pending = tool.pending_work(str(board), "boss")
    assert pending.get("stalled") == 640
    assert tool.actionable(pending) is False


def _run_capped(tmp_path, cmd, **kwargs):
    log = tmp_path / "watch.log"
    log.write_text("")
    events = []
    env = dict(os.environ, TICKETS_STALL_SECS=kwargs.pop("thresh", "0.25"),
               TICKETS_STALL_CHECK_SECS=kwargs.pop("check", "0.05"))
    old = os.environ.copy()
    os.environ.update(env)
    try:
        rc, timed = tool._watch_run_capped(
            cmd, str(tmp_path), env, str(log),
            kwargs.pop("timeout_s", 4), 1_000_000,
            on_stall=lambda measured, pid: events.append(("stall", measured, pid)),
            on_stall_resolved=lambda: events.append(("resolved",)),
            **kwargs)
    finally:
        os.environ.clear()
        os.environ.update(old)
    return rc, timed, events, log


def test_silence_marks_stalled_with_measured_duration(tmp_path):
    cmd = "%s -c 'import time; time.sleep(1.0)'" % sys.executable
    rc, timed, events, _ = _run_capped(tmp_path, cmd, thresh="0.3", check="0.05")
    assert rc == 0 and timed is False
    stalls = [e for e in events if e[0] == "stall"]
    assert stalls, events
    assert stalls[0][1] >= 0.3
    assert stalls[0][1] < 2.0
    time.sleep(0.05)
    assert tool._stall_watch().pid_alive(stalls[0][2]) is False


def test_output_resume_resolves(tmp_path):
    cmd = ("%s -c 'import time,sys; print(\"hi\", flush=True); "
           "time.sleep(0.7); print(\"again\", flush=True); time.sleep(0.15)'"
           % sys.executable)
    rc, timed, events, _ = _run_capped(tmp_path, cmd, thresh="0.25", check="0.05")
    assert rc == 0
    kinds = [e[0] for e in events]
    assert "stall" in kinds
    assert "resolved" in kinds
    assert kinds.index("resolved") > kinds.index("stall")


def test_fast_healthy_run_is_never_stalled(tmp_path):
    cmd = "%s -c 'print(\"ok\")'" % sys.executable
    rc, timed, events, _ = _run_capped(tmp_path, cmd, thresh="2.0", check="0.5")
    assert rc == 0 and events == []


def test_limited_run_is_not_marked_stalled(tmp_path):
    cmd = "%s -c 'import time; time.sleep(0.8)'" % sys.executable
    rc, timed, events, _ = _run_capped(
        tmp_path, cmd, thresh="0.2", check="0.05", limited=True)
    assert rc == 0
    assert events == []


def test_stalled_run_end_clears_stall_so_next_tick_is_fresh(tmp_path):
    """Regression: stall written by on_stall must not survive run end.

    Reproduces the silent-dead-seat follow-up: `_record_watch_stall` (as
    on_stall does) then the run terminates. The next pending_work tick must
    re-evaluate fresh instead of inheriting a permanent STALLED block.
    """
    board = tmp_path / ".tickets"
    (board / "agents").mkdir(parents=True)
    owner = "boss"
    (board / "agents" / ("%s.json" % owner)).write_text(
        json.dumps({"owner": owner}))
    tool._run_begin(str(board), owner, 1, str(tmp_path))
    tool._record_watch_stall(str(board), owner, 640, pid=os.getpid())
    rec = tool._agent_rec(str(board), owner)
    assert rec.get("stall") and rec["stall"].get("measured_s") == 640
    live = tool.agent_liveness(str(board), rec)
    assert live["state"] == "stalled"
    assert "640" in live["detail"]
    pending = tool.pending_work(str(board), owner)
    assert pending.get("stalled") == 640
    assert tool.actionable(pending) is False

    tool._run_end(str(board), owner, 1, 0)

    rec = tool._agent_rec(str(board), owner)
    assert not rec.get("stall")
    pending = tool.pending_work(str(board), owner)
    assert "stalled" not in pending
    live = tool.agent_liveness(str(board), rec)
    assert live["state"] != "stalled"


def test_output_resume_clears_agent_stall_field(tmp_path):
    board = tmp_path / ".tickets"
    (board / "agents").mkdir(parents=True)
    owner = "boss"
    (board / "agents" / ("%s.json" % owner)).write_text(
        json.dumps({"owner": owner}))
    tool._run_begin(str(board), owner, 1, str(tmp_path))
    tool._record_watch_stall(str(board), owner, 400, pid=os.getpid())
    assert tool.pending_work(str(board), owner).get("stalled") == 400
    tool._resolve_watch_stall(str(board), owner)
    rec = tool._agent_rec(str(board), owner)
    assert not rec.get("stall")
    assert rec.get("stall_resolved")
    assert "stalled" not in tool.pending_work(str(board), owner)


def _dead_pid():
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


@pytest.mark.parametrize("scenario", ["no_run", "run_inactive", "dead_watcher", "dead_child"])
def test_orphaned_stall_does_not_block_or_render_stalled(tmp_path, scenario):
    """Regression: a watcher killed without its finally block (SIGKILL, OOM,
    reboot) leaves agent["stall"] behind. It must not block retrigger forever
    or show STALLED instead of the dead seat it is."""
    board = tmp_path / ".tickets"
    (board / "agents").mkdir(parents=True)
    owner = "boss"
    (board / "agents" / ("%s.json" % owner)).write_text(
        json.dumps({"owner": owner}))
    child = os.getpid()
    if scenario != "no_run":
        tool._run_begin(str(board), owner, 1, str(tmp_path))
    if scenario == "run_inactive":
        tool._run_beat(str(board), owner, active=False)
    elif scenario == "dead_watcher":
        tool._run_beat(str(board), owner, pid=_dead_pid())
    elif scenario == "dead_child":
        child = _dead_pid()
    tool._record_watch_stall(str(board), owner, 640, pid=child)
    rec = tool._agent_rec(str(board), owner)
    assert rec.get("stall")

    live = tool.agent_liveness(str(board), rec)
    assert live["state"] != "stalled"
    pending = tool.pending_work(str(board), owner)
    assert "stalled" not in pending
    assert not tool._agent_rec(str(board), owner).get("stall")


def test_timeout_kills_process_group(tmp_path):
    cmd = ("%s -c 'import time,os; print(os.getpid(), flush=True); time.sleep(30)'"
           % sys.executable)
    rc, timed, events, log = _run_capped(
        tmp_path, cmd, thresh="0.2", check="0.05", timeout_s=0.7)
    assert timed is True
    assert rc == 124
    text = log.read_text()
    pid = None
    for line in text.splitlines():
        if line.strip().isdigit():
            pid = int(line.strip())
            break
    if pid:
        assert tool._stall_watch().pid_alive(pid) is False
