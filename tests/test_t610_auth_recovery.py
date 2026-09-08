"""Cursor auth recovery stays cheap, explicit, and separate from quota."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from watch_reaper import collect_watch_pids_from_board

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="operator", env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent,
             HOME=str(board.parent.parent / "home"))
    if env:
        e.update(env)
    r = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                       text=True, env=e, cwd=board.parent)
    if args and args[0] == "spawn" and "--stop" not in args:
        collect_watch_pids_from_board(board)
    return r


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty",
                    "-m", "init"], check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    assert run(b, "create", "Work", "--role", "docs").returncode == 0
    assert run(b, "join", "cursor-seat", "--roles", "docs",
               "--harness", "cursor").returncode == 0
    return b


def fake_agent(tmp_path, status, login_status=None):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    state = tmp_path / "logged-in"
    script = bindir / "agent"
    script.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = status ]; then\n"
        "  if [ -f %s ]; then echo %s; else echo %s; fi\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = login ]; then touch %s; exit 0; fi\n"
        "echo OK\nexit 0\n" % (
            str(state), repr(login_status or status), repr(status), str(state)))
    script.chmod(0o755)
    return dict(PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""))


def agent_record(board):
    return json.loads((board / "agents" / "cursor-seat.json").read_text())


def test_stopped_ide_does_not_matter_when_headless_identity_is_ready(board, tmp_path):
    env = fake_agent(tmp_path, "Logged in as dev@example.test")
    r = run(board, "harness", "auth", "cursor-seat", env=env)
    assert r.returncode == 0
    assert "Ready" in r.stdout and "dev@example.test" in r.stdout
    assert agent_record(board)["auth_check"]["state"] == "ready"


def test_missing_credential_is_login_required_with_exact_recovery(board, tmp_path):
    env = fake_agent(tmp_path, "Not logged in")
    before = json.loads((board / "workforce.json").read_text())["cursor-seat"].copy()
    r = run(board, "harness", "auth", "cursor-seat", env=env)
    assert r.returncode == 1
    assert "Login required" in r.stdout
    assert "recover:  agent login" in r.stdout
    snap = run(board, "ui", "--json", env=env)
    seat = next(a for a in json.loads(snap.stdout)["agents"] if a["name"] == "cursor-seat")
    assert seat["auth"] == "login_required" and seat["auth_login_cmd"] == "agent login"
    refused = run(board, "spawn", "cursor-seat", "--roles", "backend", env=env)
    assert refused.returncode != 0 and "watcher not started" in refused.stderr
    assert json.loads((board / "workforce.json").read_text())["cursor-seat"] == before
    assert not (board / "agents" / "cursor-seat.watch.pid").exists()


def test_real_quota_is_not_reported_as_auth(board, tmp_path):
    env = fake_agent(tmp_path, "Usage limit reached; try again later")
    r = run(board, "harness", "auth", "cursor-seat", env=env)
    assert r.returncode == 1
    assert "Usage quota reached" in r.stdout
    assert "Login required" not in r.stdout
    assert agent_record(board)["auth_check"]["state"] == "quota"


def test_stale_pid_is_reclaimed_but_a_live_pid_is_not(board, tmp_path):
    env = fake_agent(tmp_path, "Logged in as dev@example.test")
    pid = board / "agents" / "cursor-seat.watch.pid"
    pid.write_text("99999999")
    r = run(board, "harness", "auth", "cursor-seat", "--recover-stale", env=env)
    assert r.returncode == 0 and "stale watcher lock reclaimed" in r.stdout
    assert not pid.exists()
    pid.write_text(str(os.getpid()))
    r = run(board, "harness", "auth", "cursor-seat", "--recover-stale", env=env)
    assert r.returncode == 2 and "watcher is running" in r.stdout
    assert pid.read_text() == str(os.getpid())
    pid.unlink()


def test_login_verifies_then_spawn_preserves_registration(board, tmp_path):
    env = fake_agent(tmp_path, "Not logged in", "Logged in as dev@example.test")
    before = json.loads((board / "workforce.json").read_text())["cursor-seat"].copy()
    rec_before = agent_record(board)
    r = run(board, "harness", "auth", "cursor-seat", "--login", env=env)
    assert r.returncode == 0 and "verification:" in r.stdout and "Ready" in r.stdout
    r = run(board, "spawn", "cursor-seat", "--worktree", rec_before["worktree"],
            "--max-runs", "1", "--every", "1", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    after = json.loads((board / "workforce.json").read_text())["cursor-seat"]
    assert after == before
    assert agent_record(board)["worktree"] == rec_before["worktree"]
