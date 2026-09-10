"""T-706: pause→wake poke parity. Claude socket is gold; queue/poll is not a PASS."""

import os
import subprocess
import time
from pathlib import Path
from unittest import mock

from test_t683_session_adapters import (  # noqa: F401
    FakeInbox, _adapters, _run, board, cache_dir, sock_dir,
)


def test_codex_app_server_turn_start_is_woken(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    sa.write_endpoint(str(board), "ml-eng", {
        "seat": "ml-eng", "provider": "codex", "mode": "native",
        "thread": "thread-live", "pid": os.getpid(),
        "at": "now", "heartbeat_epoch": time.time(),
        "lease_id": "lease-1", "fence": 1})
    with mock.patch.object(sa, "_poke_codex_wake", return_value="woken"):
        label = sa.wake_seat(str(board), "ml-eng", "hello pause", harness="codex")
    assert label == "woken", label


def test_codex_queue_without_app_server_is_queued_offline(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    monkeypatch.delenv("CODEX_APP_SERVER_CONTROL_SOCK", raising=False)
    monkeypatch.delenv("CODEX_APP_SERVER_SOCKET", raising=False)
    sa = _adapters()
    sa.write_endpoint(str(board), "ml-eng", {
        "seat": "ml-eng", "provider": "codex", "mode": "native",
        "thread": "thread-abc", "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time()})
    with mock.patch.object(sa, "_codex_queue_start", return_value=False):
        with mock.patch.object(sa, "_codex_turn_start", return_value=False):
            with mock.patch("subprocess.run") as run_mock:
                run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
                label = sa.wake_seat(str(board), "ml-eng", "hello", harness="codex")
    assert label == "queued-offline", label
    assert run_mock.call_args[0][0][:4] == ["codex", "queue", "--thread", "thread-abc"]


def test_cursor_tmux_persist_is_woken(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    sa.write_endpoint(str(board), "grok-worker", {
        "seat": "grok-worker", "provider": "cursor", "mode": "native",
        "session_id": "chat-1", "persist_session": "persist-grok",
        "pid": os.getpid(), "at": "now", "heartbeat_epoch": time.time()})
    calls = []

    def fake_which(cmd):
        return "/usr/bin/tmux" if cmd == "tmux" else None

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with mock.patch.object(sa, "_which", side_effect=fake_which):
        with mock.patch("subprocess.run", side_effect=fake_run):
            label = sa.wake_seat(str(board), "grok-worker", "wake now", harness="cursor")
    assert label == "woken", label
    assert calls[0][:4] == ["tmux", "send-keys", "-t", "persist-grok"]
    assert calls[1] == ["tmux", "send-keys", "-t", "persist-grok", "Enter"]


def test_cursor_without_persist_is_not_native_pass(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    sa.write_endpoint(str(board), "cursor-seat", {
        "seat": "cursor-seat", "provider": "cursor", "mode": "supervised",
        "session_id": "chat-1", "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time()})
    with mock.patch("subprocess.run") as run_mock:
        label = sa.wake_seat(str(board), "cursor-seat", "hello", harness="cursor")
    assert label.startswith("supervised"), label
    run_mock.assert_not_called()


def test_remote_grok_still_fail_closed(board, cache_dir):
    sa = _adapters()
    label = sa.wake_seat(str(board), "grok-remote", "hello", harness="remote")
    assert label == "remote bridge required"


def test_claude_socket_regression(board, cache_dir, sock_dir):
    sock_path = str(Path(sock_dir) / "claude.sock")
    inbox = FakeInbox(sock_path)
    r = _run(board, "join", "claude-gold", "--roles", "backend", "--persistent",
             env={"TICKETS_CACHE_DIR": cache_dir,
                  "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
                  "CLAUDE_CODE_MESSAGING_TOKEN": "tok",
                  "TICKET_SESSION_PID": str(os.getpid())})
    assert r.returncode == 0, r.stderr
    sent = _run(board, "msg", "please act", "--to", "claude-gold", "--task",
                agent="sender", env={"TICKETS_CACHE_DIR": cache_dir})
    assert sent.returncode == 0, sent.stderr
    assert "wake: claude-gold -> woken" in sent.stdout
    payload = inbox.wait_for_message()
    inbox.close()
    assert payload and "please act" in payload
