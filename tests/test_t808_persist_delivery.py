"""T-808: directed mail must start persistent work without an operator keystroke."""
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
    spec = importlib.util.spec_from_file_location("tickets_t808", str(TOOL))
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


def _ns(text, to, task=False, owner="lead"):
    return type("A", (), {
        "owner": owner, "text": text, "to": to, "re": "", "task": task,
    })()


def test_queued_offline_and_supervised_labels_persist_poke(board, monkeypatch):
    tk = _mod()
    poked = []
    labels = iter(["queued-offline",
                   "supervised (no persist/tmux or ACP control sock; agent -p --resume "
                   "is a new paid run, not pause-resume)"])

    def fake_wake(board_arg, seat, text, harness=None, message_id=""):
        return next(labels)

    monkeypatch.setattr(tk, "_session_adapters", lambda: type("SA", (), {
        "wake_seat": staticmethod(fake_wake),
        "wake_payload": staticmethod(lambda fmt, m: "payload"),
        "provider_for_harness": staticmethod(lambda h: h or ""),
    })())
    monkeypatch.setattr(tk, "_poke_persist_watch", lambda board_arg, seat: poked.append(seat) or True)
    for harness, seat in (("codex", "codex-seat"), ("cursor", "cursor-seat")):
        assert run(board, "join", seat, "--roles", "backend", "--harness", harness,
                   "--wake-mode", "continuous").returncode == 0
        captured = []
        monkeypatch.setattr("builtins.print", lambda *a, **k: captured.append(" ".join(str(x) for x in a)))
        tk.cmd_msg(_ns("please act now", seat), str(board))
        assert any("watch-poked" in line for line in captured), captured
        rec = json.loads((board / "agents" / (seat + ".json")).read_text())
        assert rec["wake_delivery"]["label"] == "watch-poked"
        assert rec["wake_delivery"]["poked"] is True
        assert rec["wake_delivery"]["message_id"]
    assert poked == ["codex-seat", "cursor-seat"]


def test_woken_native_inject_does_not_persist_poke(board, monkeypatch):
    tk = _mod()
    poked = []

    monkeypatch.setattr(tk, "_session_adapters", lambda: type("SA", (), {
        "wake_seat": staticmethod(lambda *a, **k: "woken"),
        "wake_payload": staticmethod(lambda fmt, m: "payload"),
        "provider_for_harness": staticmethod(lambda h: h or ""),
    })())
    monkeypatch.setattr(tk, "_poke_persist_watch", lambda board_arg, seat: poked.append(seat) or True)
    assert run(board, "join", "claude-seat", "--roles", "backend", "--harness", "claude",
               "--wake-mode", "continuous").returncode == 0
    captured = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: captured.append(" ".join(str(x) for x in a)))
    tk.cmd_msg(_ns("please act now", "claude-seat"), str(board))
    assert poked == []
    assert any("wake: claude-seat -> woken" in line for line in captured), captured
    rec = json.loads((board / "agents" / "claude-seat.json").read_text())
    assert rec["wake_delivery"]["label"] == "woken"
    assert rec["wake_delivery"]["poked"] is False


def test_replayed_message_id_is_idempotent(board, monkeypatch):
    tk = _mod()
    wakes = []
    pokes = []

    monkeypatch.setattr(tk, "_session_adapters", lambda: type("SA", (), {
        "wake_seat": staticmethod(lambda *a, **k: wakes.append(k.get("message_id")) or "queued-offline"),
        "wake_payload": staticmethod(lambda fmt, m: "payload"),
        "provider_for_harness": staticmethod(lambda h: h or ""),
    })())
    monkeypatch.setattr(tk, "_poke_persist_watch", lambda board_arg, seat: pokes.append(seat) or True)
    assert run(board, "join", "agy-seat", "--roles", "backend", "--harness", "agy",
               "--wake-mode", "continuous").returncode == 0
    captured = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: captured.append(" ".join(str(x) for x in a)))
    tk.cmd_msg(_ns("same instruction", "agy-seat"), str(board))
    rec = json.loads((board / "agents" / "agy-seat.json").read_text())
    mid = rec["wake_delivery"]["message_id"]
    assert pokes == ["agy-seat"]
    assert wakes == [mid]
    tk.cmd_msg(_ns("same instruction", "agy-seat"), str(board))
    rec2 = json.loads((board / "agents" / "agy-seat.json").read_text())
    mid2 = rec2["wake_delivery"]["message_id"]
    # A new board row gets a new id and is delivered once; prior ids stay
    # idempotent so a retry cannot double-start the first turn.
    assert mid2 != mid
    assert tk._already_autonomous_wake(str(board), "agy-seat", mid2)
    assert tk._already_autonomous_wake(str(board), "agy-seat", mid)
    assert wakes == [mid, mid2]
    assert pokes == ["agy-seat", "agy-seat"]
    captured.clear()
    posted = {"id": mid, "to": "agy-seat", "from": "lead", "text": "same instruction",
              "at": "t", "re": ""}
    monkeypatch.setattr(tk, "post_message", lambda *a, **k: dict(posted))
    monkeypatch.setattr("builtins.print", lambda *a, **k: captured.append(" ".join(str(x) for x in a)))
    tk.cmd_msg(_ns("same instruction", "agy-seat"), str(board))
    assert any("deduped" in line for line in captured), captured
    assert wakes == [mid, mid2]
    assert pokes == ["agy-seat", "agy-seat"]


