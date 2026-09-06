"""T-237 -- liveness truth: is this agent alive, and does the board say so?

Every assertion here goes through the real CLI as a subprocess and reads what a
MASTER reads (`tickets who`, `tickets dash`, `tickets limits`), never a helper
in isolation. That is deliberate and it is the point of the ticket: an
assertion about checkin() alone passes happily while the board still lies to
the person acting on it.

The two directions are one mechanism seen from both sides, so both are tested:
a long real session must not read as stale, and a session failing instantly
must not read as fresh.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="", env=None, cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          env=e, cwd=str(cwd or board.parent))


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    assert run(b, "create", "Write docs", "--role", "docs", cwd=repo).returncode == 0
    return b


# ---- helpers that fabricate exactly what the real tools leave on disk ----

def home(board):
    return board.parent.parent / "home"


def stamp(secs_ago=0):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - secs_ago))


def agent_rec(board, name):
    return json.loads((board / "agents" / (name + ".json")).read_text())


def write_agent(board, name, **fields):
    (board / "agents").mkdir(parents=True, exist_ok=True)
    p = board / "agents" / (name + ".json")
    rec = json.loads(p.read_text()) if p.exists() else {"owner": name}
    rec.update(fields)
    p.write_text(json.dumps(rec))


def claude_transcript(board, cwd, secs_ago=0, lines=1):
    """A Claude session transcript where the real one lives: a per-directory
    project dir whose name is the absolute path with / . _ turned into -."""
    d = home(board) / ".claude" / "projects" / re.sub(r"[/._]", "-", str(cwd))
    d.mkdir(parents=True, exist_ok=True)
    f = d / "session.jsonl"
    f.write_text("".join(
        json.dumps({"type": "assistant", "timestamp": stamp(secs_ago + i), "cwd": str(cwd)}) + "\n"
        for i in range(lines, 0, -1)))
    return f


def codex_rollout(board, cwd, error=None, secs_ago=0, telemetry_turns=8):
    """A codex rollout carrying the `rate_limits` telemetry block that every
    turn writes -- the false signal the old `tickets limits` scan counted as
    evidence -- plus a final task_complete that is the actual verdict."""
    d = home(board) / ".codex" / "sessions" / "2026" / "09" / "07"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "rollout-test.jsonl"
    out = [json.dumps({"type": "session_meta", "payload": {"cwd": str(cwd)}})]
    for i in range(telemetry_turns):
        out.append(json.dumps({"type": "token_count", "timestamp": stamp(secs_ago + 60 + i),
                               "rate_limits": {"limit_id": "premium", "used_percent": 3}}))
    out.append(json.dumps({"timestamp": stamp(secs_ago), "type": "event_msg",
                           "payload": {"type": "task_complete",
                                       "last_agent_message": None if error else "done",
                                       "error": ({"message": error, "codex_error_info": "usage_limit_exceeded"}
                                                 if error else False)}}))
    f.write_text("\n".join(out) + "\n")
    os.utime(f, (time.time() - secs_ago, time.time() - secs_ago))
    return f


def watch_log(board, name, runs):
    """runs = [(started_secs_ago, duration_s, rc, body)] oldest first, written
    in cmd_watch's own log format."""
    (board / "agents").mkdir(parents=True, exist_ok=True)
    out = []
    for i, (ago, dur, rc, body) in enumerate(runs, 1):
        out.append("%s run %d trigger={\"messages_to_me\": [\"x\"]}" % (stamp(ago), i))
        if body:
            out.append(body)
        out.append("%s run %d exit %s" % (stamp(ago - dur), i, rc))
    (board / "agents" / (name + ".watch.log")).write_text("\n".join(out) + "\n")


def who(board):
    r = run(board, "who")
    assert r.returncode == 0, r.stderr
    return r.stdout


