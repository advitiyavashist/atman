"""Wake-up layer: pending / stop-hook / watch / boot / hooks / codex-hook.

Runs the real CLI as a subprocess against a throwaway board so the tests see
exactly what a hook or a watcher sees. No network, no model, no home-dir writes.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="", stdin="", env=None, cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "", HOME=str(board.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    if env:
        e.update(env)
    where = cwd or (board.parent if board.parent.is_dir() else Path("/"))
    return subprocess.run([sys.executable, str(TOOL), *args], input=stdin, capture_output=True, text=True,
                          env=e, cwd=where)


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
    run(board, "msg", "hello everyone", agent="alice")
    rc, p = pending(board, "bob")
    assert rc == 1 and p.get("broadcasts") == 1 and p["pending"] is False
    run(board, "msg", "bob please look", "--to", "bob", agent="alice")
    rc, p = pending(board, "bob")
    assert rc == 0 and "messages_to_me" in p


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
        r = run(board, "hooks", "claude", "--settings", str(settings))
        assert r.returncode == 0, r.stderr
    s = json.loads(settings.read_text())
    assert len(s["hooks"]["SessionStart"]) == 1 and len(s["hooks"]["UserPromptSubmit"]) == 1
    cmds = [h["command"] for e in s["hooks"]["Stop"] for h in e["hooks"]]
    assert "other-tool" in cmds and any("stop-hook" in c for c in cmds) and len(cmds) == 2
    assert "Bash(ls)" in s["permissions"]["allow"] and "Bash(tickets:*)" in s["permissions"]["allow"]
    run(board, "hooks", "claude", "--settings", str(settings), "--no-stop")
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
    r = run(board, "spawn", "doc", "--exec", "true", "--every", "5", agent="master")
    assert r.returncode == 0, r.stderr
    assert "watcher for doc started" in r.stdout
    pid_file = board / "agents" / "doc.watch.pid"
    time.sleep(1.5)
    assert pid_file.exists()
    lst = run(board, "spawn", "--list").stdout
    assert "doc" in lst and "pid" in lst
    r2 = run(board, "spawn", "doc", "--exec", "true", agent="master")
    assert "already running" in r2.stdout
    r3 = run(board, "spawn", "doc", "--stop", agent="master")
    assert "asked watcher" in r3.stdout
    for _ in range(20):
        if not pid_file.exists():
            break
        time.sleep(1)
    assert not pid_file.exists(), "watcher exits and releases its lock on --stop"
    assert (board.parent / ".worktrees" / "doc").is_dir(), "spawn created the worktree"


def test_spawn_inherits_project_settings(board):
    (board.parent / ".claude").mkdir()
    (board.parent / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(pytest:*)"]}}))
    r = run(board, "spawn", "doc", "--exec", "true", "--every", "5", agent="master")
    assert "inherited project settings" in r.stdout
    inherited = json.loads((board.parent / ".worktrees" / "doc" / ".claude" / "settings.json").read_text())
    assert inherited["permissions"]["allow"] == ["Bash(pytest:*)"]
    run(board, "spawn", "doc", "--stop", agent="master")
