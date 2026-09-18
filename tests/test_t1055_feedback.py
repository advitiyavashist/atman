"""T-1055: `atm feedback` -- a local-only, pasteable run summary.

No telemetry: the promise is that the board stays on the user's machine, so
this reads only existing board data, prints to stdout, and sends nothing
anywhere. Throwaway boards only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def run(board, *args, agent=""):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    e.pop("TICKET_SEAT", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        e.pop(var, None)
    if agent:
        e["TICKET_SESSION_ID"] = "test-session-" + agent
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          env=e, cwd=str(board.parent))


def load_ticket(board, tid):
    return json.loads((board / (tid + ".json")).read_text())


def save_ticket(board, t):
    (board / (t["id"] + ".json")).write_text(json.dumps(t, indent=2) + "\n")


def test_feedback_on_a_fresh_board_prints_zeros_not_errors(board):
    """A board where nothing has succeeded yet is the most informative case,
    not an error case: `atm feedback` must not crash on it."""
    for p in board.glob("T-*.json"):
        p.unlink()
    r = run(board, "feedback")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "tickets created:          0" in out
    assert "tickets reviewed:         0" in out
    assert "tickets accepted:         0" in out
    assert "tickets done, unverified: 0" in out
    assert "release overrides:        0" in out
    assert "reopens: 0" in out
    assert "objective: not set" in out
    assert "seats LIMITED: 0" in out
    assert 'seats whose recorded watcher is gone (closest tracked state to "stalled"): 0' in out
    assert "atm version: " in out
    assert "harness logins: not checked" in out
    assert "Nothing here is sent anywhere." in out


def test_feedback_counts_reviewed_accepted_and_unverified_done(board):
    sha = "a" * 40
    t1 = load_ticket(board, "T-001")
    t1["status"] = "done"
    t1["review_head"] = sha
    t1["review_events"] = [{"kind": "accept", "by": "boss", "at": "2026-09-16T00:00:00Z", "sha": sha}]
    save_ticket(board, t1)

    r = run(board, "create", "Second ticket", "--role", "docs")
    assert r.returncode == 0, r.stderr
    t2 = load_ticket(board, "T-002")
    t2["status"] = "done"  # done, but never reviewed or accepted (T-992)
    save_ticket(board, t2)

    out = run(board, "feedback").stdout
    assert "tickets created:          2" in out
    assert "tickets reviewed:         1" in out
    assert "tickets accepted:         1" in out
    assert "tickets done, unverified: 1" in out


def test_feedback_counts_reopens(board):
    t = load_ticket(board, "T-001")
    t["status"] = "claimed"
    t["owner"] = "alice"
    save_ticket(board, t)
    r = run(board, "reopen", "T-001", "--notes", "handing to someone else")
    assert r.returncode == 0, r.stderr + r.stdout
    out = run(board, "feedback").stdout
    assert "reopens: 1" in out


def test_feedback_counts_a_limited_seat_without_naming_it(board):
    r = run(board, "limit", "alice", "--note", "quota")
    assert r.returncode == 0, r.stderr
    out = run(board, "feedback").stdout
    assert "seats LIMITED: 1" in out
    assert "alice" not in out


def test_feedback_does_not_print_a_seat_named_after_the_user(board):
    """Seat names are often the person's own login name: count, never name."""
    user = "kavanauser"
    r = run(board, "limit", user, "--note", "quota")
    assert r.returncode == 0, r.stderr
    out = run(board, "feedback").stdout
    assert "seats LIMITED: 1" in out
    assert user not in out


def test_feedback_does_not_print_objective_text(board):
    secret_path = "/Users/someone/private/clientA/roadmap.md"
    token = "ghp_" + "Z9" * 18
    r = run(board, "objective", "ship %s using %s" % (secret_path, token),
            "--exit", "token %s works" % token)
    assert r.returncode == 0, r.stderr
    out = run(board, "feedback").stdout
    assert "objective: set (state: active, exit criteria: yes)" in out
    assert secret_path not in out
    assert "clientA" not in out
    assert token not in out
    assert "ghp_" not in out


