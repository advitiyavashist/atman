"""Wake-up layer: pending / stop-hook / watch / boot / hooks / codex-hook.

Runs the real CLI as a subprocess against a throwaway board so the tests see
exactly what a hook or a watcher sees. No network, no model, no home-dir writes.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from watch_reaper import collect_watch_pids_from_board

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="", stdin="", env=None, cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "", HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    # Strip every session-id var the harness running THIS test might itself
    # be sitting in (e.g. CLAUDE_CODE_SESSION_ID from an outer coding-agent
    # session) -- left in place, every subprocess this file launches would
    # share one identical session key, and a session's RECORDED identity
    # (from `join`) deliberately outranks an explicit per-invocation
    # TICKET_AGENT for the surfaces that resolve through it (board/msg/inbox/
    # stop-hook; see tests/test_identity_precedence.py) -- so every simulated
    # actor in a test that exercises several of them would collapse onto
    # whichever name last joined under that one shared session, instead of
    # the TICKET_AGENT each call actually asked for.
    #
    # Stripping to nothing is not enough on its own: with NO session key at
    # all, read_identity() falls back to the flat legacy per-board file
    # (deliberately, for harnesses that genuinely give none -- see
    # test_identity_session_scope's legacy-file test), and this file joins
    # several differently-named agents on ONE board per test, so they would
    # all collide on that single shared file -- the exact sideways bleed the
    # session-scoped identity fix exists to prevent, just re-triggered by the
    # test doing what no single real harness session would.
    #
    # A FRESH id per call does not work either: `join doc` and a later
    # `agent="doc"` call are meant to model the SAME session across two
    # subprocesses, and giving them unrelated ids breaks that continuity --
    # session_seat()/seat_confirmed() then see a session key with no
    # matching record and refuse to fall back to the (correctly set)
    # TICKET_AGENT, because a session key present at all means "trust the
    # record, not the ambient var".
    #
    # So: derive a STABLE id per simulated actor instead, anchored on
    # whichever value names that actor for THIS call -- the positional name
    # for `join` (which never passes `agent=`), otherwise `agent=` itself.
    # Same actor name -> same id across every call in a test, matching what
    # one continuous harness session would give for real; different actors,
    # or no actor at all, get different (or no-record) ids and so cannot
    # inherit each other's recorded identity.
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID", "TERM_SESSION_ID"):
        e.pop(var, None)
    if args and args[0] == "join" and len(args) > 1 and args[1]:
        actor = args[1]
    else:
        actor = agent or "__anonymous__"
    e["TICKET_SESSION_ID"] = "test-session-" + actor
    if env:
        e.update(env)
    where = cwd or (board.parent if board.parent.is_dir() else Path("/"))
    r = subprocess.run([sys.executable, str(TOOL), *args], input=stdin, capture_output=True, text=True,
                       env=e, cwd=where)
    if args and args[0] == "spawn" and "--stop" not in args and "--list" not in args:
        collect_watch_pids_from_board(board)
    return r


def _wait_for_path(path, timeout=10.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return
        time.sleep(interval)
    assert path.exists()


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                            GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    r = run(b, "create", "Write docs", "--role", "docs", cwd=repo)
    assert r.returncode == 0, r.stderr
    return b


def pending(board, agent):
    r = run(board, "pending", "--agent", agent, "--json")
    return r.returncode, json.loads(r.stdout)


# ---- pending ------------------------------------------------------------

def test_pending_nothing_for_stranger(board):
    run(board, "join", "bob", "--roles", "backend")
    rc, p = pending(board, "bob")
    assert rc == 1 and p["pending"] is False


def test_pending_ready_in_lane(board):
    run(board, "join", "doc", "--roles", "docs")
    rc, p = pending(board, "doc")
    assert rc == 0 and p["ready_in_my_lane"][0].startswith("T-001")


def test_pending_direct_message_and_broadcast_only(board):
    run(board, "join", "bob", "--roles", "backend")
    # T-244 stamps bob's inbox_seen the moment its record is created above.
    # Without a real gap, alice's broadcast below can land in the SAME
    # wall-clock second, which the pre-existing, separately-filed T-228 hole
    # (strict `>` on second-resolution timestamps) would then hide.
    time.sleep(1.1)
    run(board, "msg", "hello everyone", agent="alice")
    rc, p = pending(board, "bob")
    assert rc == 1 and p.get("broadcasts") == 1 and p["pending"] is False
    run(board, "msg", "bob please look", "--to", "bob", agent="alice")
    rc, p = pending(board, "bob")
    assert rc == 1 and "messages_to_me" in p and p["pending"] is False
    run(board, "msg", "task: bob please look", "--to", "bob", agent="alice")
    rc, p = pending(board, "bob")
    assert rc == 0 and "task_messages" in p


def test_pending_holding_ticket(board):
    run(board, "join", "doc", "--roles", "docs")
    assert run(board, "next", agent="doc").returncode == 0
    rc, p = pending(board, "doc")
    assert rc == 0 and p["holding"][0].startswith("T-001") and "ready_in_my_lane" not in p


def test_pending_respects_usage_limit(board):
    run(board, "join", "doc", "--roles", "docs")
    run(board, "limit", "doc", "--note", "out")
    rc, p = pending(board, "doc")
    assert rc == 1 and "limited" in p
    run(board, "limit", "doc", "--clear")
    assert pending(board, "doc")[0] == 0


def test_pending_needs_gating(board):
    run(board, "create", "Docker thing", "--role", "docs", "--needs", "docker")
    run(board, "join", "doc", "--roles", "docs")
    rc, p = pending(board, "doc")
    assert all("Docker" not in x for x in p["ready_in_my_lane"])
    run(board, "join", "doc", "--roles", "docs", "--can", "docker")
    rc, p = pending(board, "doc")
    assert any("Docker" in x for x in p["ready_in_my_lane"])


def test_pending_survives_missing_board(tmp_path):
    r = run(tmp_path / "nope" / ".tickets", "pending", "--agent", "x", "--json")
    assert r.returncode == 1


# ---- stop hook ----------------------------------------------------------

def stop(board, agent, event=None, env=None):
    r = run(board, "stop-hook", agent=agent, stdin=json.dumps(event or {}), env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout or "{}")


def test_stop_hook_blocks_when_holding(board):
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    out = stop(board, "doc", {"stop_hook_active": False})
    assert out.get("decision") == "block" and "T-001" in out["reason"]


def test_stop_hook_loop_guard_and_no_agent(board):
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    assert stop(board, "doc", {"stop_hook_active": True}) == {}
    assert stop(board, "", {}) == {}


def test_stop_hook_kill_switch_and_broadcast_only(board):
    run(board, "join", "bob", "--roles", "backend")
    run(board, "msg", "fyi", agent="alice")
    assert stop(board, "bob") == {}
    run(board, "next", agent="bob") if False else None
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    assert stop(board, "doc", env={"TICKETS_STOP_HOOK": "off"}) == {}


def test_stop_hook_rate_cap(board):
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    blocks = [stop(board, "doc").get("decision") for _ in range(6)]
    assert blocks[:4] == ["block"] * 4 and blocks[4:] == [None, None]


def test_stop_hook_never_crashes_on_garbage(board):
    # garbage stdin is treated as a plain stop event: exit 0 and valid JSON,
    # blocking or not depending on the board (here: a ready ticket exists)
    r = run(board, "stop-hook", agent="doc", stdin="not json {{{")
    assert r.returncode == 0 and json.loads(r.stdout).get("decision") in (None, "block")
    r = run(board, "stop-hook", agent="", stdin="not json {{{")
    assert r.returncode == 0 and json.loads(r.stdout) == {}


def test_stop_hook_ignores_limited_agent(board):
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    run(board, "limit", "doc")
    assert stop(board, "doc") == {}


# ---- watch --------------------------------------------------------------

def test_watch_once_exit_codes_and_dry_run(board):
    run(board, "join", "doc", "--roles", "docs")
    r = run(board, "watch", "--agent", "doc", "--once", "--dry-run")
    assert r.returncode == 0 and "dry-run" in r.stdout
    run(board, "join", "bob", "--roles", "backend")
    assert run(board, "watch", "--agent", "bob", "--once", "--dry-run").returncode == 1


def test_watch_executes_command_and_logs(board):
    run(board, "join", "doc", "--roles", "docs")
    marker = board.parent / "ran.txt"
    r = run(board, "watch", "--agent", "doc", "--once", "--exec", "echo executed > %s" % marker)
    assert r.returncode == 0 and marker.read_text().strip() == "executed"
    log = (board / "agents" / "doc.watch.log").read_text()
    assert "run 1 trigger=" in log and "exit 0" in log


def test_watch_max_runs_and_lock(board):
    run(board, "join", "doc", "--roles", "docs")
    r = run(board, "watch", "--agent", "doc", "--every", "5", "--max-runs", "1", "--exec", "true")
    assert r.returncode == 0 and "max-runs reached" in r.stdout
    assert not (board / "agents" / "doc.watch.pid").exists(), "lock released"
    (board / "agents" / "doc.watch.pid").write_text(str(os.getpid()))  # a live pid
    r = run(board, "watch", "--agent", "doc", "--every", "5", "--max-runs", "1", "--exec", "true")
    assert r.returncode != 0 and "already running" in (r.stderr + r.stdout)
    (board / "agents" / "doc.watch.pid").write_text("999999")  # stale pid is reclaimed
    r = run(board, "watch", "--agent", "doc", "--every", "5", "--max-runs", "1", "--exec", "true")
    assert r.returncode == 0


def test_watch_run_timeout(board):
    run(board, "join", "doc", "--roles", "docs")
    t = time.time()
    r = run(board, "watch", "--agent", "doc", "--once", "--run-timeout", "0", "--exec", "true")
    assert r.returncode == 0 and time.time() - t < 30


def test_watch_refuses_anonymous(board):
    r = run(board, "watch", "--once")
    assert r.returncode != 0


# ---- prompt / guide -----------------------------------------------------

def test_prompt_mentions_agent_and_loop(board):
    r = run(board, "prompt", "--agent", "doc", "--extra", "EXTRA LINE")
    assert "You are doc" in r.stdout and "tickets review" in r.stdout and "EXTRA LINE" in r.stdout
    assert "tickets clear" in r.stdout  # the prohibition is spelled out


def test_guide_covers_three_tools(board):
    r = run(board, "guide")
    for tool in ("claude", "codex", "cursor"):
        assert tool in r.stdout.lower()


# ---- hooks installers (never touch the real home) ------------------------

def test_hooks_claude_idempotent_and_no_stop(board, tmp_path):
    settings = tmp_path / "home" / "settings.json"
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-tool"}]}]},
                                    "permissions": {"allow": ["Bash(ls)"]}}))
    for _ in range(2):
        r = run(board, "hooks", "claude", "--agent", "doc", "--settings", str(settings))
        assert r.returncode == 0, r.stderr
    s = json.loads(settings.read_text())
    assert len(s["hooks"]["SessionStart"]) == 1 and len(s["hooks"]["UserPromptSubmit"]) == 1
    cmds = [h["command"] for e in s["hooks"]["Stop"] for h in e["hooks"]]
    assert "other-tool" in cmds and any("hook-run --agent doc --event stop" in c for c in cmds) and len(cmds) == 2
    assert "Bash(ls)" in s["permissions"]["allow"] and "Bash(tickets:*)" in s["permissions"]["allow"]
    run(board, "hooks", "claude", "--agent", "doc", "--settings", str(settings), "--no-stop")
    s = json.loads(settings.read_text())
    assert [h["command"] for e in s["hooks"]["Stop"] for h in e["hooks"]] == ["other-tool"]


def test_hooks_codex_scoped_and_preserving(board, tmp_path):
    hp = tmp_path / "home" / "hooks.json"
    hp.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "keep-me"}]}]}}))
    wt = board.parent
    r = run(board, "hooks", "codex", "--agent", "cx", "--worktree", str(wt), "--hooks-file", str(hp))
    assert r.returncode == 0, r.stderr
    run(board, "hooks", "codex", "--agent", "cx", "--worktree", str(wt), "--hooks-file", str(hp))
    cfg = json.loads(hp.read_text())
    ss = [h["command"] for e in cfg["hooks"]["SessionStart"] for h in e["hooks"]]
    assert ss[0] == "keep-me" and sum("codex-hook --agent cx" in c for c in ss) == 1
    # the hook body: scoped to the worktree, read-only, right envelope
    run(board, "join", "cx", "--roles", "docs")
    ev = {"hook_event_name": "SessionStart", "cwd": str(wt)}
    out = json.loads(run(board, "codex-hook", "--agent", "cx", "--worktree", str(wt), stdin=json.dumps(ev)).stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart" and "ready_in_my_lane" in ctx
    elsewhere = run(board, "codex-hook", "--agent", "cx", "--worktree", str(wt), stdin=json.dumps(dict(ev, cwd="/")))
    assert elsewhere.stdout.strip() == ""
    assert json.loads((board / "T-001.json").read_text())["status"] == "open", "hook must not mutate"


def test_hooks_cursor_writes_project_hooks(board):
    r = run(board, "hooks", "cursor", "--agent", "cu")
    assert r.returncode == 0, r.stderr
    cfg = json.loads((board.parent / ".cursor" / "hooks.json").read_text())
    assert set(cfg["hooks"]) >= {"sessionStart", "beforeSubmitPrompt", "stop"}
    assert (board.parent / ".cursor" / "hooks" / "tickets-board.py").exists()


# ---- boot ---------------------------------------------------------------

def test_boot_does_every_step(board, tmp_path):
    settings = tmp_path / "home" / "settings.json"
    r = run(board, "boot", "--agent", "doc", "--tool", "claude", "--roles", "docs", "--settings", str(settings))
    assert r.returncode == 0, r.stderr
    assert "joined as doc" in r.stdout and "hooks installed" in r.stdout and "checked in" in r.stdout
    assert "ready_in_my_lane" in r.stdout and "NEXT: TICKET_AGENT=doc tickets next" in r.stdout
    assert json.loads(settings.read_text())["hooks"]["Stop"]
    who = run(board, "who").stdout
    assert "doc" in who
    r2 = run(board, "boot", "--agent", "doc", "--settings", str(settings))  # idempotent, no tool
    assert r2.returncode == 0 and "identity ok" in r2.stdout


def test_boot_after_claim_points_to_mine(board, tmp_path):
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    r = run(board, "boot", "--agent", "doc")
    assert "holding" in r.stdout and "NEXT: TICKET_AGENT=doc tickets mine" in r.stdout


def test_boot_refuses_without_board(tmp_path):
    r = run(tmp_path / "x" / ".tickets", "boot", "--agent", "doc")
    assert r.returncode != 0


# ---- brief / util / dash ------------------------------------------------

def test_brief_agent_and_ticket_reach_prompt_and_owner(board):
    run(board, "join", "doc", "--roles", "docs")
    r = run(board, "brief", "doc", "Write in lowercase.", agent="master")
    assert r.returncode == 0, r.stderr
    assert (board / "briefs" / "doc.md").read_text().count("Write in lowercase.") == 1
    run(board, "next", agent="doc")
    r = run(board, "brief", "--ticket", "T-001", "Reviewer wants a title.", agent="master")
    assert r.returncode == 0 and "owner doc messaged" in r.stdout
    p = run(board, "prompt", "--agent", "doc").stdout
    assert "Write in lowercase." in p and "Reviewer wants a title." in p
    assert run(board, "brief", "doc", "--show").stdout.strip().endswith("Write in lowercase.")
    assert "Reviewer wants a title." in run(board, "brief", "--ticket", "T-001", "--show").stdout
    # the ticket owner got a DM about the new context
    assert "context added to T-001" in run(board, "inbox", "--keep", agent="doc").stdout


def test_brief_from_file_replaces(board, tmp_path):
    f = tmp_path / "b.md"
    f.write_text("# standing\nbe brief\n")
    run(board, "brief", "doc", "old line", agent="master")
    r = run(board, "brief", "doc", "--file", str(f), agent="master")
    assert r.returncode == 0
    assert (board / "briefs" / "doc.md").read_text() == "# standing\nbe brief\n"


def test_util_and_dash_once(board):
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    r = run(board, "util", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    doc = next(a for a in data["agents"] if a["agent"] == "doc")
    assert doc["state"] == "busy" and doc["in_flight"] == 1
    r = run(board, "dash", "--once")
    assert r.returncode == 0
    for section in ("IN FLIGHT", "REVIEW QUEUE", "AGENTS", "UTILIZATION", "HEALTH", "MESSAGES"):
        assert section in r.stdout
    assert "T-001" in r.stdout and "@doc" in r.stdout


# ---- master prompt / spawn lifecycle ------------------------------------

def test_prompt_worker_has_stuck_rule_and_no_env_prefix(board):
    p = run(board, "prompt", "--agent", "doc").stdout
    assert "stuck:" in p and "TICKET_AGENT is already set" in p and "TICKET_AGENT=doc tickets" not in p


def test_prompt_master_variant(board):
    run(board, "master", "take", agent="boss")
    p = run(board, "prompt", "--master", "--agent", "boss").stdout
    assert "MASTER" in p and "UNBLOCK" in p and "tickets merge" in p and "You do not take feature tickets" in p


def test_master_pending_keys(board):
    run(board, "master", "take", agent="boss")
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    run(board, "msg", "stuck: cannot commit", "--to", "boss", agent="doc")
    rc, p = pending(board, "boss")
    assert rc == 0 and "stuck_messages" in p
    # a review submission from a worktree branch shows up as review_queue
    repo = board.parent
    (repo / ".gitignore").write_text(".tickets/\n.worktrees/\n")
    genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "ignore board"], check=True, env=genv)
    subprocess.run(["git", "-C", str(repo), "switch", "-q", "-c", "doc"], check=True)
    r = run(board, "review", "T-001", "--notes", "done", agent="doc", cwd=repo)
    assert r.returncode == 0, r.stderr
    rc, p = pending(board, "boss")
    assert "review_queue" in p and p["review_queue"] == ["T-001"]


def test_spawn_lifecycle_with_stub_command(board):
    run(board, "join", "doc", "--roles", "docs")
    r = run(board, "spawn", "doc", "--exec", "true", "--every", "5", "--persist", agent="master")
    assert r.returncode == 0, r.stderr
    assert "watcher for doc started" in r.stdout
    pid_file = board / "agents" / "doc.watch.pid"
    _wait_for_path(pid_file)
    lst = run(board, "spawn", "--list").stdout
    assert "doc" in lst and "pid" in lst
    r2 = run(board, "spawn", "doc", "--exec", "true", "--persist", agent="master")
    assert "already running" in r2.stdout
    r3 = run(board, "spawn", "doc", "--stop", agent="master")
    assert "stopped 1 watcher" in r3.stdout
    for _ in range(20):
        if not pid_file.exists():
            break
        time.sleep(1)
    assert not pid_file.exists(), "watcher exits and releases its lock on --stop"
    assert (board.parent / ".worktrees" / "doc").is_dir(), "spawn created the worktree"


def test_spawn_inherits_project_settings(board):
    (board.parent / ".claude").mkdir()
    (board.parent / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(pytest:*)"]}}))
    r = run(board, "spawn", "doc", "--exec", "true", "--every", "5", "--persist", agent="master")
    assert "inherited project settings" in r.stdout
    inherited = json.loads((board.parent / ".worktrees" / "doc" / ".claude" / "settings.json").read_text())
    assert inherited["permissions"]["allow"] == ["Bash(pytest:*)"]
    run(board, "spawn", "doc", "--stop", agent="master")


# ---- chief of staff seat / ui snapshot -----------------------------------

def test_cos_seat_wakes_and_may_integrate(board):
    run(board, "master", "take", agent="planner")
    assert "cos-x" in run(board, "master", "cos", "cos-x", agent="planner").stdout
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    run(board, "msg", "stuck: need a decision", "--to", "planner", agent="doc")
    rc, p = pending(board, "cos-x")
    assert rc == 0 and "stuck_messages" in p, "cos wakes on stuck messages too"
    cp = run(board, "prompt", "--cos", "--agent", "cos-x").stdout
    assert "CHIEF OF STAFF" in cp and "tickets merge" in cp
    pp = run(board, "prompt", "--master", "--agent", "planner").stdout
    assert "MASTER PLANNER" in pp and "cos-x" in pp and "ROUTE BY COMPLEXITY" in pp
    # take keeps the cos; clearing works
    run(board, "master", "take", agent="planner2")
    assert "cos-x" in run(board, "master", "cos", agent="planner2").stdout
    run(board, "master", "cos", "none", agent="planner2")
    assert "none" in run(board, "master", "cos", agent="planner2").stdout


def test_ui_snapshot_shape(board):
    run(board, "master", "take", agent="planner")
    run(board, "master", "init", agent="planner")
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    r = run(board, "ui", "--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    for k in ("goals", "util", "sprint", "in_flight", "review", "open", "agents", "health", "messages", "master"):
        assert k in d
    assert d["master"] == "planner" and d["in_flight"][0]["id"] == "T-001"
    assert any(a["name"] == "doc" and a["state"] == "busy" for a in d["agents"])
    assert "Mission" in d["goals"] or "MISSION" in d["goals"]


# ---- review regressions (2026-09-06) ------------------------------------

def test_worker_cmd_quotes_model_from_workforce(board):
    # The workforce record is writable by any agent; it must not reach the shell unquoted.
    import importlib.util
    run(board, "join", "evil", "--roles", "backend", "--model", "opus; echo INJECTED >pwned")
    spec = importlib.util.spec_from_file_location("tickets_under_test", TOOL)
    tk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tk)
    cmd = tk._worker_cmd(str(board), "evil")
    assert "--model 'opus; echo INJECTED >pwned'" in cmd, cmd
    # T-257: this shells out for real (cmd embeds `$(tickets prompt)`), so it
    # must not inherit the ambient environment -- an operator's real shell
    # exports TICKETS_DIR pointing at a live board so plain `tickets ...`
    # just works, and that value would otherwise leak straight through here.
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="evil",
             HOME=str(board.parent.parent / "home"))
    subprocess.run(cmd.replace("claude -p", "true -p"), shell=True, cwd=board.parent,
                   capture_output=True, env=e)
    assert not (board.parent / "pwned").exists()


def test_checkin_preserves_inbox_seen_limit_and_stop_blocks(board):
    run(board, "join", "bob", "--roles", "backend")
    run(board, "msg", "bob please look", "--to", "bob", agent="alice")
    run(board, "inbox", agent="bob")                      # marks inbox_seen
    run(board, "limit", "bob", "--note", "out", agent="bob")
    rec_path = board / "agents" / "bob.json"
    rec = json.loads(rec_path.read_text())
    assert rec.get("inbox_seen") and rec.get("limit")
    run(board, "here", agent="bob")                        # any check-in
    rec2 = json.loads(rec_path.read_text())
    assert rec2.get("inbox_seen") == rec["inbox_seen"]
    assert rec2.get("limit") == rec["limit"]
    rc, p = pending(board, "bob")
    assert "messages_to_me" not in p, p


# ---- objective + heartbeat (master drive mode) ----------------------------

def test_objective_set_show_done(board):
    r = run(board, "objective", agent="boss")
    assert "no objective" in r.stdout
    run(board, "master", "take", agent="boss")
    assert run(board, "objective", "Ship V1", agent="boss").returncode == 0
    out = run(board, "objective", agent="boss").stdout
    assert "Ship V1" in out and "sprint" in out and "review queue" in out
    assert run(board, "objective", "--done", "gates green", agent="boss").returncode == 0
    assert "MET" in run(board, "objective", agent="boss").stdout
    msgs = (board / "messages.jsonl").read_text()
    assert "objective set" in msgs and "objective met" in msgs


def test_master_heartbeat_drives_only_the_master_seat(board):
    run(board, "master", "take", agent="boss")
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")                          # T-001 claimed: nothing is ready for anyone
    run(board, "objective", "Ship V1", "--exit", "gates green", agent="boss")
    rc, p = pending(board, "boss")
    assert "drive" not in p                                   # no heartbeat configured
    r = run(board, "watch", "--once", "--heartbeat", "30", "--dry-run", agent="boss")
    rc, p = pending(board, "boss")
    assert "drive" in p and p["pending"] is True, p
    # the interactive stop hook never keeps a turn open for a heartbeat
    assert stop(board, "boss") == {}
    # printing the master prompt stamps drive_at, so the next poll is quiet
    out = run(board, "prompt", "--master", agent="boss").stdout
    assert "DRIVE THE OBJECTIVE" in out and "Ship V1" in out
    rc, p = pending(board, "boss")
    assert "drive" not in p
    # a plain worker is never driven; a seat spawned with --heartbeat is (standing seat)
    run(board, "join", "bob", "--roles", "backend")
    rc, p = pending(board, "bob")
    assert "drive" not in p
    run(board, "watch", "--once", "--heartbeat", "30", "--dry-run", agent="bob")
    rc, p = pending(board, "bob")
    assert "drive" in p
    out = run(board, "prompt", agent="bob").stdout
    assert "HEARTBEAT" in out and "Ship V1" in out
    rc, p = pending(board, "bob")
    assert "drive" not in p                                   # prompt stamped drive_at
    # a met objective stops the heartbeat
    run(board, "objective", "--done", "shipped", agent="boss")
    rec = json.loads((board / "agents" / "boss.json").read_text())
    rec.pop("drive_at", None)
    (board / "agents" / "boss.json").write_text(json.dumps(rec))
    rc, p = pending(board, "boss")
    assert "drive" not in p


def test_spawn_stop_stops_all_duplicate_watch_loops(board):
    """spawn --stop must SIGTERM every live loop for the name, not just the pid-file holder."""
    run(board, "join", "dup", "--roles", "docs")
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="dup",
               HOME=str(board.parent.parent / "home"))
    cwd = str(board.parent)
    argv = [sys.executable, str(TOOL), "watch", "--agent", "dup", "--every", "3600",
            "--persist", "--exec", "true"]
    p1 = subprocess.Popen(argv, env=env, cwd=cwd, start_new_session=True,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid_file = board / "agents" / "dup.watch.pid"
    _wait_for_path(pid_file)
    os.unlink(pid_file)  # orphan p1 from the lock file so a second loop can start
    p2 = subprocess.Popen(argv, env=env, cwd=cwd, start_new_session=True,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    r = run(board, "spawn", "dup", "--stop", agent="master")
    assert r.returncode == 0, r.stderr
    assert str(p1.pid) in r.stdout and str(p2.pid) in r.stdout
    assert "stopped" in r.stdout
    p1.wait(timeout=10)
    p2.wait(timeout=10)


def test_watch_sigterm_exits_within_poll_interval(board):
    """SIGTERM must not wait for the full --every sleep (T-332 / claude-fable scope)."""
    run(board, "join", "doc", "--roles", "docs")
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="doc",
               HOME=str(board.parent.parent / "home"))
    proc = subprocess.Popen(
        [sys.executable, str(TOOL), "watch", "--agent", "doc", "--every", "3600",
         "--persist", "--exec", "true"],
        env=env, cwd=str(board.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    time.sleep(0.5)
    t0 = time.time()
    proc.terminate()
    proc.wait(timeout=5)
    assert time.time() - t0 < 2.0, "SIGTERM should interrupt the sleep, not wait up to --every"


def test_dash_health_flag_duplicate_watchers(board):
    """T-433: dash/list/health surface watcher_count and flag count != 1."""
    run(board, "join", "dup", "--roles", "docs")
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="dup",
               HOME=str(board.parent.parent / "home"))
    cwd = str(board.parent)
    argv = [sys.executable, str(TOOL), "watch", "--agent", "dup", "--cwd", cwd,
            "--every", "3600", "--persist", "--exec", "true"]
    p1 = subprocess.Popen(argv, env=env, cwd=cwd, start_new_session=True,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid_file = board / "agents" / "dup.watch.pid"
    _wait_for_path(pid_file)
    os.unlink(pid_file)
    p2 = subprocess.Popen(argv, env=env, cwd=cwd, start_new_session=True,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    dash = run(board, "dash", "--once").stdout
    assert "watchers=2 !!" in dash
    ui = json.loads(run(board, "ui", "--json").stdout)
    dup = next(a for a in ui["agents"] if a["name"] == "dup")
    assert dup["watcher_count"] == 2
    assert any("2 watch loops" in h["msg"] for h in ui["health"])
    r = run(board, "spawn", "dup", "--stop", agent="master")
    assert r.returncode == 0, r.stderr
    p1.wait(timeout=10)
    p2.wait(timeout=10)
    ui2 = json.loads(run(board, "ui", "--json").stdout)
    dup2 = next(a for a in ui2["agents"] if a["name"] == "dup")
    assert dup2["watcher_count"] in (0, 1)


def test_spawn_passes_heartbeat_to_watch(board):
    run(board, "master", "take", agent="boss")
    r = run(board, "spawn", "boss", "--master", "--heartbeat", "15", "--exec", "true",
            "--every", "5", "--persist", agent="boss")
    assert r.returncode == 0, r.stderr
    try:
        rec = None
        for _ in range(40):
            time.sleep(0.25)
            rec = json.loads((board / "agents" / "boss.json").read_text())
            if rec.get("drive_every"):
                break
        assert rec and rec.get("drive_every") == 15, rec
    finally:
        run(board, "spawn", "boss", "--stop", agent="boss")


# ---- bounded growth: watch log, messages.jsonl, MASTER.md decision log ----

def test_watch_caps_run_output_during_a_single_run(board):
    """A single verbose run must not be able to blow past the log's rotation
    budget -- the old code only rotated between runs, so one big run grew the
    log unbounded. TICKETS_WATCH_LOG_MAX_BYTES lets the test use a tiny cap
    instead of waiting to produce 5 MB of real output."""
    run(board, "join", "doc", "--roles", "docs")
    script = "import sys; sys.stdout.write('x' * 50000)"
    cmd = "%s -c %s" % (sys.executable, shlex.quote(script))
    r = run(board, "watch", "--agent", "doc", "--once", "--exec", cmd,
            env={"TICKETS_WATCH_LOG_MAX_BYTES": "1000"})
    assert r.returncode == 0, r.stderr
    # the capped run's ~1000 bytes alone clears the same threshold, so the
    # very next log() call rotates it straight into doc.watch.log.1 -- that
    # is bounded growth working as intended, just not in the live file.
    log_dir = board / "agents"
    log = (log_dir / "doc.watch.log").read_text()
    rotated = (log_dir / "doc.watch.log.1").read_text() if (log_dir / "doc.watch.log.1").exists() else ""
    combined = log + rotated
    # count only the run of x's from the payload, not incidental x's in log
    # metadata like "exit" or "trigger".
    longest_run = max((len(g) for g in re.findall(r"x+", combined)), default=0)
    assert longest_run <= 1000
    assert "capped at 1000 bytes" in combined


def test_watch_log_cap_does_not_truncate_a_small_run(board):
    run(board, "join", "doc", "--roles", "docs")
    marker = board.parent / "ran.txt"
    r = run(board, "watch", "--agent", "doc", "--once", "--exec", "echo hello world > %s" % marker,
            env={"TICKETS_WATCH_LOG_MAX_BYTES": "1000000"})
    assert r.returncode == 0 and marker.read_text().strip() == "hello world"
    log = (board / "agents" / "doc.watch.log").read_text()
    assert "capped" not in log and "run 1 exit 0" in log


def test_messages_rotate_past_cap_and_load_messages_defaults_to_live_file(board):
    run(board, "join", "alice", "--roles", "backend")
    env = {"TICKETS_MESSAGES_MAX_BYTES": "200"}
    for i in range(20):
        r = run(board, "msg", "message number %d filler filler filler" % i, agent="alice", env=env)
        assert r.returncode == 0, r.stderr
    archives = sorted(board.glob("messages.*.jsonl"))
    assert archives, "expected messages.jsonl to have rotated to a dated archive"
    assert "message number 0 " in archives[0].read_text()
    live = (board / "messages.jsonl").read_text()
    assert "message number 19" in live
    # unread/live-tail readers only pay for the live file: the oldest posts
    # rotated out are not visible without asking for full history.
    assert "message number 0 " not in live
    out = run(board, "inbox", "--all", "--limit", "100", agent="alice").stdout
    assert "message number 0 " in out and "message number 19" in out


def test_never_checked_in_agent_still_gets_mail_sent_after_it_joined(board):
    """T-244 (root cause per sonnet-qa's T-241 repro): checkin()/join() used
    to never initialize inbox_seen, so an agent's guaranteed first state was
    since="" -- and unread()'s `if since and (...)` treats that falsy since
    as "skip the archive check", backwards for the agent with the LEAST
    history to fall back on. A DM addressed to a freshly-joined agent and
    then rotated away before that agent's first `tickets inbox` call was
    silently and permanently lost.

    Fix: checkin() now stamps inbox_seen to "now" the moment an agent's
    record is created (join, or any command that checks a new name in for
    the first time), so since is never "" for a real agent again -- mail
    sent after it joined is found via the ordinary T-212 archive-catchup
    path (since predates the live file's oldest survivor) even once it has
    rotated out of the live file.
    """
    run(board, "join", "dave", "--roles", "backend")  # dave never runs `tickets inbox`
    env = {"TICKETS_MESSAGES_MAX_BYTES": "200"}
    r = run(board, "msg", "IMPORTANT-FOR-DAVE", "--to", "dave", agent="alice", env=env)
    assert r.returncode == 0, r.stderr
    for i in range(20):
        r = run(board, "msg", "filler %d filler filler filler" % i, agent="alice", env=env)
        assert r.returncode == 0, r.stderr
    assert sorted(board.glob("messages.*.jsonl")), "expected rotation to have fired"

    out = run(board, "inbox", "--limit", "500", agent="dave").stdout
    assert "IMPORTANT-FOR-DAVE" in out, (
        "dave's very first ever inbox check must not silently drop a DM sent "
        "after he joined just because it was archived before he first checked -- "
        "got: %r" % out
    )


def test_new_agent_does_not_see_history_from_before_it_joined(board):
    """The flood question the ticket asked to be decided and written down:
    a brand-new agent's inbox_seen is stamped to its join time, so mail
    already on the board before it existed is deliberately NOT unread mail
    for it -- it is history, visible only via `tickets inbox --all`.

    T-228 note: the sleep below is load-bearing, not padding. `at` and
    `inbox_seen` are both whole-second stamps, so without a real clock gap
    the last filler message can share carol's join second, and "before she
    joined" then stops being a question this data can answer. That tie is
    decided deliberately in the other direction -- see
    test_message_in_the_same_second_as_a_join_is_delivered_not_dropped --
    so pin the flood policy on mail that is unambiguously older.
    """
    env = {"TICKETS_MESSAGES_MAX_BYTES": "200"}
    for i in range(20):
        r = run(board, "msg", "before-carol message %d filler filler" % i, agent="alice", env=env)
        assert r.returncode == 0, r.stderr
    time.sleep(1.1)  # make "before" mean an earlier second, not the same one
    run(board, "join", "carol", "--roles", "backend")  # joins after all prior mail

    out = run(board, "inbox", agent="carol").stdout
    assert "before-carol" not in out
    assert "inbox empty" in out

    full = run(board, "inbox", "--all", "--limit", "100", agent="carol").stdout
    assert "before-carol message 19" in full  # still on the board, just not "unread"


def test_message_in_the_same_second_as_a_join_is_delivered_not_dropped(board):
    """T-228, and the deliberate tie-break against T-244's flood policy.

    Second-resolution stamps cannot distinguish "posted just before this
    agent joined" from "posted just after", and the two policies collide on
    exactly that tie. It is resolved in favour of delivery, because the two
    ways of being wrong are not symmetric: showing one extra message that
    slightly predates the join is a cosmetic wart, while dropping it loses a
    DM forever -- and "welcome, take T-123" sent the instant a new agent
    appears is a real and common shape on this board, not a hypothetical.
    A new agent still never sees the *flood*; only the boundary second.
    """
    run(board, "join", "dave", "--roles", "backend")
    # T-327 composition: the join second is read from joined_at, which is now
    # the field that means "when this agent appeared". It used to be read from
    # inbox_seen, because T-244 stamped that at first check-in -- a stamp this
    # branch removes, since overloading a delivery receipt as a join clock is
    # what destroyed pre-join directed mail (see checkin()).
    joined = json.loads((board / "agents" / "dave.json").read_text())["joined_at"]
    # post with the message's own stamp forced onto dave's exact join second
    (board / "messages.jsonl").write_text(
        json.dumps({"at": joined, "from": "alice", "to": "dave",
                    "re": "", "text": "WELCOME-DAVE-TAKE-T123"}) + "\n")
    out = run(board, "inbox", agent="dave").stdout
    assert "WELCOME-DAVE-TAKE-T123" in out, (
        "a DM landing in the very second an agent joined must not be "
        "silently dropped -- got: %r" % out)


def test_legacy_agent_record_missing_inbox_seen_key_still_gets_archived_mail(board):
    """Reviewer-found gap (sonnet-deploy, T-244 review), re-pinned on the
    BEHAVIOUR rather than on the stamp that used to deliver it.

    The gap is real and this test still guards it: an agent record written
    before inbox_seen existed as a field is present on disk but has no
    inbox_seen key, and unread()'s old `if since and (...)` treated that
    empty since as "skip the archive check" -- so that one class of agent
    silently lost archived mail.

    T-244 closed it by making since never empty (checkin() stamped
    inbox_seen = now()). That stamp is gone on this branch: it also swallowed
    every message addressed to a seat before the seat joined, which is how
    briefs are delivered here and which T-327 measured and rejected. The
    defect is now fixed at its own site instead -- since="" reads the
    archives -- so the assertion moved from "the key came back" to "the mail
    arrived", which is what the reviewer was actually protecting. Asserting
    the stamp would only pin the mechanism, and the mechanism is what changed.
    """
    run(board, "join", "erin", "--roles", "backend")
    rec_path = board / "agents" / "erin.json"
    rec = json.loads(rec_path.read_text())
    rec.pop("inbox_seen", None)  # simulate a record written before the field existed
    rec_path.write_text(json.dumps(rec))

    env = {"TICKETS_MESSAGES_MAX_BYTES": "200"}
    run(board, "here", agent="erin")  # any ordinary check-in, not a join
    assert "inbox_seen" not in json.loads(rec_path.read_text()), (
        "a check-in must not stamp a delivery watermark it never earned -- "
        "that stamp is what destroyed pre-join directed mail"
    )
    time.sleep(1.1)  # now() is second-precision (T-228); force a real clock gap

    r = run(board, "msg", "IMPORTANT-FOR-ERIN", "--to", "erin", agent="alice", env=env)
    assert r.returncode == 0, r.stderr
    for i in range(20):
        r = run(board, "msg", "filler %d filler filler filler" % i, agent="alice", env=env)
        assert r.returncode == 0, r.stderr
    assert sorted(board.glob("messages.*.jsonl")), "expected rotation to have fired"

    out = run(board, "inbox", "--limit", "500", agent="erin").stdout
    assert "IMPORTANT-FOR-ERIN" in out, (
        "a record with no inbox_seen key at all must still find mail sent to "
        "it, even once that mail has rotated into an archive -- got: %r" % out
    )


def test_master_decision_log_trims_past_cap_and_archives(board):
    run(board, "master", "init", agent="boss")
    env = {"TICKETS_MASTER_LOG_MAX_BYTES": "300", "TICKETS_MASTER_LOG_KEEP_ENTRIES": "3"}
    for i in range(10):
        r = run(board, "master", "log", "decision number %d with some extra padding text" % i,
                agent="boss", env=env)
        assert r.returncode == 0, r.stderr
    body = (board / "MASTER.md").read_text()
    assert "decision number 9" in body, "the most recent decision must never be dropped"
    assert "decision number 0" not in body, "trimmed entries must leave the inline log"
    assert "archived to MASTER.decisions.archive.md" in body
    archive = (board / "MASTER.decisions.archive.md").read_text()
    assert "decision number 0" in archive, "trimmed entries must survive in the archive"
