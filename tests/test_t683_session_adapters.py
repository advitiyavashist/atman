"""T-683: provider-native persistent session adapters."""

import importlib.util
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest import mock

import pytest

from test_wakeup import board, pending, run  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
ADAPTERS = ROOT / "session_adapters.py"


def _adapters():
    spec = importlib.util.spec_from_file_location("session_adapters_t683", ADAPTERS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tickets():
    spec = importlib.util.spec_from_file_location("tickets_t683", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cache_dir(tmp_path):
    return str(tmp_path / "cache")


@pytest.fixture
def sock_dir():
    d = tempfile.mkdtemp(prefix="t683", dir="/tmp")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _run(board, *args, agent="", env=None, **kwargs):
    e = {"TICKETS_CACHE_DIR": env.get("TICKETS_CACHE_DIR") if env else None}
    e = {k: v for k, v in e.items() if v}
    if env:
        e.update(env)
    return run(board, *args, agent=agent, env=e, **kwargs)


class FakeInbox:
    def __init__(self, path):
        self.path = str(path)
        self.received = []
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(1)
        self._srv.settimeout(10)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            chunks = []
            try:
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
            except OSError:
                pass
        self.received.append(b"".join(chunks).decode("utf-8", "replace"))

    def wait_for_message(self, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.received:
                return self.received[-1]
            time.sleep(0.05)
        return None

    def close(self):
        try:
            self._srv.close()
        except OSError:
            pass


def test_endpoint_files_are_private_outside_board(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    sock_path = str(Path(sock_dir) / "alice.sock")
    token = "SEKRIT-TOKEN-DO-NOT-LEAK"
    sa.write_endpoint(str(board), "alice", {
        "seat": "alice", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": token, "pid": os.getpid(), "at": "now"})
    endpoint_files = list(Path(cache_dir).rglob("alice.json"))
    assert len(endpoint_files) == 1
    mode = stat.S_IMODE(endpoint_files[0].stat().st_mode)
    assert mode == 0o600
    for p in Path(board).rglob("*"):
        if p.is_file():
            assert token not in p.read_text(errors="ignore"), p


def test_claude_register_and_wake(board, cache_dir, sock_dir):
    sock_path = str(Path(sock_dir) / "bob.sock")
    inbox = FakeInbox(sock_path)
    r = _run(board, "join", "bob", "--roles", "backend", "--persistent",
             env={"TICKETS_CACHE_DIR": cache_dir,
                  "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
                  "CLAUDE_CODE_MESSAGING_TOKEN": "tok",
                  "TICKET_SESSION_PID": str(os.getpid())})
    assert r.returncode == 0, r.stderr
    assert "persistent: native claude endpoint registered" in r.stdout
    sent = _run(board, "msg", "please act", "--to", "bob", "--task", agent="sender",
                env={"TICKETS_CACHE_DIR": cache_dir})
    assert sent.returncode == 0, sent.stderr
    assert "wake: bob -> woken" in sent.stdout
    payload = inbox.wait_for_message()
    inbox.close()
    assert payload and "please act" in payload


def test_codex_queue_wake_uses_thread(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    sa.write_endpoint(str(board), "codex-seat", {
        "seat": "codex-seat", "provider": "codex", "mode": "native",
        "thread": "thread-abc", "pid": os.getpid(), "at": "now"})
    with mock.patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        label = sa.wake_seat(str(board), "codex-seat", "hello", harness="codex")
    assert label == "queued"
    args = run_mock.call_args[0][0]
    assert args[:4] == ["codex", "queue", "--thread", "thread-abc"]
    assert args[5] == "hello"


def test_cursor_resume_wake(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    sa.write_endpoint(str(board), "cursor-seat", {
        "seat": "cursor-seat", "provider": "cursor", "mode": "native",
        "session_id": "chat-123", "pid": os.getpid(), "at": "now"})
    with mock.patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        label = sa.wake_seat(str(board), "cursor-seat", "hello", harness="cursor")
    assert label == "woken"
    args = run_mock.call_args[0][0]
    assert "chat-123" in args


def test_remote_adapter_fails_closed_on_native_wake(board, cache_dir):
    sa = _adapters()
    label = sa.wake_seat(str(board), "grok-worker", "hello", harness="remote")
    assert label == "remote bridge required"


def test_stale_endpoint_removed(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    dead_sock = str(Path(sock_dir) / "dead.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(dead_sock)
    s.close()
    sa.write_endpoint(str(board), "stale", {
        "seat": "stale", "provider": "claude", "mode": "native",
        "socket": dead_sock, "token": "", "pid": 99999999, "at": "now"})
    ep, was_stale = sa.live_endpoint(str(board), "stale")
    assert ep is None and was_stale
    assert sa.read_endpoint(str(board), "stale") is None


def test_spawn_skips_live_native_session(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    sock_path = str(Path(sock_dir) / "live.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(sock_path)
    s.listen(1)
    sa.write_endpoint(str(board), "native-seat", {
        "seat": "native-seat", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "", "pid": os.getpid(), "at": "now"})
    _run(board, "join", "native-seat", "--roles", "backend", "--harness", "claude")
    r = _run(board, "spawn", "native-seat", env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr
    assert "skip: native-seat has a live native session" in r.stdout
    s.close()


def test_watch_skips_live_native_session(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    sock_path = str(Path(sock_dir) / "watch.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(sock_path)
    s.listen(1)
    sa.write_endpoint(str(board), "watch-seat", {
        "seat": "watch-seat", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "", "pid": os.getpid(), "at": "now"})
    _run(board, "join", "watch-seat", "--roles", "backend", "--harness", "claude")
    r = _run(board, "watch", "--agent", "watch-seat", "--once",
             env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr
    assert "skip: watch-seat has a live native session" in r.stdout
    s.close()


def test_continuous_dm_wakes_native_endpoint(board, cache_dir, sock_dir):
    sock_path = str(Path(sock_dir) / "cont.sock")
    inbox = FakeInbox(sock_path)
    _run(board, "join", "seat", "--roles", "backend", "--wake-mode", "continuous",
         env={"TICKETS_CACHE_DIR": cache_dir,
              "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
              "CLAUDE_CODE_MESSAGING_TOKEN": "tok",
              "TICKET_SESSION_PID": str(os.getpid())},
         agent="seat")
    _run(board, "join", "seat", "--roles", "backend", "--wake-mode", "continuous",
         "--persistent",
         env={"TICKETS_CACHE_DIR": cache_dir,
              "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
              "CLAUDE_CODE_MESSAGING_TOKEN": "tok",
              "TICKET_SESSION_PID": str(os.getpid())},
         agent="seat")
    time.sleep(1.1)
    sent = _run(board, "msg", "continuous ping", "--to", "seat", agent="sender",
                env={"TICKETS_CACHE_DIR": cache_dir})
    assert sent.returncode == 0, sent.stderr
    assert "wake: seat -> woken" in sent.stdout
    inbox.close()


def test_probe_fail_closed_without_transport(board, cache_dir):
    sa = _adapters()
    with mock.patch.dict(os.environ, {}, clear=True):
        probe = sa.probe_provider("claude")
    assert not probe.get("ok")
    reg = sa.register_persistent(str(board), "alice", "claude", "now")
    assert not reg.get("ok")


def test_public_state_redacts_token(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    sa.write_endpoint(str(board), "alice", {
        "seat": "alice", "provider": "claude", "mode": "native",
        "socket": "/tmp/x.sock", "token": "secret", "pid": os.getpid(), "at": "now"})
    red = sa.redact_endpoint(sa.read_endpoint(str(board), "alice"))
    assert red["token"] == "<redacted>"


@pytest.mark.parametrize("provider", ["claude", "codex", "cursor", "remote"])
def test_adapter_contract_probe_shape(provider):
    sa = _adapters()
    with mock.patch.object(sa, "_which", return_value="/bin/fake"):
        with mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess([], 0, "--resume", "")
            with mock.patch.dict(os.environ, {
                "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/x.sock",
                "CODEX_THREAD_ID": "t1",
                "CURSOR_CONVERSATION_ID": "c1",
            }, clear=False):
                probe = sa.probe_provider(provider)
    if provider == "remote":
        assert probe.get("ok")
        assert probe["capabilities"]["native_inject"] is False
    else:
        assert "capabilities" in probe or "reason" in probe