def test_task_only_ordinary_dm_stays_gated(board, monkeypatch):
    tk = _mod()
    poked = []
    monkeypatch.setattr(tk, "_poke_persist_watch", lambda board_arg, seat: poked.append(seat) or True)
    monkeypatch.setattr(tk, "_session_adapters", lambda: type("SA", (), {
        "wake_seat": staticmethod(lambda *a, **k: "queued-offline"),
        "wake_payload": staticmethod(lambda fmt, m: "payload"),
        "provider_for_harness": staticmethod(lambda h: h or ""),
    })())
    assert run(board, "join", "worker", "--roles", "backend", "--harness", "cursor",
               "--wake-mode", "task-only").returncode == 0
    captured = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: captured.append(" ".join(str(x) for x in a)))
    tk.cmd_msg(_ns("please look when convenient", "worker"), str(board))
    assert poked == []
    assert not any(line.startswith("wake:") for line in captured), captured
    rec = json.loads((board / "agents" / "worker.json").read_text())
    assert not rec.get("wake_delivery")
    tk.cmd_msg(_ns("run this now", "worker", task=True), str(board))
    assert poked == ["worker"]
    assert any("watch-poked" in line for line in captured), captured


def test_remote_bridge_still_skips_persist_poke(board, monkeypatch):
    tk = _mod()
    poked = []
    monkeypatch.setattr(tk, "_poke_persist_watch", lambda board_arg, seat: poked.append(seat) or True)
    monkeypatch.setattr(tk, "_session_adapters", lambda: type("SA", (), {
        "wake_seat": staticmethod(lambda *a, **k: "remote bridge required"),
        "wake_payload": staticmethod(lambda fmt, m: "payload"),
        "provider_for_harness": staticmethod(lambda h: h or ""),
    })())
    assert run(board, "join", "grok-remote", "--roles", "backend", "--harness", "remote",
               "--wake-mode", "continuous").returncode == 0
    captured = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: captured.append(" ".join(str(x) for x in a)))
    tk.cmd_msg(_ns("please act now", "grok-remote"), str(board))
    assert poked == []
    assert any("remote bridge required" in line for line in captured), captured


def test_live_persist_watch_survives_queued_offline_poke(board, monkeypatch):
    tk = _mod()
    monkeypatch.setattr(tk, "_session_adapters", lambda: type("SA", (), {
        "wake_seat": staticmethod(lambda *a, **k: "queued-offline"),
        "wake_payload": staticmethod(lambda fmt, m: "payload"),
        "provider_for_harness": staticmethod(lambda h: "codex"),
    })())
    seat = "persist-codex"
    assert run(board, "join", seat, "--roles", "docs", "--harness", "codex",
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
        r = run(board, "msg", "wake this persist loop", "--to", seat, agent="lead")
        assert r.returncode == 0, r.stderr + r.stdout
        assert "watch-poked" in r.stdout, r.stdout
        rec = json.loads((board / "agents" / (seat + ".json")).read_text())
        assert rec["wake_delivery"]["label"] == "watch-poked"
        cmdline = subprocess.run(
            ["ps", "-p", str(proc.pid), "-o", "command="],
            capture_output=True, text=True).stdout
        assert str(TOOL) in cmdline
    finally:
        try:
            os.kill(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
        collect_watch_pids_from_board(board)


def test_persist_poke_retries_then_gives_up():
    tk = _mod()
    assert tk.PERSIST_POKE_ATTEMPTS == 3
    assert tk._poke_persist_watch("/no-such-board", "nobody") is False
    assert tk._poke_persist_watch("/no-such-board", "nobody", attempts=1) is False
