"""T-1499: per-user seat service owns persistent watchers.

spawn --persist records desired state; reconcile restarts dead runners;
concurrent starts cannot double a seat; service install captures login env.
"""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from test_wakeup import TOOL, board, run  # noqa: F401


def _tickets():
    spec = importlib.util.spec_from_file_location("tickets_t1499", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seat_service():
    path = TOOL.parent / "seat_service.py"
    spec = importlib.util.spec_from_file_location("seat_service_t1499", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _live(board_path, owner):
    tk = _tickets()
    return tk._live_watch_pids(owner, board=str(board_path))


def _wait_live(board_path, owner, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        pids = _live(board_path, owner)
        if pids:
            return pids
        time.sleep(0.1)
    return []


def _wait_gone(board_path, owner, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _live(board_path, owner):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def service_home(tmp_path, monkeypatch):
    home = tmp_path / "svc-home"
    home.mkdir()
    monkeypatch.setenv("ATMAN_SERVICE_HOME", str(home))
    monkeypatch.delenv("ATMAN_SPAWN_DIRECT", raising=False)
    return home


def test_seat_may_spawn_rule_is_deterministic():
    ss = _seat_service()
    active = {"state": "active", "backoff_until": None}
    assert ss.seat_may_spawn(active, 0) is True
    assert ss.seat_may_spawn(active, 1) is False
    assert ss.seat_may_spawn({"state": "stopped"}, 0) is False
    assert ss.seat_may_spawn({"state": "inactive"}, 0) is False
    future = time.time() + 60
    assert ss.seat_may_spawn({"state": "active", "backoff_until": future}, 0) is False
    past = time.time() - 5
    assert ss.seat_may_spawn({"state": "active", "backoff_until": past}, 0) is True


def test_spawn_persist_records_desired_without_service(board, service_home):
    run(board, "join", "doc", "--roles", "docs")
    r = run(board, "spawn", "doc", "--exec", "true", "--every", "5", "--persist",
            agent="master")
    assert r.returncode == 0, r.stderr
    assert "watcher for doc started" in r.stdout
    desired = json.loads((board / "agents" / "doc.desired").read_text())
    assert desired["state"] == "active"
    assert desired["spec"]["worktree"]
    assert desired["spec"]["argv"]
    assert "atm service install" in r.stdout
    run(board, "spawn", "doc", "--stop", agent="master")
    stopped = json.loads((board / "agents" / "doc.desired").read_text())
    assert stopped["state"] == "stopped"


def test_service_install_writes_unit_and_login_env(board, service_home):
    ss = _seat_service()
    r = run(board, "service", "install", "--no-load", agent="master",
            env={"ATMAN_SERVICE_HOME": str(service_home)})
    assert r.returncode == 0, r.stderr + r.stdout
    assert ss.service_installed(str(service_home))
    env_path = ss.env_snapshot_path(str(service_home))
    assert Path(env_path).is_file()
    snap = json.loads(Path(env_path).read_text())
    assert "env" in snap and isinstance(snap["env"], dict)
    plist = Path(ss.plist_path(str(service_home)))
    marker = Path(ss.service_home(str(service_home))) / "installed"
    assert plist.is_file() or marker.is_file()
    r2 = run(board, "service", "uninstall", "--no-load", agent="master",
             env={"ATMAN_SERVICE_HOME": str(service_home)})
    assert r2.returncode == 0, r2.stderr
    assert not ss.service_installed(str(service_home))


def test_kill_runner_reconcile_restarts(board, service_home):
    ss = _seat_service()
    ss.mark_installed(str(service_home))
    run(board, "join", "doc", "--roles", "docs")
    env = {"ATMAN_SERVICE_HOME": str(service_home)}
    r = run(board, "spawn", "doc", "--exec", "true", "--every", "5", "--persist",
            agent="master", env=env)
    assert r.returncode == 0, r.stderr
    assert "desired=active" in r.stdout or "watcher for doc started" in r.stdout
    pids = _wait_live(board, "doc")
    assert pids, "service-owned spawn should start a runner immediately: %s" % r.stdout
    os.kill(pids[0], signal.SIGKILL)
    assert _wait_gone(board, "doc")
    desired = json.loads((board / "agents" / "doc.desired").read_text())
    assert desired["state"] == "active"
    r2 = run(board, "service", "reconcile", agent="master", env=env)
    assert r2.returncode == 0, r2.stderr
    restarted = _wait_live(board, "doc")
    assert restarted, r2.stdout
    assert restarted[0] != pids[0]
    run(board, "spawn", "doc", "--stop", agent="master", env=env)


def test_service_restart_restores_active_seats(board, service_home):
    """Reboot-equivalent: service reconcile with no live runners restores actives."""
    ss = _seat_service()
    ss.mark_installed(str(service_home))
    run(board, "join", "doc", "--roles", "docs")
    env = {"ATMAN_SERVICE_HOME": str(service_home)}
    r = run(board, "spawn", "doc", "--exec", "true", "--every", "5", "--persist",
            agent="master", env=env)
    assert r.returncode == 0, r.stderr
    pids = _wait_live(board, "doc")
    assert pids
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    assert _wait_gone(board, "doc")
    r2 = run(board, "service", "reconcile", agent="master", env=env)
    assert r2.returncode == 0, r2.stderr
    assert _wait_live(board, "doc"), r2.stdout
    run(board, "spawn", "doc", "--stop", agent="master", env=env)


def test_concurrent_start_no_duplicate_runners(board, service_home):
    tk = _tickets()
    ss = _seat_service()
    ss.mark_installed(str(service_home))
    run(board, "join", "doc", "--roles", "docs")
    env = {"ATMAN_SERVICE_HOME": str(service_home)}
    r = run(board, "spawn", "doc", "--exec", "true", "--every", "5", "--persist",
            agent="master", env=env)
    assert r.returncode == 0, r.stderr
    pids = _wait_live(board, "doc")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    assert _wait_gone(board, "doc")

    results = []

    def once():
        results.append(tk._service_try_start_seat(
            str(board), "doc", home=str(service_home)))

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(once) for _ in range(4)]
        for f in futs:
            f.result(timeout=15)
    live = _live(board, "doc")
    assert len(live) == 1, "concurrent starts must leave exactly one runner: %s %s" % (
        live, results)
    started = sum(1 for item in results if item.get("started"))
    assert started == 1, results
    run(board, "spawn", "doc", "--stop", agent="master", env=env)


def test_merge_launch_env_prefers_login_path():
    ss = _seat_service()
    merged = ss.merge_launch_env(
        {"PATH": "/base/bin", "TICKET_AGENT": "keep"},
        {"PATH": "/login/bin:/usr/bin", "HOME": "/Users/x", "TICKET_AGENT": "ignore"})
    assert merged["PATH"].startswith("/login/bin")
    assert "/base/bin" in merged["PATH"]
    assert merged["TICKET_AGENT"] == "keep"
    assert merged["HOME"] == "/Users/x"


def test_render_launchd_has_keepalive():
    ss = _seat_service()
    body = ss.render_launchd_plist("/usr/bin/python3", "/tmp/tickets.py", "/tmp/log")
    assert "KeepAlive" in body
    assert "RunAtLoad" in body
    assert "service" in body and "run" in body
    unit = ss.render_systemd_unit("/usr/bin/python3", "/tmp/tickets.py")
    assert "Restart=always" in unit