def test_feedback_does_not_print_the_project_branch(board):
    """The user's branch name can carry anything; only Atman's version prints."""
    token = "ghp_" + "Q7" * 18
    branch = "feat/" + token
    subprocess.run(["git", "-C", str(board.parent), "checkout", "-q", "-b", branch], check=True)
    out = run(board, "feedback").stdout
    assert token not in out
    assert "ghp_" not in out
    assert branch not in out
    assert "atm version: " in out
    assert "inside a git worktree: yes" in out


def test_feedback_runs_no_subprocess(board, monkeypatch, capsys):
    """In-process: any process spawn during cmd_feedback is a failure."""
    import argparse
    import subprocess as sp
    import tickets as tk

    t = load_ticket(board, "T-001")
    agents = board / "agents"
    agents.mkdir(exist_ok=True)
    (agents / "alice.json").write_text(json.dumps({"owner": "alice", "cwd": str(board.parent)}))
    (agents / "alice.watch.pid").write_text("999999\n")
    (agents / "bob.json").write_text(json.dumps({"owner": "bob", "limit": {"note": "quota"}}))
    assert t["id"] == "T-001"

    spawned = []

    def refuse(*args, **kwargs):
        spawned.append(args[:1])
        raise AssertionError("atm feedback spawned a process: %r" % (args[:1],))

    monkeypatch.setattr(sp.Popen, "__init__", refuse)
    for name in ("system", "posix_spawn", "posix_spawnp", "fork", "execv", "execvp", "popen"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, refuse)
    monkeypatch.chdir(board.parent)
    tk.cmd_feedback(argparse.Namespace(), str(board))
    out = capsys.readouterr().out
    assert spawned == []
    assert "seats LIMITED: 1" in out
    assert 'seats whose recorded watcher is gone (closest tracked state to "stalled"): 1' in out
    assert "alice" not in out and "bob" not in out


def _snapshot(*roots):
    snap = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(str(root)):
            for name in dirnames + filenames:
                p = os.path.join(dirpath, name)
                st = os.lstat(p)
                data = b""
                if os.path.isfile(p) and not os.path.islink(p):
                    with open(p, "rb") as f:
                        data = f.read()
                snap[p] = (st.st_mtime_ns, st.st_size, os.path.islink(p) and os.readlink(p), data)
    return snap


