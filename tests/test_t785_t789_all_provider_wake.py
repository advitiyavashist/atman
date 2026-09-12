"""T-785 / T-789: persist-watch poke and harness-neutral mail follow-up."""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from watch_reaper import collect_watch_pids_from_board

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _mod():
    spec = importlib.util.spec_from_file_location("tickets_t785", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(board, *args, agent="lead", env=None, cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    if env:
        e.update(env)
    r = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                       env=e, cwd=str(cwd or board.parent))
    if args and args[0] == "spawn" and "--stop" not in args and "--list" not in args:
        collect_watch_pids_from_board(board)
    return r


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    r = run(b, "create", "Write docs", "--role", "docs", cwd=repo)
    assert r.returncode == 0, r.stderr
    return b


def test_hooks_rejects_unknown_and_accepts_all_providers(board):
    r = run(board, "hooks", "not-a-harness", "--agent", "x")
    assert r.returncode != 0
    r = run(board, "hooks", "--help")
    blob = r.stdout + r.stderr
    for name in ("claude", "cursor", "codex", "agy", "gemini", "devin", "grok"):
        assert name in blob


def test_seat_harness_prefers_tool_over_claude_default():
    tk = _mod()
    assert tk._should_poke_persist("no live endpoint")
    assert tk._should_poke_persist("unsupported provider")
    assert not tk._should_poke_persist("woken")
    assert not tk._should_poke_persist("remote bridge required")


def test_msg_honors_tool_not_claude(board, monkeypatch):
    seen = {}
    tk = _mod()

    def fake_wake(board_arg, seat, text, harness=None, message_id=""):
        seen["harness"] = harness
        seen["seat"] = seat
        return "no live endpoint"

    monkeypatch.setattr(tk, "_session_adapters", lambda: type("SA", (), {
        "wake_seat": staticmethod(fake_wake),
        "wake_payload": staticmethod(lambda fmt, m: "payload"),
        "provider_for_harness": staticmethod(lambda h: h or ""),
    })())
    monkeypatch.setattr(tk, "_poke_persist_watch", lambda board_arg, seat: False)
    assert run(board, "join", "cursor-seat", "--roles", "backend", "--harness", "cursor").returncode == 0
    ns = type("A", (), {
        "owner": "lead", "text": "please look", "to": "cursor-seat", "re": "", "task": True,
    })()
    tk.cmd_msg(ns, str(board))
    assert seen["harness"] == "cursor"
    assert seen["seat"] == "cursor-seat"


def test_msg_honors_tool_field_when_harness_missing(board, monkeypatch):
    seen = {}
    tk = _mod()

    def fake_wake(board_arg, seat, text, harness=None, message_id=""):
        seen["harness"] = harness
        return "no live endpoint"

    monkeypatch.setattr(tk, "_session_adapters", lambda: type("SA", (), {
        "wake_seat": staticmethod(fake_wake),
        "wake_payload": staticmethod(lambda fmt, m: "payload"),
        "provider_for_harness": staticmethod(lambda h: h or ""),
    })())
    monkeypatch.setattr(tk, "_poke_persist_watch", lambda board_arg, seat: True)
    wf = json.loads((board / "workforce.json").read_text()) if (board / "workforce.json").exists() else {}
    wf["codex-seat"] = {"tool": "codex", "roles": ["backend"]}
    (board / "workforce.json").write_text(json.dumps(wf))
    captured = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: captured.append(" ".join(str(x) for x in a)))
    ns = type("A", (), {
        "owner": "lead", "text": "please look", "to": "codex-seat", "re": "", "task": True,
    })()
    tk.cmd_msg(ns, str(board))
    assert seen["harness"] == "codex"
    assert any("watch-poked" in line for line in captured)


def test_provider_for_harness_maps_grok_to_cursor():
    spec = importlib.util.spec_from_file_location(
        "session_adapters_t785", str(TOOL.parent / "session_adapters.py"))
    sa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sa)
    assert sa.provider_for_harness("grok") == "cursor"
    assert sa.provider_for_harness("grokbots") == "cursor"
    assert sa.provider_for_harness("agy") == ""
    assert sa.provider_for_harness("gemini") == ""
    assert sa.provider_for_harness("devin") == ""
    assert sa.provider_for_harness("cursor") == "cursor"
    assert sa.provider_for_harness("claude") == "claude"


def test_msg_pokes_live_persist_watch(board):
    seat = "poked-seat"
    assert run(board, "join", seat, "--roles", "docs", "--harness", "cursor",
               "--wake-mode", "continuous").returncode == 0
    wt = board.parent
    proc = subprocess.Popen(
        [sys.executable, str(TOOL), "watch", "--agent", seat, "--every", "3600",
         "--persist", "--exec", "true", "--cwd", str(wt)],
        cwd=str(wt),
        env=dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=seat,
                 HOME=str(board.parent.parent / "home")),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True)
    collect_watch_pids_from_board(board)
    try:
        pid_file = board / "agents" / (seat + ".watch.pid")
        for _ in range(40):
            if pid_file.exists() and pid_file.read_text().strip().isdigit():
                break
            time.sleep(0.05)
        assert pid_file.exists(), "watch did not write a pid file"
        r = run(board, "msg", "wake this persist loop", "--to", seat, "--task", agent="lead")
        assert r.returncode == 0, r.stderr + r.stdout
        assert "watch-poked" in r.stdout, r.stdout
        cmdline = subprocess.run(
            ["ps", "-p", str(proc.pid), "-o", "command="],
            capture_output=True, text=True).stdout
        assert str(TOOL) in cmdline
        assert "sol-agy-harness" not in cmdline
    finally:
        try:
            os.kill(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
        collect_watch_pids_from_board(board)


def test_poke_does_not_nameerror():
    tk = _mod()
    assert callable(tk._poke_persist_watch)
    assert tk._poke_persist_watch("/no-such-board", "nobody") is False
