"""T-954: joining/spawning another seat must not rebind the caller's session.

A leadership session that runs `tickets join worker` or `tickets spawn worker`
used to write the SESSION-keyed identity as the worker. Later `tickets msg`
then posted as the worker even with TICKET_AGENT set inline (CoS incident
2026-09-14 after PR #116). Throwaway boards only.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TICKETS = os.path.join(os.path.dirname(HERE), "tickets.py")
SOURCE = [sys.executable, TICKETS]


def identity_of(tickets_dir, sid):
    path = Path(tickets_dir) / ".identities" / hashlib.sha256(sid.encode()).hexdigest()[:16]
    return path.read_text().strip() if path.exists() else None


def run(board, args, env=None, session=None, agent=None):
    e = dict(os.environ)
    for var in (
        "TICKET_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "CODEX_SESSION_ID",
        "CURSOR_SESSION_ID",
        "TERM_SESSION_ID",
        "TICKET_SEAT",
    ):
        e.pop(var, None)
    e.pop("TICKET_AGENT", None)
    e.pop("TICKETS_DIR", None)
    if session is not None:
        e["TICKET_SESSION_ID"] = session
    if agent is not None:
        e["TICKET_AGENT"] = agent
    e.update(env or {})
    if "TICKETS_DIR" not in e:
        e["TICKETS_DIR"] = os.path.join(board, ".tickets")
    return subprocess.run(
        [sys.executable, TICKETS] + args,
        cwd=board,
        env=e,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(["git", "-C", str(d), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    r = run(str(d), ["init"], session="setup", agent="setup")
    assert r.returncode == 0, r.stderr
    return str(d)


def tickets_dir(repo):
    return os.path.join(repo, ".tickets")


def senders(repo):
    path = os.path.join(tickets_dir(repo), "messages.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line).get("from") for line in f if line.strip()]


def test_leader_join_worker_leaves_leader_session_identity(repo):
    board = tickets_dir(repo)
    r = run(repo, ["join", "cos", "--roles", "review", "--alias", "cos"],
            session="cos-sid", agent="cos")
    assert r.returncode == 0, r.stderr
    assert identity_of(board, "cos-sid") == "cos"

    r = run(repo, ["join", "worker", "--roles", "backend"],
            session="cos-sid", agent="cos")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "on behalf" in r.stdout
    assert identity_of(board, "cos-sid") == "cos"

    r = run(repo, ["msg", "still-the-leader", "--to", "worker"],
            session="cos-sid", agent="cos")
    assert r.returncode == 0, r.stderr
    assert senders(repo)[-1] == "cos"


def test_leader_spawn_persist_does_not_rebind_caller(repo):
    board = tickets_dir(repo)
    r = run(repo, ["join", "cos", "--roles", "review"],
            session="cos-sid", agent="cos")
    assert r.returncode == 0, r.stderr
    assert identity_of(board, "cos-sid") == "cos"

    r = run(repo, ["spawn", "worker", "--exec", "true", "--every", "5", "--persist"],
            session="cos-sid", agent="cos")
    try:
        assert r.returncode == 0, r.stderr + r.stdout
        assert identity_of(board, "cos-sid") == "cos"
        r = run(repo, ["msg", "after-spawn", "--to", "worker"],
                session="cos-sid", agent="cos")
        assert r.returncode == 0, r.stderr
        assert senders(repo)[-1] == "cos"
    finally:
        run(repo, ["spawn", "worker", "--stop"], session="cos-sid", agent="cos")


def test_explicit_self_join_rebinds_a_wrongly_pinned_session(repo):
    board = tickets_dir(repo)
    run(repo, ["join", "cos", "--roles", "review"], session="cos-sid", agent="cos")
    # Simulate the incident: session file already points at the worker.
    key = hashlib.sha256(b"cos-sid").hexdigest()[:16]
    pinned = Path(board) / ".identities" / key
    pinned.write_text("worker\n")
    assert identity_of(board, "cos-sid") == "worker"

    r = run(repo, ["join", "cos", "--roles", "review"],
            session="cos-sid", agent="cos")
    assert r.returncode == 0, r.stderr + r.stdout
    assert identity_of(board, "cos-sid") == "cos"


def test_self_prints_seat_and_why(repo):
    run(repo, ["join", "cos", "--roles", "review"], session="cos-sid", agent="cos")
    r = run(repo, ["self"], session="cos-sid", agent="cos")
    assert r.returncode == 0, r.stderr
    assert "seat:   cos" in r.stdout
    assert "why:    session-keyed join record" in r.stdout


def test_join_retired_name_refused_without_transfer(repo):
    run(repo, ["join", "old-ceo", "--roles", "backend"],
        session="old-sid", agent="old-ceo")
    r = run(repo, ["retire", "old-ceo"], session="old-sid", agent="old-ceo")
    assert r.returncode == 0, r.stderr + r.stdout

    r = run(repo, ["join", "old-ceo", "--roles", "backend"],
            session="new-sid", agent="new-worker")
    assert r.returncode != 0
    assert "retired" in (r.stderr + r.stdout)
    assert identity_of(tickets_dir(repo), "new-sid") is None


def test_join_current_master_refused_from_other_seat(repo):
    run(repo, ["join", "boss", "--roles", "planning"],
        session="boss-sid", agent="boss")
    r = run(repo, ["master", "take"], session="boss-sid", agent="boss")
    assert r.returncode == 0, r.stderr + r.stdout

    r = run(repo, ["join", "boss", "--roles", "backend"],
            session="worker-sid", agent="worker")
    assert r.returncode != 0
    assert "leadership" in (r.stderr + r.stdout)
    assert identity_of(tickets_dir(repo), "worker-sid") is None


def test_supervisor_launch_env_strips_inherited_ticket_seat(repo, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("tickets_t954", TICKETS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("TICKET_SEAT", "ceo-seat")
    monkeypatch.setenv("TICKET_AGENT", "ceo-seat")
    monkeypatch.setenv("TICKET_SESSION_ID", "ceo-sid")
    monkeypatch.setenv("CURSOR_SESSION_ID", "ceo-cursor")
    monkeypatch.setenv("PATH", "/caller/bin:/usr/bin")
    env = mod._supervisor_launch_env(tickets_dir(repo), "worker")
    assert env["TICKET_SEAT"] == "worker"
    assert env["TICKET_AGENT"] == "worker"
    assert env["TICKETS_WATCH_PINNED"] == "worker"
    assert env.get("CURSOR_SESSION_ID") is None
    assert env["TICKET_SESSION_ID"].startswith("launch:worker:")
    assert "ceo-seat" not in env.get("TICKET_SEAT", "")
    assert env["TICKET_SESSION_ID"] != "ceo-sid"
    local = os.path.expanduser("~/.local/bin")
    assert env["PATH"].startswith("/caller/bin:")
    assert env["PATH"].index("/caller/bin") < env["PATH"].index(local)


def test_watch_from_foreign_ticket_seat_prints_pinned_owner(repo):
    run(repo, ["join", "worker", "--roles", "backend"], session="w-sid", agent="worker")
    r = run(
        repo,
        ["watch", "--agent", "worker", "--exec", "true", "--once", "--force", "--every", "1"],
        session="ceo-sid",
        agent="ceo-seat",
        env={
            "TICKET_SEAT": "ceo-seat",
            "TICKET_AGENT": "ceo-seat",
            "TICKET_SESSION_ID": "ceo-sid",
        },
    )
    text = r.stdout + r.stderr
    assert r.returncode == 0, text
    assert "seat=worker" in text
    assert "inherited TICKET_SEAT/TICKET_AGENT/TICKET_SESSION_ID stripped" in text


def test_spawn_from_foreign_ticket_seat_child_env(repo):
    run(repo, ["join", "ceo-seat", "--roles", "review"], session="ceo-sid", agent="ceo-seat")
    r = run(
        repo,
        ["spawn", "worker", "--exec", "true", "--every", "3600", "--max-runs", "1"],
        session="ceo-sid",
        agent="ceo-seat",
        env={
            "TICKET_SEAT": "ceo-seat",
            "TICKET_AGENT": "ceo-seat",
            "TICKET_SESSION_ID": "ceo-sid",
        },
    )
    try:
        text = r.stdout + r.stderr
        assert r.returncode == 0, text
        assert "seat=worker pinned" in text
        pid_path = Path(tickets_dir(repo)) / "agents" / "worker.watch.pid"
        assert pid_path.is_file(), text
        pid = int(pid_path.read_text().strip())
        env_text = subprocess.check_output(["ps", "eww", "-p", str(pid)], text=True)
        assert "TICKET_SEAT=worker" in env_text
        assert "TICKET_AGENT=worker" in env_text
        assert "TICKET_SEAT=ceo-seat" not in env_text
        assert identity_of(tickets_dir(repo), "ceo-sid") == "ceo-seat"
    finally:
        run(repo, ["spawn", "worker", "--stop"], session="ceo-sid", agent="ceo-seat")


def _path_from_ps(env_text):
    for token in env_text.split():
        if token.startswith("PATH="):
            return token[len("PATH="):]
    return ""


def test_watcher_launch_preserves_caller_path_order(repo):
    """A spawned watcher must keep the caller's PATH ahead of fallback dirs."""
    caller_first = "/caller-first-bin"
    local = os.path.expanduser("~/.local/bin")
    run(repo, ["join", "ceo-seat", "--roles", "review"], session="ceo-sid", agent="ceo-seat")
    r = run(
        repo,
        ["spawn", "worker", "--exec", "true", "--every", "3600", "--max-runs", "1"],
        session="ceo-sid",
        agent="ceo-seat",
        env={
            "TICKET_SEAT": "ceo-seat",
            "TICKET_AGENT": "ceo-seat",
            "TICKET_SESSION_ID": "ceo-sid",
            "PATH": caller_first + ":" + os.environ.get("PATH", "/usr/bin"),
        },
    )
    try:
        text = r.stdout + r.stderr
        assert r.returncode == 0, text
        pid_path = Path(tickets_dir(repo)) / "agents" / "worker.watch.pid"
        assert pid_path.is_file(), text
        pid = int(pid_path.read_text().strip())
        env_text = subprocess.check_output(["ps", "eww", "-p", str(pid)], text=True)
        child_path = _path_from_ps(env_text)
        assert child_path.startswith(caller_first + ":"), child_path
        assert child_path.index(caller_first) < child_path.index(local), child_path
    finally:
        run(repo, ["spawn", "worker", "--stop"], session="ceo-sid", agent="ceo-seat")


def test_watch_dry_run_skips_reexec_and_auth(repo):
    """--dry-run starts no model turn: no identity re-exec and no auth gate."""
    run(repo, ["join", "worker", "--roles", "backend"], session="w-sid", agent="worker")
    r = run(
        repo,
        ["watch", "--agent", "worker", "--once", "--force", "--every", "1", "--dry-run"],
        session="ceo-sid",
        agent="ceo-seat",
        env={
            "TICKET_SEAT": "ceo-seat",
            "TICKET_AGENT": "ceo-seat",
            "TICKET_SESSION_ID": "ceo-sid",
        },
    )
    text = r.stdout + r.stderr
    assert r.returncode == 0, text
    assert "auth paused" not in text
    assert "inherited TICKET_SEAT/TICKET_AGENT/TICKET_SESSION_ID stripped" not in text
