"""T-1512: the seat service must not hang silently on a macOS-protected board.

A launchd agent that opens a board under ~/Downloads, ~/Documents or ~/Desktop
blocks in opendir() until the user grants privacy consent. The service now
probes each board with a timeout, logs 'blocked by macOS privacy: <board>',
records that for `service status`, and `service install` says what to grant.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import threading
import time

import pytest

from test_wakeup import TOOL, board, run  # noqa: F401


def _tickets():
    spec = importlib.util.spec_from_file_location("tickets_t1512", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seat_service():
    path = TOOL.parent / "seat_service.py"
    spec = importlib.util.spec_from_file_location("seat_service_t1512", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tcc_protected_folder_is_string_only_and_darwin_only(tmp_path):
    ss = _seat_service()
    home = str(tmp_path / "u")
    dl = os.path.join(home, "Downloads")
    assert ss.tcc_protected_folder(os.path.join(dl, "steer", ".tickets"),
                                   home=home, system="Darwin") == dl
    assert ss.tcc_protected_folder(os.path.join(home, "Documents", "b"),
                                   home=home, system="Darwin").endswith("Documents")
    # A sibling whose name merely starts with Downloads is not protected.
    assert ss.tcc_protected_folder(os.path.join(home, "Downloads2", "b"),
                                   home=home, system="Darwin") is None
    assert ss.tcc_protected_folder(os.path.join(home, "code", "b"),
                                   home=home, system="Darwin") is None
    assert ss.tcc_protected_folder(os.path.join(dl, "b"),
                                   home=home, system="Linux") is None


def test_probe_reports_blocked_within_timeout_and_does_not_stack(tmp_path):
    ss = _seat_service()
    release = threading.Event()
    calls = []

    def stuck_listdir(path):
        calls.append(path)
        release.wait(30)
        return []

    b = str(tmp_path / "Downloads" / "repo" / ".tickets")
    t0 = time.time()
    rec = ss.probe_board(b, timeout=0.3, home=str(tmp_path), system="Darwin",
                         _listdir=stuck_listdir)
    assert time.time() - t0 < 5
    assert rec["state"] == "blocked"
    assert rec["protected"] == str(tmp_path / "Downloads")
    assert ss.describe_board_health(rec).startswith("blocked by macOS privacy: " + b)
    # The next pass reports the same stuck probe instead of starting another.
    rec2 = ss.probe_board(b, timeout=0.1, home=str(tmp_path), system="Darwin",
                          _listdir=stuck_listdir)
    assert rec2["state"] == "blocked"
    assert len(calls) == 1
    # Consent granted: the stuck read returns and the next probe is ok.
    release.set()
    deadline = time.time() + 5
    while time.time() < deadline:
        rec3 = ss.probe_board(b, timeout=1, _listdir=lambda p: [])
        if rec3["state"] == "ok":
            break
    assert rec3["state"] == "ok"


def test_probe_denied_and_missing(tmp_path):
    ss = _seat_service()

    def denied(path):
        raise PermissionError(1, "Operation not permitted", path)

    rec = ss.probe_board(str(tmp_path / "Desktop" / "b"), timeout=2,
                         home=str(tmp_path), system="Darwin", _listdir=denied)
    assert rec["state"] == "denied"
    assert "macOS privacy" in ss.describe_board_health(rec)
    missing = ss.probe_board(str(tmp_path / "nope"), timeout=2)
    assert missing["state"] == "missing"
    real = tmp_path / "real"
    (real / "agents").mkdir(parents=True)
    assert ss.probe_board(str(real), timeout=2)["state"] == "ok"


def test_reconcile_skips_blocked_board_without_touching_seats(tmp_path, monkeypatch):
    tk = _tickets()
    ss = _seat_service()
    b = tmp_path / "board"
    (b / "agents").mkdir(parents=True)
    ss.save_desired(str(b), "doc", "active",
                    spec={"argv": ["true"], "worktree": str(tmp_path)})
    touched = []
    monkeypatch.setattr(ss, "probe_board", lambda board: {
        "board": board, "state": "blocked", "detail": "no answer",
        "protected": "/Users/x/Downloads", "checked_at": "t"})
    monkeypatch.setattr(ss, "list_desired_seats",
                        lambda board: touched.append(board) or iter(()))
    monkeypatch.setattr(tk, "_seat_service", lambda: ss)
    report = tk._service_reconcile_once(home=str(tmp_path), boards=[str(b)])
    assert report["boards"] == 1
    assert [h["state"] for h in report["health"]] == ["blocked"]
    assert touched == [], "a blocked board must not be listed (that is the hang)"
    assert report["started"] == []


def test_daemon_log_lines_cover_start_skip_error_and_health_changes(monkeypatch):
    tk = _tickets()
    ss = _seat_service()
    monkeypatch.setattr(tk, "_seat_service", lambda: ss)
    blocked = {"board": "/b", "state": "blocked", "detail": "x",
               "protected": "/Users/x/Downloads"}
    ok = dict(blocked, state="ok")
    last = {}
    first = tk._service_report_log_lines(
        {"health": [blocked], "started": [], "stopped": [], "errors": [], "skipped": []},
        last)
    assert first == ["blocked by macOS privacy: /b (x)"]
    # Same state next pass: quiet.
    assert tk._service_report_log_lines(
        {"health": [blocked], "started": [], "stopped": [], "errors": [], "skipped": []},
        last) == []
    rep = {"health": [ok], "started": ["/b:doc:pid=7"], "stopped": [],
           "errors": ["/b:ops:boom"], "skipped": ["/b:qa:already-live"]}
    lines = tk._service_report_log_lines(rep, last)
    assert lines == ["board readable: /b", "started /b:doc:pid=7",
                     "error /b:ops:boom", "skip /b:qa:already-live"]
    # Steady state: skip not repeated, but every error is.
    rep2 = dict(rep, started=[], skipped=["/b:qa:already-live"])
    assert tk._service_report_log_lines(rep2, last) == ["error /b:ops:boom"]
    # A seat that was started and later skips logs the skip.
    rep3 = dict(rep2, errors=[], skipped=["/b:doc:already-live"])
    assert tk._service_report_log_lines(rep3, last) == ["skip /b:doc:already-live"]


def test_status_shows_daemon_blocked_board_and_grant(board, tmp_path):
    ss = _seat_service()
    svc = tmp_path / "svc"
    svc.mkdir()
    ss.register_board(str(board), home=str(svc))
    real = os.path.realpath(str(board))
    ss.save_board_health([{
        "board": real, "state": "blocked", "detail": "no answer from opendir() for 9s",
        "protected": "/Users/someone/Downloads", "checked_at": "t"}], home=str(svc))
    r = run(board, "service", "status", agent="master",
            env={"ATMAN_SERVICE_HOME": str(svc)})
    assert r.returncode == 0, r.stderr
    assert "daemon: last reconcile" in r.stdout
    assert "daemon: blocked by macOS privacy: %s" % real in r.stdout
    assert "Full Disk Access" in r.stdout
    # A health file older than the stale window reads as a silent daemon.
    path = ss.board_health_path(str(svc))
    data = json.loads(open(path).read())
    data["written_at"] = time.time() - 3600
    with open(path, "w") as f:
        json.dump(data, f)
    r2 = run(board, "service", "status", agent="master",
             env={"ATMAN_SERVICE_HOME": str(svc)})
    assert "hung or not running" in r2.stdout


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS privacy only")
def test_install_names_protected_board_and_binary(board, tmp_path):
    ss = _seat_service()
    home = board.parent.parent / "home"  # run() sets HOME here
    guarded = home / "Downloads" / "proj" / ".tickets"
    guarded.mkdir(parents=True)
    ss.register_board(str(guarded), home=str(home))
    env = {"ATMAN_SERVICE_HOME": str(home)}
    r = run(board, "service", "install", "--no-load", agent="master", env=env)
    try:
        assert r.returncode == 0, r.stderr
        assert "needs consent to read ~/Downloads" in r.stdout
        assert str(guarded) in r.stdout
        assert "Full Disk Access" in r.stdout
        assert "Files and Folders" in r.stdout
    finally:
        run(board, "service", "uninstall", "--no-load", agent="master", env=env)
