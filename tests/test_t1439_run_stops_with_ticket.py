"""T-1439: a run must not outlive the ticket it was started for.

A seat that keeps working after its ticket was closed, handed to someone else
or parked burns quota on work nobody will take, and pushes into a PR that is
already dead. The watcher is the only party holding both the live child and
the board, so it is the one that has to look -- on every tick, not once at
launch.

The end-to-end test is the one that matters: a real `watch --persist` with a
real sleeping child, the ticket closed from another seat through the ordinary
CLI, and the run gone within one tick with the reason on the ticket.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_wakeup import board, run  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tickets as tool  # noqa: E402

TOOL = ROOT / "tickets.py"

# A live seat's own environment renames the agent and fakes a run number in
# every subprocess this file launches, which turns a healthy tree red several
# tests later. Clear it for the whole module (reported by steer-spec-claude-0921).
SEAT_ENV = ("TICKET_SEAT", "TICKET_AGENT", "TICKETS_DIR", "TICKETS_RUN_NO",
            "TICKETS_RUN_ID", "TICKET_SESSION_ID", "TICKETS_WATCH_PINNED",
            "TICKETS_PY", "TICKET_OWNER_GENERATION")


@pytest.fixture(autouse=True)
def _no_inherited_seat(monkeypatch):
    for var in SEAT_ENV:
        monkeypatch.delenv(var, raising=False)


def _ticket(status="claimed", owner="runner", generation=1, **extra):
    t = {"id": "T-001", "title": "Write docs", "status": status, "owner": owner,
         "owner_generation": generation, "notes": [],
         "owner_lease": {"generation": generation, "owner": owner}}
    t.update(extra)
    return t


# ---- which states take a ticket out of a seat's active set ---------------

def test_active_ticket_is_no_reason_to_stop():
    assert tool._run_abandon_reason(_ticket(), "runner", 1) == ("", "")


def test_review_is_never_a_stop():
    """The seat moves its OWN ticket to review from inside the run.

    Stopping there would kill every run at the exact moment it succeeded --
    `atm review` is the last thing a worker does before it exits.
    """
    assert tool._run_abandon_reason(_ticket(status="review"), "runner", 1) == ("", "")


def test_closed_ticket_stops_the_run():
    reason, detail = tool._run_abandon_reason(_ticket(status="done"), "runner", 1)
    assert reason == "closed"
    assert "done" in detail


def test_blocked_ticket_stops_the_run():
    reason, _ = tool._run_abandon_reason(_ticket(status="blocked"), "runner", 1)
    assert reason == "blocked"


def test_reassigned_ticket_stops_the_run():
    reason, detail = tool._run_abandon_reason(_ticket(owner="someone-else"), "runner", 1)
    assert reason == "reassigned"
    assert "someone-else" in detail


def test_released_ticket_stops_the_run():
    reason, detail = tool._run_abandon_reason(
        _ticket(status="open", owner=""), "runner", 1)
    assert reason == "reassigned"
    assert "none" in detail


def test_reclaim_under_the_run_is_superseded_even_under_the_same_name():
    """A->B->A: the name reads right, the lease this run holds does not."""
    reason, detail = tool._run_abandon_reason(_ticket(generation=3), "runner", 1)
    assert reason == "superseded"
    assert "1 -> 3" in detail
    # and the generation this run started at is not itself a stop
    assert tool._run_abandon_reason(_ticket(generation=1), "runner", 1) == ("", "")


def test_a_discarded_ticket_is_superseded_not_merely_reassigned():
    """`atm discard` clears the owner too; the record must name the cause."""
    reason, detail = tool._run_abandon_reason(
        _ticket(status="open", owner="", lane="discarded"), "runner", 1)
    assert reason == "superseded"
    assert "discarded" in detail


# ---- an unreadable board keeps the run -----------------------------------

def test_unreadable_ticket_keeps_the_run(tmp_path):
    b = tmp_path / ".tickets"
    b.mkdir()
    (b / "T-001.json").write_text("{not json")
    rec, err = tool._run_ticket_snapshot(str(b), "T-001")
    assert rec is None and err
    guard = tool._RunTicketGuard(str(b), "runner", "T-001", 1, every_s=0)
    assert guard.poll() == "", "a half-written ticket must never kill live work"
    assert guard.reason == ""


def test_missing_ticket_keeps_the_run(tmp_path):
    b = tmp_path / ".tickets"
    b.mkdir()
    guard = tool._RunTicketGuard(str(b), "runner", "T-404", 1, every_s=0)
    assert guard.poll() == ""


def test_guard_reads_the_board_at_most_once_per_interval(tmp_path):
    b = tmp_path / ".tickets"
    b.mkdir()
    (b / "T-001.json").write_text(json.dumps(_ticket()))
    clock = {"t": 0.0}
    guard = tool._RunTicketGuard(str(b), "runner", "T-001", 1, every_s=10,
                                 clock=lambda: clock["t"])
    assert guard.poll() == ""
    (b / "T-001.json").write_text(json.dumps(_ticket(status="done")))
    assert guard.poll() == "", "inside the interval the board is not re-read"
    clock["t"] = 10.0
    assert guard.poll() == "closed"


def test_guard_is_not_armed_on_a_ticket_that_already_left(tmp_path):
    """Otherwise a stale agent.json binding becomes a kill/relaunch loop:
    every fresh run woken by an unrelated message would die on its first tick.
    """
    b = tmp_path / ".tickets"
    b.mkdir()
    (b / "T-001.json").write_text(json.dumps(_ticket(status="done")))
    assert tool._arm_run_ticket_guard(str(b), "runner", "T-001") is None
    (b / "T-001.json").write_text(json.dumps(_ticket()))
    guard = tool._arm_run_ticket_guard(str(b), "runner", "T-001")
    assert guard is not None and guard.baseline_generation == 1
    assert tool._arm_run_ticket_guard(str(b), "runner", "") is None


# ---- the reason is written on the ticket ---------------------------------

def test_stop_reason_lands_on_the_ticket(board):
    run(board, "join", "runner", "--roles", "docs")
    assert run(board, "claim", "T-001", agent="runner").returncode == 0
    err = tool._record_run_stop_on_ticket(
        str(board), "runner", "T-001", 3, "closed", "status=done")
    assert err == "", err
    t = json.loads((board / "T-001.json").read_text())
    note = t["notes"][-1]
    assert note["by"] == "runner"
    assert "run stopped" in note["text"]
    assert "closed" in note["text"] and "status=done" in note["text"]
    assert "run 3" in note["text"]


def test_a_refused_note_is_reported_not_raised(tmp_path, monkeypatch):
    """`save()` answers a gated ticket with sys.exit. A watcher may not."""
    b = tmp_path / ".tickets"
    b.mkdir()
    (b / "T-001.json").write_text(json.dumps(_ticket()))

    def _refuse(*a, **k):
        raise SystemExit("T-001 is dependency-gated")

    monkeypatch.setattr(tool, "save", _refuse)
    err = tool._record_run_stop_on_ticket(str(b), "runner", "T-001", 1, "closed")
    assert "dependency-gated" in err


# ---- end to end: close a ticket while a fake seat runs -------------------

def _start_watch(board, agent, exec_cmd, extra_env=None, once=False):
    home = board.parent.parent / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent,
               HOME=str(home), TICKETS_RUN_TICKET_CHECK_SECS="1")
    env.pop("TICKETS_STOP_HOOK", None)
    for var in SEAT_ENV:
        env.pop(var, None)
    env["TICKETS_DIR"] = str(board)
    env["TICKET_AGENT"] = agent
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen(
        [sys.executable, str(TOOL), "watch", "--agent", agent, "--every", "1",
         "--once" if once else "--persist", "--exec", exec_cmd,
         "--cwd", str(board.parent)],
        env=env, cwd=str(board.parent),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    pid_file = board / "agents" / ("%s.watch.pid" % agent)
    deadline = time.time() + 15
    while time.time() < deadline and not pid_file.exists():
        assert proc.poll() is None, "watch exited before the test could use it"
        time.sleep(0.05)
    return proc


def _reap(proc):
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _wait_run_active(board, agent, timeout=25):
    run_file = board / "agents" / ("%s.run" % agent)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if run_file.exists():
            try:
                rec = json.loads(run_file.read_text())
            except ValueError:
                rec = {}
            if rec.get("active"):
                return rec
        time.sleep(0.1)
    pytest.fail("no run ever went active for %s" % agent)


def _wait_run_over(board, agent, timeout=20):
    run_file = board / "agents" / ("%s.run" % agent)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            rec = json.loads(run_file.read_text())
        except (ValueError, OSError):
            rec = {}
        if rec and not rec.get("active"):
            return rec
        time.sleep(0.1)
    return {}


def _traj(board, kind, agent):
    path = board / "trajectories.jsonl"
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == kind and r.get("agent") == agent:
                rows.append(r)
    return rows


def _wait_traj(board, kind, agent, timeout=20):
    """The run receipt flips to inactive BEFORE the run_end line is appended
    (usage is parsed in between), so reading the log once is a race."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = _traj(board, kind, agent)
        if rows:
            return rows[-1]
        time.sleep(0.1)
    pytest.fail("no %s event for %s" % (kind, agent))