def test_feedback_never_executes_harnesses_or_writes_files(board):
    """Harness presence is a PATH lookup: stub binaries that would drop a
    marker when run must never be run, and nothing on disk may change --
    including the ~/.local/bin/codex retarget the catalog probe performs."""
    home = board.parent.parent / "home"
    home.mkdir(exist_ok=True)
    stubs = board.parent.parent / "stubs"
    stubs.mkdir()
    marker = board.parent.parent / "executed.marker"
    names = ("claude", "codex", "agent", "cursor-agent", "agy", "devin", "gemini",
             "git", "ps", "lsof", "uname", "sysctl", "sw_vers")
    for name in names:
        stub = stubs / name
        stub.write_text("#!/bin/sh\necho %s >> %s\n" % (name, marker))
        stub.chmod(0o755)
    # A chatgpt extension codex binary retarget_stale_local_codex would link.
    ext = home / ".vscode" / "extensions" / "openai.chatgpt-9.9.9" / "bin" / "arm64" / "codex"
    ext.parent.mkdir(parents=True)
    ext.write_text("#!/bin/sh\necho ext >> %s\n" % marker)
    ext.chmod(0o755)

    # An expired provider limit: agent_liveness() would clear it on disk.
    r = run(board, "limit", "alice", "--note", "quota")
    assert r.returncode == 0, r.stderr
    rec_path = board / "agents" / "alice.json"
    rec = json.loads(rec_path.read_text())
    rec["limit"] = dict(rec["limit"], source="provider", reset_at="2000-01-01T00:00:00Z")
    rec_path.write_text(json.dumps(rec, indent=2) + "\n")
    git_dir = board.parent / ".git"
    assert git_dir.is_dir()
    before = _snapshot(board, git_dir, home, stubs)
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="", HOME=str(home),
             PATH=str(stubs) + os.pathsep + os.environ.get("PATH", ""))
    for var in ("TICKETS_STOP_HOOK", "TICKET_SEAT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
                "CURSOR_SESSION_ID", "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        e.pop(var, None)
    out = subprocess.run([sys.executable, str(TOOL), "feedback"], capture_output=True,
                         text=True, env=e, cwd=str(board.parent))
    assert out.returncode == 0, out.stderr
    assert not marker.exists(), marker.read_text()
    assert not (home / ".local" / "bin" / "codex").exists()
    assert _snapshot(board, git_dir, home, stubs) == before
    assert "Claude Code" in out.stdout.split("harnesses found on PATH:")[1].splitlines()[0]
    assert "harness logins: not checked" in out.stdout

    # Board discovery without TICKETS_DIR must not shell out to git either.
    e.pop("TICKETS_DIR")
    out = subprocess.run([sys.executable, str(TOOL), "feedback"], capture_output=True,
                         text=True, env=e, cwd=str(board.parent))
    assert out.returncode == 0, out.stderr
    assert "tickets created:          1" in out.stdout
    assert not marker.exists(), marker.read_text()
    assert _snapshot(board, git_dir, home, stubs) == before


def test_feedback_redacts_home_and_repo_path(board):
    home = str(board.parent.parent / "home")
    repo = str(board.parent)
    out = run(board, "feedback").stdout
    assert home not in out
    assert repo not in out
    assert "<HOME>" in out or "<RUN>" in out


def _feedback_env(home, board):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="", HOME=str(home))
    e.pop("TICKETS_STOP_HOOK", None)
    e.pop("TICKET_SEAT", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        e.pop(var, None)
    return e


def test_feedback_redacts_outside_a_git_repo(tmp_path):
    """git_state() is None outside a worktree; the checkout must still be scrubbed.

    Old cmd_feedback took run/repo_name from git_state()["top"], so a non-git
    cwd printed the full checkout (e.g. code/myrepo) and still claimed the
    text was redacted. This fails on that code.
    """
    home = tmp_path / "home"
    repo = tmp_path / "code" / "myrepo"
    board = repo / ".tickets"
    home.mkdir()
    repo.mkdir(parents=True)
    assert not (repo / ".git").exists()

    env = _feedback_env(home, board)
    created = subprocess.run(
        [sys.executable, str(TOOL), "create", "Throwaway ticket"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert created.returncode == 0, created.stderr
    r = subprocess.run(
        [sys.executable, str(TOOL), "feedback"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert r.returncode == 0, r.stderr
    out = r.stdout

    home_s = str(home)
    repo_s = str(repo)
    for leaked in (
        home_s, os.path.abspath(home_s), os.path.realpath(home_s),
        repo_s, os.path.abspath(repo_s), os.path.realpath(repo_s),
        str(tmp_path), os.path.realpath(tmp_path),
    ):
        assert leaked not in out, leaked
    assert "myrepo" not in out
    assert "code/myrepo" not in out
    assert "board: /" not in out
    assert not re.search(r"(?m)(^|[\s:=])/(?:Users|home|private|var|tmp|Volumes)/", out), out
    claim = "Redacted before printing: your home directory, this checkout's path, and its folder name."
    paths_remain = any(p in out for p in (home_s, repo_s, "myrepo"))
    assert (claim in out) == (not paths_remain)
    assert claim in out


def test_feedback_redaction_claim_only_when_every_path_was_scrubbed():
    """The printed claim is a promise: empty run (git_state None) is not success."""
    import tickets as tk

    leaked = "board: /Users/me/code/myrepo/.tickets"
    half = tk._feedback_redact(leaked, home="/Users/me", run="", repo_name="")
    assert "myrepo" in half
    assert not tk._feedback_redaction_ok(half, home="/Users/me", run="", repo_name="")

    clean = tk._feedback_redact(
        leaked, home="/Users/me", run="/Users/me/code/myrepo",
        repo="/Users/me/code/myrepo", repo_name="myrepo")
    assert "/Users/me" not in clean
    assert "myrepo" not in clean
    assert tk._feedback_redaction_ok(
        clean, home="/Users/me", run="/Users/me/code/myrepo",
        repo="/Users/me/code/myrepo", repo_name="myrepo")


_AUDIT_SITECUSTOMIZE = """
import os, sys
_marker = os.environ["FEEDBACK_SPAWN_MARKER"]
_events = {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork",
           "os.forkpty", "os.spawn", "pty.spawn"}
def _hook(event, args):
    if event in _events:
        with open(_marker, "a") as f:
            f.write("%s %r\\n" % (event, args[:2]))
sys.addaudithook(_hook)
"""


def _no_spawn_env(tmp_path, home):
    site = tmp_path / "audit-site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(_AUDIT_SITECUSTOMIZE)
    stubs = tmp_path / "audit-stubs"
    stubs.mkdir()
    marker = tmp_path / "spawned.marker"
    for name in ("git", "ps", "lsof", "uname", "sysctl"):
        stub = stubs / name
        stub.write_text("#!/bin/sh\necho stub-%s >> %s\n" % (name, marker))
        stub.chmod(0o755)
    e = _feedback_env(home, "")
    e.pop("TICKETS_DIR", None)
    e.pop("ATMAN_BOARD_CONFIG", None)
    e["PYTHONPATH"] = str(site)
    e["FEEDBACK_SPAWN_MARKER"] = str(marker)
    e["PATH"] = str(stubs) + os.pathsep + os.environ.get("PATH", "")
    # Control: the hook really does record a spawn in this environment.
    subprocess.run([sys.executable, "-c", "import subprocess; subprocess.run(['true'])"],
                   env=e, check=False)
    assert marker.exists() and "subprocess.Popen" in marker.read_text()
    marker.unlink()
    return e, marker


def _git_repo_with_board(path, home):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    env = _feedback_env(home, path / ".tickets")
    r = subprocess.run([sys.executable, str(TOOL), "create", "Throwaway ticket"],
                       capture_output=True, text=True, env=env, cwd=str(path))
    assert r.returncode == 0, r.stderr
    return path / ".tickets"


def _assert_no_paths(text, *paths):
    for p in paths:
        for form in (str(p), os.path.realpath(str(p))):
            assert form not in text, (form, text)
    assert not re.search(r"(?m)(^|[\s:=('\"])/(?:Users|home|private|var|tmp|Volumes)/", text), text


def test_feedback_shared_board_config_from_a_separate_repo_spawns_nothing(tmp_path):
    """TICKETS_DIR unset, ATMAN_BOARD_CONFIG maps this repo to another repo's
    board: board_dir() goes through _refuse_unbound_live_board ->
    _cwd_belongs_to_board -> _init_cwd_worktree_root, which ran git."""
    home = tmp_path / "home"
    home.mkdir()
    shared = _git_repo_with_board(tmp_path / "shared", home)
    here = tmp_path / "work"
    here.mkdir()
    subprocess.run(["git", "init", "-q", str(here)], check=True)
    cfg = tmp_path / "board.json"
    cfg.write_text(json.dumps({"boards": {str(here): str(shared)}}))

    e, marker = _no_spawn_env(tmp_path, home)
    e["ATMAN_BOARD_CONFIG"] = str(cfg)
    r = subprocess.run([sys.executable, str(TOOL), "feedback"], capture_output=True,
                       text=True, env=e, cwd=str(here))
    assert r.returncode == 0, r.stderr
    assert "tickets created:          1" in r.stdout
    assert not marker.exists(), marker.read_text()
    _assert_no_paths(r.stdout + r.stderr, tmp_path, home, here, shared)


def test_feedback_from_a_parent_folder_refuses_without_spawning(tmp_path):
    """The one-child-board case refuses (exit 1) -- and must not run git first."""
    home = tmp_path / "home"
    home.mkdir()
    parent = tmp_path / "parent"
    child_board = _git_repo_with_board(parent / "child", home)

    e, marker = _no_spawn_env(tmp_path, home)
    r = subprocess.run([sys.executable, str(TOOL), "feedback"], capture_output=True,
                       text=True, env=e, cwd=str(parent))
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "REFUSING" in r.stderr
    assert not marker.exists(), marker.read_text()
    _assert_no_paths(r.stdout + r.stderr, tmp_path, home, parent, child_board)

