"""T-1107: a scratch board must not poke a session bound to another board."""
import json
import os

import session_adapters as sa
from test_wakeup import board  # noqa: F401


def _board(tmp_path, name):
    repo = tmp_path / name
    repo.mkdir()
    tickets = repo / ".tickets"
    tickets.mkdir()
    (tickets / "agents").mkdir()
    return str(tickets)


def _record(board, sock, at="2026-09-19T08:00:00Z"):
    return {
        "seat": "lead",
        "provider": "claude",
        "socket": sock,
        "mode": "native",
        "capabilities": {"native_inject": True},
        "board": os.path.abspath(board),
        "at": at,
    }


def test_wake_on_board_a_never_reaches_board_b_session(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("TICKETS_CACHE_DIR", str(cache))
    sock = tmp_path / "live.sock"
    sock.write_text("")
    poked = []
    monkeypatch.setattr(sa, "_poke_claude_until", lambda ep, text, attempts=None: poked.append(text) or "woken")

    board_b = _board(tmp_path, "live-board")
    board_a = _board(tmp_path, "scratch-board")
    rec = _record(board_b, str(sock))
    assert sa.commit_endpoint(board_b, "lead", dict(rec))["ok"]

    stolen = sa.commit_endpoint(board_a, "lead", _record(board_a, str(sock)))
    assert stolen.get("ok") is False
    assert "another board" in (stolen.get("reason") or "")

    # Old leak shape: scratch already has a copy of the live socket.
    sa.write_endpoint(board_a, "lead", _record(board_a, str(sock), at="2026-09-19T09:00:00Z"))
    label = sa.wake_seat(board_a, "lead", "scratch must not land", harness="claude")
    assert "another board" in label
    assert poked == []

    label_b = sa.wake_seat(board_b, "lead", "live board may wake", harness="claude")
    assert label_b == "woken"
    assert poked == ["live board may wake"]


def test_wakeup_run_scrubs_parent_messaging_socket(board, tmp_path, monkeypatch):
    from test_wakeup import run
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/parent/live.sock")
    monkeypatch.setenv("TICKETS_CACHE_DIR", str(tmp_path / "cache"))
    run(board, "join", "lead", "--persistent", "--wake-mode", "continuous",
        agent="lead")
    ep = sa.read_endpoint(str(board), "lead")
    sock = (ep or {}).get("socket") or ""
    assert sock != "/parent/live.sock"
