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


def _stale_authoritative_ready(board, name="alice", harness="cursor"):
    """Months-old authoritative Ready bound to a context this run won't match."""
    old = "2026-01-01T00:00:00Z"
    ctx = {
        "runner_id": "fixture-runner", "runner_kind": "host",
        "hostname": "fixture-host", "username": "fixture-user",
        "binary": "/fixture/bin/agent", "argv0": "agent",
        "env_fingerprint": "0000000000000000",
        "repo_root": "/fixture/repo", "worktree": "/fixture/repo",
        "origin_url": "https://example.test/fixture.git",
        "expected_origin": "https://example.test/fixture.git",
        "agent_id": name, "ticket_agent": name, "lifecycle": "persistent",
    }
    path = board / "agents" / ("%s.json" % name)
    rec = json.loads(path.read_text())
    rec["auth_check"] = {
        "state": "ready", "harness": harness, "at": old, "exit": 0,
        "detail": "Logged in", "authoritative": True, "execution_context": ctx,
        "status_cmd": "agent status", "login_cmd": "agent login",
    }
    rec["preflight"] = {"ok": True, "at": old, "state": "ready", "harness": harness}
    path.write_text(json.dumps(rec, indent=2))
    return old


def test_old_authoritative_ready_does_not_pass_logged_out_probe(board, tmp_path):
    setup_docs_seat(board)
    old = _stale_authoritative_ready(board)
    env = fake_cli(tmp_path, "agent", "Not logged in")
    env["CURSOR_FIXTURE_CHANGED"] = "1"
    r = run(board, "dispatch", "T-001", "--to", "alice", "--harness", "cursor",
            agent="boss", env=env)
    blob = r.stdout + r.stderr
    assert r.returncode != 0, blob
    assert "logged out" in blob
    t = json.loads((board / "T-001.json").read_text())
    assert not t.get("reserved_for")
    rec = json.loads((board / "agents" / "alice.json").read_text())
    assert rec["auth_check"]["at"] == old  # merge kept the old record
    assert rec["preflight"]["at"] == old  # no fresh ok written from it


def test_old_authoritative_ready_does_not_pass_missing_binary(board, tmp_path):
    setup_docs_seat(board)
    old = _stale_authoritative_ready(board)
    (tmp_path / "empty-bin").mkdir()
    env = dict(PATH=str(tmp_path / "empty-bin"), CLAUDE_FIXTURE_CHANGED="1")
    r = run(board, "dispatch", "T-001", "--to", "alice", "--harness", "cursor",
            agent="boss", env=env)
    blob = r.stdout + r.stderr
    assert r.returncode != 0, blob
    assert "binary missing" in blob
    t = json.loads((board / "T-001.json").read_text())
    assert not t.get("reserved_for")
    rec = json.loads((board / "agents" / "alice.json").read_text())
    assert rec["preflight"]["at"] == old


def test_empty_or_unknown_state_refuses():
    for rec in ({}, {"state": ""}, {"state": "mystery", "harness": "cursor"}):
        reason = pf.dispatch_refuse(rec, "cursor")
        assert "auth state unknown" in reason
        assert "atm harness auth <seat>" in reason
    assert pf.dispatch_refuse({"state": "ready"}, "cursor") == ""
    assert pf.dispatch_refuse({"state": "quota"}, "cursor") == ""
    assert pf.dispatch_refuse({"state": "unsupported"}, "cursor") == ""
    assert pf.route_skip({"state": "mystery"}, "cursor") == ""


def test_stored_unknown_state_is_not_a_cached_pass(board):
    setup_docs_seat(board)
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = board / "agents" / "alice.json"
    rec = json.loads(path.read_text())
    rec["auth_check"] = {"state": "", "harness": "cursor", "at": at}
    rec.pop("preflight", None)
    path.write_text(json.dumps(rec, indent=2))
    assert pf.cached_positive(rec, at, harness="cursor") is None
    assert "auth state unknown" in pf.dispatch_refuse(rec["auth_check"], "cursor")


def test_cached_positive_for_other_harness_does_not_skip_probe(board, tmp_path):
    setup_docs_seat(board)
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = board / "agents" / "alice.json"
    rec = json.loads(path.read_text())
    rec["preflight"] = {"ok": True, "at": at, "state": "ready", "harness": "claude"}
    rec["auth_check"] = {"state": "ready", "harness": "claude", "at": at}
    path.write_text(json.dumps(rec, indent=2))
    assert pf.cached_positive(rec, at, harness="cursor") is None
    assert pf.cached_positive(rec, at, harness="claude")
    assert pf.cached_positive(rec, at) is None
    (tmp_path / "empty-bin").mkdir()
    r = run(board, "dispatch", "T-001", "--to", "alice", "--harness", "cursor",
            agent="boss", env=dict(PATH=str(tmp_path / "empty-bin")))
    assert r.returncode != 0
    assert "binary missing" in (r.stdout + r.stderr)