SLEEPER = "%s -c 'import time; time.sleep(120)'" % sys.executable


def _close_as_master(board, tid="T-001", worker="runner"):
    """The real close: the worker submits, the master marks it done."""
    r = run(board, "status", tid, "review", "--notes", "paths, tests",
            "--force", agent=worker)
    assert r.returncode == 0, r.stderr
    r = run(board, "status", tid, "done", "--notes", "merged", agent="boss")
    assert r.returncode == 0, r.stderr


def test_closing_the_ticket_stops_the_run_within_a_tick(board):
    run(board, "join", "runner", "--roles", "docs")
    assert run(board, "claim", "T-001", agent="runner").returncode == 0
    proc = _start_watch(board, "runner", SLEEPER)
    try:
        started = _wait_run_active(board, "runner")
        assert started.get("ticket") == "T-001"
        child_started = time.time()

        # Submitting for review is the seat's OWN last act inside the run.
        # It must not end the run it came from.
        r = run(board, "status", "T-001", "review", "--notes", "paths, tests",
                "--force", agent="runner")
        assert r.returncode == 0, r.stderr
        time.sleep(3)
        live = json.loads((board / "agents" / "runner.run").read_text())
        assert live.get("active"), "IN REVIEW must not stop the run that made it"

        closed = run(board, "status", "T-001", "done", "--notes", "merged",
                     agent="boss")
        assert closed.returncode == 0, closed.stderr
        rec = _wait_run_over(board, "runner")
        assert rec, "the run was still active after the ticket closed"
        assert time.time() - child_started < 60, "the sleeping child ran on"
        assert rec.get("rc") == tool.RUN_STOPPED_RC

        end = _wait_traj(board, "run_end", "runner")
        assert end.get("outcome") == "stopped", end
        assert end.get("stop_reason") == "closed", end
        assert end.get("ticket") == "T-001"

        t = json.loads((board / "T-001.json").read_text())
        texts = [n.get("text", "") for n in t.get("notes", [])]
        assert any("run stopped" in x and "closed" in x for x in texts), texts

        # keep the worktree: the watcher touches no tree on the way out
        assert (board.parent / ".git").exists()
        assert proc.poll() is None, "stopping a run must not stop the watcher"
    finally:
        _reap(proc)