def state_of(text, name):
    """The state word `tickets who` printed for one agent."""
    for ln in text.splitlines():
        if ln.startswith(name + " ") or ln.startswith(name.ljust(14)):
            return ln[14:].split()[0]
    raise AssertionError("no row for %s in:\n%s" % (name, text))


# ---- direction 1: a long real session must not read as stale ------------

def test_working_agent_with_stale_loop_seen_reads_working(board):
    """The cos-opus case, which nearly got a live agent's tickets reopened:
    `seen` is 50 minutes old because the watcher is still blocked on a child
    that has not returned, while the session is writing a turn right now."""
    run(board, "join", "doc", "--roles", "docs")
    cwd = board.parent
    claude_transcript(board, cwd, secs_ago=0)
    write_agent(board, "doc", cwd=str(cwd), seen=stamp(50 * 60))

    out = who(board)
    assert state_of(out, "doc") == "working", out
    # and the old number is still on screen -- renamed, not deleted, because
    # "when did the watcher last idle" is a real question, just a different one
    assert "loop-seen" in out and "50m ago" in out


def test_in_run_heartbeat_is_written_while_the_child_is_still_running(board):
    """The literal ask: something records liveness DURING a run. Read from a
    second process while the child is still executing -- if this only worked
    after the child exited it would be the bug, not the fix."""
    run(board, "join", "doc", "--roles", "docs")
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="", HOME=str(home(board)))
    e.pop("TICKETS_STOP_HOOK", None)
    proc = subprocess.Popen(
        [sys.executable, str(TOOL), "watch", "--agent", "doc", "--once",
         "--beat-every", "1", "--exec", "sleep 6"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e, cwd=str(board.parent))
    try:
        runfile = board / "agents" / "doc.run"
        deadline = time.time() + 15
        beats = []
        while time.time() < deadline and len(beats) < 2:
            if runfile.exists():
                try:
                    rec = json.loads(runfile.read_text())
                except ValueError:
                    rec = {}
                if rec.get("active") and rec.get("beat") and (not beats or rec["beat"] != beats[-1]):
                    beats.append(rec["beat"])
            time.sleep(0.4)
        assert proc.poll() is None, "child already exited; this proves nothing about mid-run"
        assert len(beats) >= 2, "no repeating heartbeat during the run: %s" % beats
    finally:
        proc.wait(timeout=30)
    ended = json.loads((board / "agents" / "doc.run").read_text())
    assert ended["active"] is False and ended["rc"] == 0, ended


def test_heartbeat_does_not_clobber_what_the_child_writes_to_the_agent_record(board):
    """The heartbeat lives in its own file on purpose. The child session runs
    `tickets` commands that read-modify-write agents/<name>.json; a heartbeat
    thread rewriting that same record every second would race them and drop
    whatever the child had just recorded."""
    run(board, "join", "doc", "--roles", "docs")
    r = run(board, "watch", "--agent", "doc", "--once", "--beat-every", "1",
            "--exec", "%s %s inbox" % (shquote(sys.executable), shquote(str(TOOL))))
    assert r.returncode == 0, r.stderr
    rec = agent_rec(board, "doc")
    assert rec.get("inbox_seen"), "the child's own inbox_seen survived the run: %s" % rec
    assert "beat" not in rec, "the heartbeat must not be written into the agent record"


def shquote(s):
    import shlex
    return shlex.quote(s)


# ---- direction 2: a fast-failing session must not read as fresh ---------

def test_fast_failing_agent_does_not_read_as_healthy(board):
    """The mirror image, and the reason polling faster is not a fix: the runs
    fail in seconds, so the watcher checks in constantly and `seen` is the
    FRESHEST on the board while the agent cannot start a session at all."""
    run(board, "join", "doc", "--roles", "docs")
    err = "ActionRequiredError: You've hit your usage limit. Resets 10/5/2026."
    watch_log(board, "doc", [(400, 5, 1, err), (300, 6, 1, err), (200, 5, 1, err), (100, 6, 1, err)])
    write_agent(board, "doc", cwd=str(board.parent), seen=stamp(5))  # 5 seconds old

    out = who(board)
    assert state_of(out, "doc") == "limited", out
    assert "usage limit" in out
    # it is derived, not asserted by a human, and must say so
    assert "~" in [ln[23] for ln in out.splitlines() if ln.startswith("doc ")][:1] or "limited ~" in out
    assert "limit" not in agent_rec(board, "doc"), "no manual `tickets limit` record exists"


def test_a_slow_failure_does_not_break_the_failing_streak(board):
    """Found on the live board against my own first cut: gpt-cursor had been
    hard-limited until October with 37 consecutive failures, and gating the
    streak on run DURATION dropped it the moment one failure took 97s instead
    of 7. The evidence is the CLI's error text, not the clock."""
    run(board, "join", "doc", "--roles", "docs")
    err = "ActionRequiredError: You've hit your usage limit. Resets 10/5/2026."
    watch_log(board, "doc", [(600, 6, 1, err), (500, 7, 1, err), (400, 6, 1, err), (300, 97, 1, err)])
    write_agent(board, "doc", cwd=str(board.parent), seen=stamp(30))
    assert state_of(who(board), "doc") == "limited", who(board)


def test_repeated_failure_with_no_quotable_error_is_unknown_not_limited(board):
    """Failing fast is not proof of a limit. Saying `limited` here would be a
    confident wrong answer, and saying `idle` would be worse."""
    run(board, "join", "doc", "--roles", "docs")
    watch_log(board, "doc", [(400, 3, 1, "boom"), (300, 3, 1, "boom"), (200, 3, 1, "boom")])
    write_agent(board, "doc", cwd=str(board.parent), seen=stamp(10))
    out = who(board)
    assert state_of(out, "doc") == "unknown", out
    assert "read it by hand" in out


# ---- unknown is a state, not a place errors go to die -------------------

def test_no_ground_truth_reads_unknown_never_idle(board):
    run(board, "join", "doc", "--roles", "docs")
    write_agent(board, "doc", cwd="/no/such/worktree", seen=stamp(30))
    out = who(board)
    assert state_of(out, "doc") == "unknown", out
    assert "?" in out and "no Claude or Codex transcript" in out


def test_a_run_in_flight_alone_is_not_evidence_the_agent_is_working(board):
    """A fresh in-run heartbeat proves the WATCHER is up and a child started.
    It says nothing about whether the agent is doing anything, so it must not
    be rendered as `working` -- that conflation is the deeper bug."""
    run(board, "join", "doc", "--roles", "docs")
    write_agent(board, "doc", cwd="/no/such/worktree", seen=stamp(3000))
    (board / "agents" / "doc.run").write_text(json.dumps(
        {"active": True, "run": 7, "beat": stamp(2), "pid": os.getpid()}))
    out = who(board)
    assert state_of(out, "doc") == "unknown", out
    assert "watcher alive" in out and "no transcript to confirm work" in out


def test_transcript_on_a_shared_cwd_is_marked_heuristic_not_stated_as_fact(board):
    """Three records on the live board point at one worktree. A Claude
    transcript is keyed by directory and carries no agent name, so reading it
    naively reports all three as working when at most one is."""
    run(board, "join", "doc", "--roles", "docs")
    run(board, "join", "bob", "--roles", "backend")
    cwd = board.parent
    claude_transcript(board, cwd, secs_ago=0)
    write_agent(board, "doc", cwd=str(cwd), seen=stamp(60))
    write_agent(board, "bob", cwd=str(cwd), seen=stamp(60))
    out = who(board)
    assert "cwd shared with bob" in out and "cwd shared with doc" in out, out
    assert "transcript cannot say which" in out


# ---- the named false signal: codex rate_limits telemetry ---------------

def test_codex_rate_limit_telemetry_is_not_read_as_a_limit(board):
    """Every codex turn writes a `rate_limits` block, so the old substring
    scan reported healthy and hard-limited sessions identically. The verdict
    is the last task_complete, and here it is clean."""
    run(board, "join", "doc", "--roles", "docs")
    codex_rollout(board, board.parent, error=None, secs_ago=0, telemetry_turns=12)
    write_agent(board, "doc", cwd=str(board.parent), seen=stamp(3000))
    out = who(board)
    assert state_of(out, "doc") == "working", out

    lim = run(board, "limits")
    assert lim.returncode == 0, lim.stderr
    assert "rate_limit" not in lim.stdout, "the discredited hit-count scan is back in the default view"
    assert "working" in lim.stdout


def test_codex_task_complete_error_reads_limited_without_a_manual_record(board):
    run(board, "join", "doc", "--roles", "docs")
    codex_rollout(board, board.parent, error="You've hit your usage limit. Try again at 10:00 AM.")
    write_agent(board, "doc", cwd=str(board.parent), seen=stamp(5))
    assert state_of(who(board), "doc") == "limited", who(board)
    assert "limit" not in agent_rec(board, "doc")


def test_codex_failure_that_is_not_a_limit_is_unknown_not_idle(board):
    """A broken host or a dead login is not a usage limit and not health."""
    run(board, "join", "doc", "--roles", "docs")
    codex_rollout(board, board.parent, error="spawn codex-code-mode-host ENOENT")
    write_agent(board, "doc", cwd=str(board.parent), seen=stamp(5))
    out = who(board)
    assert state_of(out, "doc") == "unknown", out
    assert "last turn failed" in out


def test_raw_scan_is_available_but_labelled_as_not_evidence(board):
    run(board, "join", "doc", "--roles", "docs")
    codex_rollout(board, board.parent, error=None, telemetry_turns=12)
    r = run(board, "limits", "--raw-scan")
    assert r.returncode == 0, r.stderr
    assert "NOT EVIDENCE" in r.stdout


# ---- what a master actually reads before reassigning -------------------

def test_dash_separates_a_human_assertion_from_a_derived_one(board):
    """Both are DOWN, but 'a person read the log' and 'the tool worked it out'
    are different levels of evidence and the reader must be able to tell."""
    run(board, "join", "doc", "--roles", "docs")
    run(board, "join", "bob", "--roles", "backend")
    run(board, "limit", "bob", "--until", "06:00", "--note", "session limit")
    err = "ActionRequiredError: You've hit your usage limit."
    watch_log(board, "doc", [(400, 5, 1, err), (300, 5, 1, err), (200, 5, 1, err)])
    write_agent(board, "doc", cwd=str(board.parent), seen=stamp(5))

    r = run(board, "dash", "--once")
    assert r.returncode == 0, r.stderr
    doc = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("doc ")]
    bob = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("bob ")]
    assert doc and "DOWN~" in doc[0], doc
    assert bob and "DOWN!" in bob[0], bob


def test_a_derived_limit_stops_counting_the_agent_as_live_capacity(board):
    """gpt-cursor was listed as live capacity with pending work while hard
    limited until October, because DOWN was reachable only through a
    hand-typed record."""
    run(board, "join", "doc", "--roles", "docs")
    err = "ActionRequiredError: You've hit your usage limit."
    watch_log(board, "doc", [(400, 5, 1, err), (300, 5, 1, err), (200, 5, 1, err)])
    write_agent(board, "doc", cwd=str(board.parent), seen=stamp(5))
    r = run(board, "dash", "--once")
    assert r.returncode == 0, r.stderr
    m = re.search(r"UTILIZATION 24h  \((\d+) live agents, (\d+) down\)", r.stdout)
    assert m, r.stdout
    assert int(m.group(2)) >= 1, "a derived limit still counted as live capacity:\n%s" % r.stdout
