"""T-1043: binary+auth preflight before dispatch; usage does not block."""
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ticket_board import preflight as pf
from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def run(board, *args, agent="boss", env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent,
             HOME=str(board.parent.parent / "home"),
             TICKETS_DISPATCH_NO_SPAWN="1",
             TICKETS_CACHE_DIR=str(board.parent.parent / "cache"))
    e.pop("TICKETS_STOP_HOOK", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, env=e, cwd=str(board.parent))


def fake_cli(tmp_path, name, text, rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text("#!/bin/sh\necho %s\nexit %d\n" % (repr(text), rc))
    script.chmod(stat.S_IRWXU)
    return dict(PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""))


def setup_docs_seat(board, name="alice", harness="cursor"):
    assert run(board, "join", name, "--roles", "docs", "--harness", harness,
               agent=name).returncode == 0
    assert run(board, "join", "boss", "--roles", "docs", agent="boss").returncode == 0
    assert run(board, "master", "take", "--owner", "boss", agent="boss").returncode == 0


def test_missing_binary_refuses_with_install_hint(board, tmp_path):
    setup_docs_seat(board)
    env = dict(PATH=str(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    r = run(board, "dispatch", "T-001", "--to", "alice", "--harness", "cursor",
            agent="boss", env=env)
    blob = r.stdout + r.stderr
    assert r.returncode != 0
    assert "binary missing" in blob
    assert "install" in blob.lower() and "agent" in blob
    t = json.loads((board / "T-001.json").read_text())
    assert not t.get("reserved_for")


def test_logged_out_refuses_with_relogin_hint(board, tmp_path):
    setup_docs_seat(board)
    env = fake_cli(tmp_path, "agent", "Not logged in")
    r = run(board, "dispatch", "T-001", "--to", "alice", "--harness", "cursor",
            agent="boss", env=env)
    blob = r.stdout + r.stderr
    assert r.returncode != 0
    assert "logged out" in blob
    assert "agent login" in blob
    t = json.loads((board / "T-001.json").read_text())
    assert not t.get("reserved_for")


def test_authenticated_dispatches(board, tmp_path):
    setup_docs_seat(board)
    env = fake_cli(tmp_path, "agent", "Logged in as fixture@example.test")
    r = run(board, "dispatch", "T-001", "--to", "alice", "--harness", "cursor",
            agent="boss", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "reserved for alice" in r.stdout
    t = json.loads((board / "T-001.json").read_text())
    assert t.get("reserved_for") == "alice"


def test_unreadable_usage_does_not_block_dispatch(board, tmp_path):
    setup_docs_seat(board)
    env = fake_cli(tmp_path, "agent", "Logged in as fixture@example.test")
    r = run(board, "dispatch", "T-001", "--to", "alice", "--harness", "cursor",
            agent="boss", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    rec = json.loads((board / "agents" / "alice.json").read_text())
    rec["auth_check"]["state"] = "quota"
    rec["auth_check"]["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec.pop("preflight", None)
    (board / "agents" / "alice.json").write_text(json.dumps(rec, indent=2))
    run(board, "reserve", "T-001", "--drop", agent="boss")
    r = run(board, "dispatch", "T-001", "--to", "alice", "--harness", "cursor",
            agent="boss", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert pf.dispatch_refuse({"state": "quota", "harness": "cursor"}) == ""
    assert pf.dispatch_refuse({"state": "ready", "harness": "cursor",
                               "detail": "usage unknown"}) == ""


def test_route_skips_logged_out_keeps_authenticated(board, tmp_path):
    setup_docs_seat(board, "alice", "cursor")
    assert run(board, "join", "bob", "--roles", "docs", "--harness", "cursor",
               agent="bob").returncode == 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bob = json.loads((board / "agents" / "bob.json").read_text())
    bob["preflight"] = {"ok": True, "at": now, "state": "ready", "harness": "cursor"}
    (board / "agents" / "bob.json").write_text(json.dumps(bob, indent=2))
    env = fake_cli(tmp_path, "agent", "Not logged in")
    routed = run(board, "route", "--only", "alice", "bob", agent="boss", env=env)
    assert routed.returncode == 0, routed.stderr + routed.stdout
    t = json.loads((board / "T-001.json").read_text())
    assert t.get("suggested") == "bob"


def test_positive_cache_skips_reprobe(board, tmp_path):
    setup_docs_seat(board)
    now = datetime.now(timezone.utc)
    rec = json.loads((board / "agents" / "alice.json").read_text())
    rec["preflight"] = {
        "ok": True,
        "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "state": "ready",
        "harness": "cursor",
    }
    (board / "agents" / "alice.json").write_text(json.dumps(rec, indent=2))
    env = dict(PATH=str(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    r = run(board, "dispatch", "T-001", "--to", "alice", "--harness", "cursor",
            agent="boss", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    stale = now - timedelta(seconds=pf.CACHE_SECS + 5)
    rec["preflight"]["at"] = stale.strftime("%Y-%m-%dT%H:%M:%SZ")
    rec.pop("auth_check", None)
    (board / "agents" / "alice.json").write_text(json.dumps(rec, indent=2))
    run(board, "reserve", "T-001", "--drop", agent="boss")
    denied = run(board, "dispatch", "T-001", "--to", "alice", "--harness", "cursor",
                 agent="boss", env=env)
    assert denied.returncode != 0
    assert "binary missing" in (denied.stdout + denied.stderr)