def test_reassigning_the_ticket_stops_the_run(board):
    run(board, "join", "runner", "--roles", "docs")
    run(board, "join", "other", "--roles", "docs")
    assert run(board, "claim", "T-001", agent="runner").returncode == 0
    proc = _start_watch(board, "runner", SLEEPER)
    try:
        _wait_run_active(board, "runner")
        moved = run(board, "assign", "T-001", "--owner", "other", agent="boss")
        assert moved.returncode == 0, moved.stderr
        rec = _wait_run_over(board, "runner")
        assert rec, "the run was still active after the ticket moved"
        end = _wait_traj(board, "run_end", "runner")
        assert end.get("outcome") == "stopped"
        assert end.get("stop_reason") in ("reassigned", "superseded"), end
        assert (board.parent / ".git").exists(), "no cleanup on the reassign path"
        assert proc.poll() is None
    finally:
        _reap(proc)


def test_blocking_the_ticket_stops_the_run(board):
    run(board, "join", "runner", "--roles", "docs")
    assert run(board, "claim", "T-001", agent="runner").returncode == 0
    proc = _start_watch(board, "runner", SLEEPER)
    try:
        _wait_run_active(board, "runner")
        parked = run(board, "block", "T-001", "--reason", "waits on T-002",
                     agent="boss")
        assert parked.returncode == 0, parked.stderr
        assert _wait_run_over(board, "runner"), "run outlived the block"
        end = _wait_traj(board, "run_end", "runner")
        assert end.get("stop_reason") == "blocked", end
        assert (board.parent / ".git").exists(), "no cleanup on the blocked path"
    finally:
        _reap(proc)


