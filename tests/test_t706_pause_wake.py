"""T-706: pause→wake poke parity. Claude socket is gold; queue/poll is not a PASS."""

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from unittest import mock

from test_t683_session_adapters import (  # noqa: F401
    FakeInbox, _adapters, _run, board, cache_dir, sock_dir,
)


class FakeAcp:
    """Newline JSON-RPC ACP control sock. Accepts many short connections."""

    def __init__(self, path):
        self.path = str(path)
        self.requests = []
        self._stop = threading.Event()
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(8)
        self._srv.settimeout(0.2)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                buf = b""
                try:
                    while b"\n" not in buf:
                        data = conn.recv(4096)
                        if not data:
                            break
                        buf += data
                except OSError:
                    continue
                line = buf.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except ValueError:
                    continue
                self.requests.append(req)
                rid = req.get("id", 1)
                result = {"sessionId": (req.get("params") or {}).get("sessionId")}
                if req.get("method") == "session/prompt":
                    result = {"stopReason": "end_turn"}
                conn.sendall((json.dumps({"jsonrpc": "2.0", "id": rid,
                                          "result": result}) + "\n").encode("utf-8"))

    def close(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass



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
    monkeypatch.delenv("CURSOR_ACP_CONTROL_SOCK", raising=False)
    sa = _adapters()
    sa.write_endpoint(str(board), "cursor-seat", {
        "seat": "cursor-seat", "provider": "cursor", "mode": "supervised",
        "session_id": "chat-1", "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time()})
    with mock.patch("subprocess.run") as run_mock:
        label = sa.wake_seat(str(board), "cursor-seat", "hello", harness="cursor")
    assert label.startswith("supervised"), label
    run_mock.assert_not_called()


def test_cursor_acp_session_prompt_is_woken(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(Path(sock_dir) / "cursor-acp.sock")
    monkeypatch.setenv("CURSOR_ACP_CONTROL_SOCK", sock_path)
    inbox = FakeAcp(sock_path)
    sa = _adapters()
    sa.write_endpoint(str(board), "cursor-native-wake", {
        "seat": "cursor-native-wake", "provider": "cursor", "mode": "native",
        "session_id": "6312ec1d-e48d-4649-9712-1314accc0d21",
        "socket": sock_path, "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time(), "lease_id": "lease-c", "fence": 1})
    label = sa.wake_seat(str(board), "cursor-native-wake", "pause resume now",
                          harness="cursor")
    assert label == "woken", label
    methods = [m.get("method") for m in inbox.requests]
    assert "session/load" in methods
    assert "session/prompt" in methods
    prompt = next(m for m in inbox.requests if m.get("method") == "session/prompt")
    assert prompt["params"]["sessionId"] == "6312ec1d-e48d-4649-9712-1314accc0d21"
    inbox.close()


def test_cursor_conversation_id_join_without_transport_is_supervised(
        board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    monkeypatch.setenv("CURSOR_CONVERSATION_ID", "chat-interactive")
    monkeypatch.delenv("CURSOR_PERSIST_SESSION", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("CURSOR_ACP_CONTROL_SOCK", str(Path(cache_dir) / "missing.sock"))
    sa = _adapters()
    def fake_which(cmd):
        return "/bin/agent" if cmd == "agent" else None

    with mock.patch.object(sa, "_which", side_effect=fake_which):
        with mock.patch("subprocess.run") as help_mock:
            help_mock.return_value = subprocess.CompletedProcess([], 0, "--resume", "")
            reg = sa.register_persistent(str(board), "cursor-native-wake", "cursor", "now")
    assert reg.get("ok"), reg
    assert reg.get("mode") == "supervised"
    assert reg["record"]["capabilities"]["native_inject"] is False


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