def test_discarding_the_ticket_stops_the_run(board):
    run(board, "join", "runner", "--roles", "docs")
    assert run(board, "claim", "T-001", agent="runner").returncode == 0
    proc = _start_watch(board, "runner", SLEEPER)
    try:
        _wait_run_active(board, "runner")
        gone = run(board, "discard", "T-001", "--reason", "superseded by T-002",
                   agent="boss")
        assert gone.returncode == 0, gone.stderr
        assert _wait_run_over(board, "runner"), "run outlived the discard"
        end = _wait_traj(board, "run_end", "runner")
        assert end.get("stop_reason") == "superseded", end
    finally:
        _reap(proc)


def test_a_stopped_run_is_not_counted_as_a_failure(board):
    """No adapter_failure, so the seat is not fenced off its next trigger."""
    run(board, "join", "runner", "--roles", "docs")
    assert run(board, "claim", "T-001", agent="runner").returncode == 0
    proc = _start_watch(board, "runner", SLEEPER)
    try:
        _wait_run_active(board, "runner")
        assert run(board, "block", "T-001", "--reason", "parked",
                   agent="boss").returncode == 0
        assert _wait_run_over(board, "runner"), "run never ended"
        time.sleep(1.0)
        rec = json.loads((board / "agents" / "runner.json").read_text())
        assert not rec.get("adapter_failure"), rec.get("adapter_failure")
    finally:
        _reap(proc)


def test_an_ordinary_run_is_left_alone(board):
    """The guard must not end runs whose ticket is still the seat's."""
    run(board, "join", "runner", "--roles", "docs")
    assert run(board, "claim", "T-001", agent="runner").returncode == 0
    quick = "%s -c 'import time; time.sleep(4)'" % sys.executable
    proc = _start_watch(board, "runner", quick)
    try:
        _wait_run_active(board, "runner")
        rec = _wait_run_over(board, "runner", timeout=30)
        assert rec, "run never ended"
        assert rec.get("rc") == 0, rec
        end = _wait_traj(board, "run_end", "runner")
        assert end.get("outcome") != "stopped", end
        assert end.get("exit") == 0
    finally:
        _reap(proc)


def test_once_mode_exits_clean_on_a_stopped_run(board):
    """A cron wrapper must not read a deliberate stop as a harness failure."""
    run(board, "join", "runner", "--roles", "docs")
    assert run(board, "claim", "T-001", agent="runner").returncode == 0
    proc = _start_watch(board, "runner", SLEEPER, once=True)
    try:
        _wait_run_active(board, "runner")
        assert run(board, "block", "T-001", "--reason", "parked",
                   agent="boss").returncode == 0
        proc.wait(timeout=30)
        assert proc.returncode == 0, "a stopped run is not a failed one"
        end = _wait_traj(board, "run_end", "runner")
        assert end.get("outcome") == "stopped"
    finally:
        _reap(proc)
