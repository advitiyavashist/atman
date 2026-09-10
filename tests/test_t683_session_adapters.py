"""T-683: provider-native persistent session adapters."""

import importlib.util
import json
import os
import shutil
import signal
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



def test_codex_app_server_start_is_woken(board, cache_dir, monkeypatch, tmp_path):
    """Managed app-server queue/start elevates Codex poke to woken (Claude bar)."""
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock = tmp_path / "app-server-control.sock"
    sock.write_text("")  # exists check only; RPC is mocked
    monkeypatch.setenv("CODEX_APP_SERVER_CONTROL_SOCK", str(sock))
    sa = _adapters()
    sa.write_endpoint(str(board), "codex-seat", {
        "seat": "codex-seat", "provider": "codex", "mode": "native",
        "thread": "thread-live", "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time()})
    with mock.patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(sa, "_codex_queue_start", return_value=True):
            label = sa.wake_seat(str(board), "codex-seat", "hello", harness="codex")
    assert label == "woken"
    assert sa.has_live_native_session(str(board), "codex-seat") is True


def test_codex_retained_without_control_sock_does_not_block_watch(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    monkeypatch.delenv("CODEX_APP_SERVER_CONTROL_SOCK", raising=False)
    sa = _adapters()
    sa.write_endpoint(str(board), "codex-seat", {
        "seat": "codex-seat", "provider": "codex", "mode": "native",
        "thread": "thread-old", "pid": None, "at": "now", "heartbeat_epoch": 1})
    assert sa.has_live_native_session(str(board), "codex-seat") is False


def test_codex_queue_wake_uses_thread(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    sa.write_endpoint(str(board), "codex-seat", {
        "seat": "codex-seat", "provider": "codex", "mode": "native",
        "thread": "thread-abc", "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time()})
    with mock.patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        label = sa.wake_seat(str(board), "codex-seat", "hello", harness="codex")
    # Without app-server control sock, sqlite enqueue is queued-offline not woken.
    assert label == "queued-offline"
    args = run_mock.call_args[0][0]
    assert args[:4] == ["codex", "queue", "--thread", "thread-abc"]
    assert args[5] == "hello"


def test_cursor_resume_is_not_native_enqueue(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    sa.write_endpoint(str(board), "cursor-seat", {
        "seat": "cursor-seat", "provider": "cursor", "mode": "native",
        "session_id": "chat-123", "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time()})
    with mock.patch("subprocess.run") as run_mock:
        label = sa.wake_seat(str(board), "cursor-seat", "hello", harness="cursor")
    assert "supervised" in label
    run_mock.assert_not_called()
    with mock.patch.object(sa, "_which", return_value="/bin/agent"):
        with mock.patch("subprocess.run") as help_mock:
            help_mock.return_value = subprocess.CompletedProcess([], 0, "--resume", "")
            probe = sa.probe_provider("cursor")
    assert probe.get("ok")
    assert probe["capabilities"]["native_inject"] is False


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


def test_two_same_provider_persistent_sessions_stay_isolated(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    alice_sock = str(Path(sock_dir) / "alice.sock")
    bob_sock = str(Path(sock_dir) / "bob.sock")
    alice_inbox = FakeInbox(alice_sock)
    bob_inbox = FakeInbox(bob_sock)
    for name, sock in (("alice", alice_sock), ("bob", bob_sock)):
        r = _run(board, "join", name, "--roles", "backend", "--persistent",
                  "--wake-mode", "continuous",
                  env={"TICKETS_CACHE_DIR": cache_dir,
                       "CLAUDE_CODE_MESSAGING_SOCKET": sock,
                       "TICKET_SESSION_PID": str(os.getpid())},
                  agent=name)
        assert r.returncode == 0, r.stderr
        assert "lifecycle=persistent" in r.stdout
    sa = _adapters()
    assert sa.read_endpoint(str(board), "alice")["socket"] == alice_sock
    assert sa.read_endpoint(str(board), "bob")["socket"] == bob_sock
    time.sleep(1.1)
    sent = _run(board, "msg", "only-alice", "--to", "alice", agent="sender",
                 env={"TICKETS_CACHE_DIR": cache_dir})
    assert sent.returncode == 0, sent.stderr
    payload = alice_inbox.wait_for_message()
    alice_inbox.close()
    bob_inbox.close()
    assert payload and "only-alice" in payload
    assert not bob_inbox.received


def test_session_bind_refuses_identity_crosswire(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(Path(sock_dir) / "shared.sock")
    sa = _adapters()
    sa.write_endpoint(str(board), "alice", {
        "seat": "alice", "agent_id": "alice", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "", "pid": os.getpid(), "at": "now"})
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", sock_path)
    monkeypatch.setenv("TICKET_SESSION_PID", str(os.getpid()))
    reg = sa.register_persistent(str(board), "mallory", "claude", "now")
    assert not reg.get("ok")
    assert "alice" in reg.get("reason", "")
    assert sa.read_endpoint(str(board), "mallory") is None


def test_rebind_replaces_same_seat_session(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    first = str(Path(sock_dir) / "old.sock")
    second = str(Path(sock_dir) / "new.sock")
    Path(first).touch()
    Path(second).touch()
    monkeypatch.setenv("TICKET_SESSION_PID", str(os.getpid()))
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", first)
    sa = _adapters()
    first_reg = sa.register_persistent(str(board), "alice", "claude", "t1")
    assert first_reg.get("ok")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", second)
    refused = sa.register_persistent(str(board), "alice", "claude", "t2")
    assert not refused.get("ok")
    assert "active lease" in refused.get("reason", "")
    monkeypatch.setenv("TICKETS_SESSION_LEASE", first_reg["lease_id"])
    assert sa.register_persistent(str(board), "alice", "claude", "t3").get("ok")
    ep = sa.read_endpoint(str(board), "alice")
    assert ep["socket"] == second
    assert ep["agent_id"] == "alice"
    assert int(ep.get("fence") or 0) >= 2
    assert ep.get("lease_id")
    assert ep.get("prev_lease_id") == first_reg["lease_id"]


def test_persistent_task_only_does_not_poke_ordinary_dm(board, cache_dir, sock_dir):
    sock_path = str(Path(sock_dir) / "task.sock")
    inbox = FakeInbox(sock_path)
    r = _run(board, "join", "seat", "--roles", "backend", "--persistent",
              "--wake-mode", "task-only",
              env={"TICKETS_CACHE_DIR": cache_dir,
                   "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
                   "TICKET_SESSION_PID": str(os.getpid())})
    assert r.returncode == 0, r.stderr
    time.sleep(1.1)
    quiet = _run(board, "msg", "do not spend a turn", "--to", "seat", agent="sender",
                  env={"TICKETS_CACHE_DIR": cache_dir})
    assert quiet.returncode == 0, quiet.stderr
    assert "wake:" not in quiet.stdout
    tasked = _run(board, "msg", "spend this turn", "--to", "seat", "--task", agent="sender",
                  env={"TICKETS_CACHE_DIR": cache_dir})
    assert tasked.returncode == 0, tasked.stderr
    assert "wake: seat -> woken" in tasked.stdout
    payload = inbox.wait_for_message()
    inbox.close()
    assert payload and "spend this turn" in payload


def test_ephemeral_teardown_is_not_reachable(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    _run(board, "join", "temp", "--roles", "backend", "--lifecycle", "ephemeral",
         env={"TICKETS_CACHE_DIR": cache_dir})
    r = _run(board, "watch", "--agent", "temp", "--once",
              env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode in (0, 1), r.stderr
    snap = _tickets().board_snapshot(str(board))
    row = next(a for a in snap["agents"] if a["name"] == "temp")
    assert row["lifecycle"] == "ephemeral"
    assert row["reachable"] is False
    assert row["adapter_delivery"] == "exited"


def test_lifecycle_defaults_and_explicit_override(board):
    tk = _tickets()
    _run(board, "join", "master", "--roles", "leadership", agent="master")
    _run(board, "join", "cos", "--roles", "leadership", agent="cos")
    _run(board, "master", "take", agent="master")
    _run(board, "master", "cos", "cos", agent="master")
    _run(board, "join", "worker", "--roles", "backend")
    assert tk.lifecycle_of(str(board), "worker") == "ephemeral"
    assert tk.lifecycle_of(str(board), "master") == "persistent"
    assert tk.lifecycle_of(str(board), "cos") == "persistent"
    _run(board, "join", "master", "--roles", "leadership", "--lifecycle", "ephemeral",
         "--wake-mode", "continuous", agent="master")
    assert tk.lifecycle_of(str(board), "master") == "ephemeral"
    assert tk.wake_mode_of(str(board), "master") == "continuous"


def test_board_snapshot_lifecycle_badge_fields(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(Path(sock_dir) / "ui.sock")
    Path(sock_path).touch()
    _run(board, "join", "seat", "--roles", "backend", "--persistent",
         "--wake-mode", "task-only",
         env={"TICKETS_CACHE_DIR": cache_dir,
              "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
              "TICKET_SESSION_PID": str(os.getpid())})
    snap = _tickets().board_snapshot(str(board))
    row = next(a for a in snap["agents"] if a["name"] == "seat")
    assert row["agent_id"] == "seat"
    assert row["lifecycle"] == "persistent"
    assert row["wake_mode"] == "task-only"
    assert row["adapter_provider"] == "claude"
    assert row["adapter_mode"] == "native"
    assert row["adapter_usage"] == "unmeasured"
    assert row["reachable"] is True
    assert row["adapter_native_online"] is True
    assert row["adapter_state"] == "online"
    assert "lifecycle" in tk_html()


def tk_html():
    src = (ROOT / "tickets.py").read_text()
    start = src.index('UI_HTML = r"""')
    return src[start:start + 80000]


@pytest.mark.skipif(not shutil.which("codex"), reason="codex CLI not installed")
def test_installed_codex_queue_help_smoke():
    r = subprocess.run(["codex", "queue", "--help"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not shutil.which("agent"), reason="cursor agent CLI not installed")
def test_installed_cursor_resume_help_smoke():
    r = subprocess.run(["agent", "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "--resume" in (r.stdout + r.stderr)


def test_persistent_offline_is_not_reachable(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    r = _run(board, "join", "boss", "--roles", "leadership", "--lifecycle", "persistent",
             "--wake-mode", "task-only",
             env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr
    snap = _tickets().board_snapshot(str(board))
    row = next(a for a in snap["agents"] if a["name"] == "boss")
    assert row["lifecycle"] == "persistent"
    assert row["reachable"] is False
    assert row["adapter_native_online"] is False
    assert row["adapter_state"] in ("offline", "queued-offline")


def test_pidless_codex_endpoint_expires_without_heartbeat(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    sa.write_endpoint(str(board), "codex-seat", {
        "seat": "codex-seat", "provider": "codex", "mode": "native",
        "thread": "thread-old", "pid": None, "at": "now", "heartbeat_epoch": 1})
    ep, was_stale = sa.live_endpoint(str(board), "codex-seat")
    assert ep is None and was_stale
    retained = sa.read_endpoint(str(board), "codex-seat")
    assert retained is not None and retained.get("thread") == "thread-old"
    assert sa.public_adapter_state(str(board), "codex-seat", "codex", False, False)[
        "adapter_native_online"] is False
    # Retained thread identity must not suppress watch: queue≠pause→resume.
    assert sa.has_live_native_session(str(board), "codex-seat") is False


def test_wake_delivery_is_deduped_by_message_id(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(Path(sock_dir) / "dedupe.sock")
    inbox = FakeInbox(sock_path)
    sa = _adapters()
    sa.write_endpoint(str(board), "bob", {
        "seat": "bob", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "", "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time()})
    assert sa.wake_seat(str(board), "bob", "hello", message_id="m1") == "woken"
    assert sa.wake_seat(str(board), "bob", "hello", message_id="m1") == "deduped"
    inbox.close()


def test_held_explicit_task_dominates_cos_prompt(board):
    tk = _tickets()
    _run(board, "join", "planner", "--roles", "leadership", agent="planner")
    _run(board, "join", "grok-worker", "--roles", "docs,review,verification", agent="grok-worker")
    _run(board, "master", "take", agent="planner")
    _run(board, "master", "cos", "grok-worker", agent="planner")
    _run(board, "next", agent="grok-worker")
    sent = _run(board, "msg", "prove the live Grok CoS identity wake", "--to", "grok-worker",
                "--task", "--re", "T-001", agent="planner")
    assert sent.returncode == 0, sent.stderr
    ns = type("A", (), {"agent": "grok-worker", "master": False, "cos": True, "extra": ""})()
    text = tk.prompt_text(ns, str(board))
    assert text.startswith("HELD WORK DOMINATES THIS RUN.")
    assert "T-001" in text
    assert "prove the live Grok CoS identity wake" in text
    assert text.index("HELD WORK") < text.index("REVIEW + MERGE")


def test_spawn_stop_clears_dead_active_run(board):
    tk = _tickets()
    _run(board, "join", "runner", "--roles", "backend")
    tk._run_beat(str(board), "runner", pid=99999999, run=1, active=True, started="now")
    rec = tk._read_run(str(board), "runner")
    assert rec.get("active") is True
    r = _run(board, "spawn", "runner", "--stop")
    assert r.returncode == 0, r.stderr
    rec = tk._read_run(str(board), "runner")
    assert rec.get("active") is False
    assert rec.get("interrupted") is True


def test_codex_persistent_ceo_stub_queue_shape(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-ceo")
    monkeypatch.setenv("TICKET_SESSION_PID", str(os.getpid()))
    with mock.patch.object(sa, "_which", return_value="/bin/codex"):
        with mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess([], 0, "queue", "")
            reg = sa.register_persistent(str(board), "codex-ceo", "codex", "now")
    assert reg.get("ok"), reg
    assert reg.get("mode") == "native"
    with mock.patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        label = sa.wake_seat(str(board), "codex-ceo", "hello")
    assert label == "queued-offline"
    args = run_mock.call_args[0][0]
    assert args[:4] == ["codex", "queue", "--thread", "thread-ceo"]


def test_harness_mismatch_does_not_cross_poke(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(Path(sock_dir) / "grok.sock")
    inbox = FakeInbox(sock_path)
    sa = _adapters()
    sa.write_endpoint(str(board), "grok-worker", {
        "seat": "grok-worker", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "", "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time()})
    label = sa.wake_seat(str(board), "grok-worker", "hello", harness="remote")
    assert label == "remote bridge required"
    assert sa.read_endpoint(str(board), "grok-worker") is None
    sa.write_endpoint(str(board), "grok-worker", {
        "seat": "grok-worker", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "", "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time()})
    label = sa.wake_seat(str(board), "grok-worker", "hello", harness="codex")
    assert "refused" in label
    assert "removed" in label
    assert sa.read_endpoint(str(board), "grok-worker") is None
    inbox.close()
    assert not inbox.received


def test_stale_same_seat_rebind_does_not_need_lease(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    dead = str(Path(sock_dir) / "dead.sock")
    alive = str(Path(sock_dir) / "alive.sock")
    Path(dead).touch()
    Path(alive).touch()
    sa = _adapters()
    sa.write_endpoint(str(board), "alice", {
        "seat": "alice", "provider": "claude", "mode": "native",
        "socket": dead, "token": "", "pid": 99999999, "at": "now",
        "lease_id": "old-lease", "fence": 1, "heartbeat_epoch": time.time()})
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", alive)
    monkeypatch.setenv("TICKET_SESSION_PID", str(os.getpid()))
    monkeypatch.delenv("TICKETS_SESSION_LEASE", raising=False)
    reg = sa.register_persistent(str(board), "alice", "claude", "now")
    assert reg.get("ok"), reg
    assert sa.read_endpoint(str(board), "alice")["socket"] == alive


def test_sigterm_finalizes_active_watch_run(board):
    tk = _tickets()
    _run(board, "join", "runner", "--roles", "backend")
    tk._run_begin(str(board), "runner", 1, str(board.parent))
    assert tk._read_run(str(board), "runner").get("active") is True
    assert tk._finalize_active_watch_run(str(board), "runner") is True
    rec = tk._read_run(str(board), "runner")
    assert rec.get("active") is False
    assert rec.get("interrupted") is True
    assert rec.get("rc") == 143
    assert tk._finalize_active_watch_run(str(board), "runner") is False


def test_late_heartbeat_cannot_resurrect_stopped_run_without_live_watcher(board):
    """spawn --stop with no live watcher must stay closed if a dead beat lands."""
    tk = _tickets()
    _run(board, "join", "runner", "--roles", "backend")
    tk._run_begin(str(board), "runner", 11, str(board.parent), run_id="r-11")
    r = _run(board, "spawn", "runner", "--stop")
    assert r.returncode == 0, r.stderr
    assert "no running watcher" in (r.stdout + r.stderr)
    rec = tk._read_run(str(board), "runner")
    assert rec.get("active") is False
    assert rec.get("interrupted") is True
    closed_gen = rec.get("generation")
    tk._run_beat(str(board), "runner", pid=os.getpid(), run=11,
                 cwd=str(board.parent), active=True, interrupted=True)
    rec = tk._read_run(str(board), "runner")
    assert rec.get("active") is False
    assert rec.get("interrupted") is True
    assert rec.get("run") == 11
    assert rec.get("generation") == closed_gen
    tk._run_begin(str(board), "runner", 12, str(board.parent), run_id="r-12")
    rec = tk._read_run(str(board), "runner")
    assert rec.get("active") is True
    assert rec.get("run") == 12
    assert rec.get("interrupted") is False
    assert rec.get("generation") == closed_gen + 1


def test_stop_serializes_with_cross_process_late_heartbeat(board, tmp_path):
    """A stop cannot be overwritten after another process reads old state."""
    tk = _tickets()
    _run(board, "join", "runner", "--roles", "backend")
    tk._run_begin(str(board), "runner", 11, str(board.parent), run_id="r-11")
    heartbeat_read = tmp_path / "heartbeat-read"
    heartbeat_release = tmp_path / "heartbeat-release"
    stop_lock_attempt = tmp_path / "stop-lock-attempt"

    heartbeat_code = r"""
import importlib.util, os, sys, time
tool, board, read_marker, release_marker = sys.argv[1:]
spec = importlib.util.spec_from_file_location("tickets_heartbeat_child", tool)
tk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tk)
original_read = tk._read_run
def paused_read(board_arg, owner):
    record = original_read(board_arg, owner)
    open(read_marker, "w").write(str(record.get("generation")))
    deadline = time.time() + 10
    while not os.path.exists(release_marker):
        if time.time() >= deadline:
            raise RuntimeError("heartbeat release timed out")
        time.sleep(0.01)
    return record
tk._read_run = paused_read
tk._run_beat(board, "runner", pid=os.getpid(), run=11,
             cwd=os.path.dirname(board), active=True, interrupted=True)
"""
    stop_code = r"""
import fcntl, importlib.util, os, sys
tool, board, attempt_marker = sys.argv[1:]
original_flock = fcntl.flock
def marked_flock(fd, operation):
    if operation & fcntl.LOCK_EX:
        open(attempt_marker, "w").write("attempted")
    return original_flock(fd, operation)
fcntl.flock = marked_flock
spec = importlib.util.spec_from_file_location("tickets_stop_child", tool)
tk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tk)
tk._mark_run_interrupted(board, "runner")
"""

    heartbeat = subprocess.Popen(
        [sys.executable, "-c", heartbeat_code, str(TOOL), str(board),
         str(heartbeat_read), str(heartbeat_release)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stop = None
    try:
        deadline = time.time() + 10
        while not heartbeat_read.exists() and heartbeat.poll() is None and time.time() < deadline:
            time.sleep(0.01)
        assert heartbeat_read.exists(), heartbeat.communicate(timeout=1)

        stop = subprocess.Popen(
            [sys.executable, "-c", stop_code, str(TOOL), str(board),
             str(stop_lock_attempt)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 10
        while not stop_lock_attempt.exists() and stop.poll() is None and time.time() < deadline:
            time.sleep(0.01)
        assert stop_lock_attempt.exists(), stop.communicate(timeout=1)
        assert stop.poll() is None, "stop crossed the heartbeat transaction without waiting"

        heartbeat_release.touch()
        hb_out, hb_err = heartbeat.communicate(timeout=10)
        stop_out, stop_err = stop.communicate(timeout=10)
        assert heartbeat.returncode == 0, hb_out + hb_err
        assert stop.returncode == 0, stop_out + stop_err
    finally:
        heartbeat_release.touch(exist_ok=True)
        for proc in (heartbeat, stop):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    rec = tk._read_run(str(board), "runner")
    assert rec.get("generation") == 2
    assert rec.get("active") is False
    assert rec.get("interrupted") is True


def test_staged_release_ships_session_adapters(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "install_live_t683", ROOT / "scripts/install_live.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    repo = tmp_path / "source"
    repo.mkdir()
    installer.seed_fixture_repo(repo, ROOT)
    env = dict(installer.clean_env(), GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="t@t")

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], env=env).decode().strip()

    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "fixture release")
    sha = git("rev-parse", "HEAD")
    live = tmp_path / "tools/tickets.py"
    release = installer.install(repo, sha, live, activate=False)
    assert (release / "session_adapters.py").is_file()
    assert "session_adapters.py" in json.loads((release / "release.json").read_text())["files"]
    board = tmp_path / "board"
    board.mkdir()
    for name, content in (("tickets.json", "[]"), ("sprints.json", "[]"),
                          ("roles.json", "{}"), ("agents.json", "[]")):
        (board / name).write_text(content)
    arbitrary = tmp_path / "elsewhere"
    arbitrary.mkdir()
    env = dict(installer.clean_env(), TICKETS_DIR=str(board), HOME=str(tmp_path),
               TICKET_AGENT="installer", PYTHONNOUSERSITE="1")
    env.pop("PYTHONPATH", None)
    r = subprocess.run(
        [sys.executable, "-S", str(release / "tickets.py"),
         "join", "persist-seat", "--roles", "backend", "--persistent"],
        cwd=str(arbitrary), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "ModuleNotFoundError" not in r.stderr
    assert "joined as persist-seat" in r.stdout


def test_two_concurrent_register_cannot_both_win_fence_one(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    monkeypatch.delenv("TICKETS_SESSION_LEASE", raising=False)
    sock_a = str(Path(sock_dir) / "a.sock")
    sock_b = str(Path(sock_dir) / "b.sock")
    Path(sock_a).touch()
    Path(sock_b).touch()
    sa = _adapters()
    results = []

    def one(sock):
        record = {
            "seat": "alice", "agent_id": "alice", "provider": "claude", "mode": "native",
            "socket": sock, "token": "", "pid": os.getpid(), "at": "now",
        }
        results.append(sa.commit_endpoint(str(board), "alice", record))

    t1 = threading.Thread(target=one, args=(sock_a,))
    t2 = threading.Thread(target=one, args=(sock_b,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    oks = [r for r in results if r.get("ok")]
    fences = [int(r["fence"]) for r in oks]
    assert len(oks) == 1, results
    assert fences == [1]
    ep = sa.read_endpoint(str(board), "alice")
    assert int(ep.get("fence") or 0) == 1


def test_stale_delivery_after_rebind_does_not_mark_new_lease(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    first = str(Path(sock_dir) / "old.sock")
    second = str(Path(sock_dir) / "new.sock")
    Path(first).touch()
    Path(second).touch()
    monkeypatch.setenv("TICKET_SESSION_PID", str(os.getpid()))
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", first)
    monkeypatch.delenv("TICKETS_SESSION_LEASE", raising=False)
    sa = _adapters()
    first_reg = sa.register_persistent(str(board), "alice", "claude", "t1")
    assert first_reg.get("ok"), first_reg
    old_lease = first_reg["lease_id"]
    old_fence = int(first_reg["record"]["fence"])

    def poke_and_rebind(ep, text):
        monkeypatch.setenv("TICKETS_SESSION_LEASE", old_lease)
        monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", second)
        assert sa.register_persistent(str(board), "alice", "claude", "t2").get("ok")
        return True

    with mock.patch.object(sa, "_poke_claude", side_effect=poke_and_rebind):
        label = sa.wake_seat(str(board), "alice", "hello", harness="claude", message_id="old-msg")
    assert "stale" in label
    tk = _tickets()
    _run(board, "join", "alice", "--roles", "backend")
    tk._note_native_wake_result(str(board), "alice", label, "old-msg")
    ep = sa.read_endpoint(str(board), "alice")
    assert ep["socket"] == second
    assert ep.get("last_delivery_id") != "old-msg"
    assert ep.get("lease_id") != old_lease


def test_concurrent_cross_seat_same_session_only_one_binds(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock = str(Path(sock_dir) / "shared.sock")
    Path(sock).touch()
    sa = _adapters()
    results = []

    def one(seat):
        record = {
            "seat": seat, "agent_id": seat, "provider": "claude", "mode": "native",
            "socket": sock, "token": "", "pid": os.getpid(), "at": "now",
        }
        results.append((seat, sa.commit_endpoint(str(board), seat, record)))

    t1 = threading.Thread(target=one, args=("alice",))
    t2 = threading.Thread(target=one, args=("mallory",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    oks = [r for seat, r in results if r.get("ok")]
    assert len(oks) == 1, results
    bound = [seat for seat, r in results if r.get("ok")][0]
    other = "mallory" if bound == "alice" else "alice"
    assert sa.read_endpoint(str(board), bound)["socket"] == sock
    assert sa.read_endpoint(str(board), other) is None


def test_wake_retries_same_message_then_succeeds(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(Path(sock_dir) / "retry.sock")
    Path(sock_path).touch()
    sa = _adapters()
    sa.write_endpoint(str(board), "bob", {
        "seat": "bob", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-1", "fence": 1, "heartbeat_epoch": time.time()})
    with mock.patch.object(sa, "_poke_claude", side_effect=[False, True]) as poke:
        label = sa.wake_seat(str(board), "bob", "hello", harness="claude", message_id="durable-1")
    assert label == "woken"
    assert poke.call_count == 2
    assert sa.read_endpoint(str(board), "bob")["last_delivery_id"] == "durable-1"


def test_wake_exhausts_same_message_without_claiming_retrying(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(Path(sock_dir) / "exh.sock")
    Path(sock_path).touch()
    sa = _adapters()
    sa.write_endpoint(str(board), "bob", {
        "seat": "bob", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-1", "fence": 1, "heartbeat_epoch": time.time()})
    _run(board, "join", "bob", "--roles", "backend")
    with mock.patch.object(sa, "_poke_claude", return_value=False) as poke:
        label = sa.wake_seat(str(board), "bob", "hello", harness="claude", message_id="durable-1")
    assert label == "refused"
    assert poke.call_count == 3
    tk = _tickets()
    tk._note_native_wake_result(str(board), "bob", label, "durable-1")
    rec = tk._agent_rec(str(board), "bob") or {}
    assert rec.get("adapter_failure", {}).get("state") == "failed"
    assert rec.get("adapter_failure", {}).get("state") != "retrying"
    # wake_seat retired only the observed lease; _note must not delete by seat.
    assert sa.read_endpoint(str(board), "bob") is None


def test_live_endpoint_stale_cleanup_does_not_drop_rebind(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    dead = str(Path(sock_dir) / "dead.sock")
    alive = str(Path(sock_dir) / "alive.sock")
    Path(dead).touch()
    Path(alive).touch()
    sa = _adapters()
    sa.write_endpoint(str(board), "alice", {
        "seat": "alice", "provider": "claude", "mode": "native",
        "socket": dead, "token": "", "pid": 99999999, "at": "now",
        "lease_id": "old-lease", "fence": 1, "heartbeat_epoch": time.time()})
    observed = sa.read_endpoint(str(board), "alice")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", alive)
    monkeypatch.setenv("TICKET_SESSION_PID", str(os.getpid()))
    monkeypatch.delenv("TICKETS_SESSION_LEASE", raising=False)
    assert sa.register_persistent(str(board), "alice", "claude", "now").get("ok")
    sa.remove_endpoint_if_match(str(board), "alice", expected_lease=observed["lease_id"],
                                 expected_fence=int(observed["fence"]))
    ep, was_stale = sa.live_endpoint(str(board), "alice")
    assert ep is not None and not was_stale
    assert ep["socket"] == alive
    assert ep.get("lease_id") != "old-lease"


def test_pidless_codex_reconnects_and_first_wake_keeps_thread(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-old")
    monkeypatch.delenv("TICKETS_SESSION_LEASE", raising=False)
    sa = _adapters()
    sa.write_endpoint(str(board), "codex-seat", {
        "seat": "codex-seat", "provider": "codex", "mode": "native",
        "thread": "thread-old", "pid": None, "at": "now", "heartbeat_epoch": 1,
        "lease_id": "old-lease", "fence": 1})
    with mock.patch.object(sa, "_which", return_value="/bin/codex"):
        with mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess([], 0, "queue", "")
            reg = sa.register_persistent(str(board), "codex-seat", "codex", "now")
    assert reg.get("ok"), reg
    ep, stale = sa.live_endpoint(str(board), "codex-seat")
    assert ep is not None and not stale
    assert ep.get("thread") == "thread-old"
    sa.write_endpoint(str(board), "codex-seat", {
        "seat": "codex-seat", "provider": "codex", "mode": "native",
        "thread": "thread-old", "pid": None, "at": "now", "heartbeat_epoch": 1,
        "lease_id": ep.get("lease_id"), "fence": int(ep.get("fence") or 1)})
    with mock.patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        label = sa.wake_seat(str(board), "codex-seat", "hello", harness="codex",
                             message_id="after-idle")
    assert label == "queued-offline"
    live, _ = sa.live_endpoint(str(board), "codex-seat")
    # Retained pid-less identity may be TTL-stale; delivery still recorded on endpoint.
    stored = sa.read_endpoint(str(board), "codex-seat")
    assert stored is not None
    assert stored.get("last_delivery_id") == "after-idle"
    assert stored.get("last_delivery_status") == "queued-offline"


def test_concurrent_same_message_id_pokes_once(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(Path(sock_dir) / "once.sock")
    Path(sock_path).touch()
    sa = _adapters()
    sa.write_endpoint(str(board), "bob", {
        "seat": "bob", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-1", "fence": 1, "heartbeat_epoch": time.time()})
    in_poke = threading.Event()
    proceed = threading.Event()
    pokes = []

    def slow_poke(ep, text):
        pokes.append(1)
        in_poke.set()
        proceed.wait(timeout=5)
        return True

    labels = []

    def one():
        labels.append(sa.wake_seat(str(board), "bob", "hello", harness="claude",
                                    message_id="same-mid"))

    with mock.patch.object(sa, "_poke_claude", side_effect=slow_poke):
        t1 = threading.Thread(target=one)
        t1.start()
        assert in_poke.wait(timeout=5)
        t2 = threading.Thread(target=one)
        t2.start()
        t2.join(timeout=5)
        proceed.set()
        t1.join(timeout=5)
    assert not t1.is_alive() and not t2.is_alive()
    assert pokes == [1]
    assert labels.count("woken") == 1
    assert labels.count("deduped") == 1


def test_remote_and_custom_msg_stay_queued_offline(board):
    _run(board, "join", "grok-worker", "--roles", "docs", "--harness", "remote",
         "--wake-mode", "continuous")
    _run(board, "join", "custom-seat", "--roles", "backend", "--harness", "custom",
         "--cmd", "true {prompt_file}", "--wake-mode", "continuous")
    sent = _run(board, "msg", "please stay queued", "--to", "grok-worker", "--task",
                 agent="sender")
    assert sent.returncode == 0, sent.stderr
    sent2 = _run(board, "msg", "please stay queued too", "--to", "custom-seat", "--task",
                  agent="sender")
    assert sent2.returncode == 0, sent2.stderr
    tk = _tickets()
    for name in ("grok-worker", "custom-seat"):
        rec = tk._agent_rec(str(board), name) or {}
        assert "adapter_failure" not in rec, rec
    snap = json.loads(_run(board, "ui", "--json", agent="sender").stdout)
    grok = next(a for a in snap["agents"] if a["name"] == "grok-worker")
    custom = next(a for a in snap["agents"] if a["name"] == "custom-seat")
    assert grok["adapter_state"] == "queued-offline"
    assert custom["adapter_state"] == "queued-offline"


def test_sigterm_child_prompt_emits_run_end(board):
    _run(board, "join", "runner", "--roles", "backend", "--wake-mode", "continuous")
    sent = _run(board, "msg", "held work dominates", "--to", "runner", "--task",
                 agent="sender")
    assert sent.returncode == 0, sent.stderr
    cmd = "%s -c 'import sys,time; open(sys.argv[1]).read(); time.sleep(60)' {prompt_file}" % (
        sys.executable,)
    home = board.parent.parent / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="runner", HOME=str(home))
    proc = subprocess.Popen(
        [sys.executable, str(TOOL), "watch", "--agent", "runner", "--persist",
         "--every", "1", "--exec", cmd, "--cwd", str(board.parent)],
        cwd=str(board.parent), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    run_file = board / "agents" / "runner.run"
    try:
        deadline = time.time() + 15
        rec = {}
        while time.time() < deadline:
            if run_file.exists():
                rec = json.loads(run_file.read_text())
                if rec.get("active"):
                    break
            time.sleep(0.1)
        else:
            proc.kill()
            out = (proc.stdout.read() if proc.stdout else "") + (proc.stderr.read() if proc.stderr else "")
            raise AssertionError("watch never started a child: %s" % out)
        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
        rec = json.loads(run_file.read_text())
        assert rec.get("active") is False
        assert rec.get("interrupted") is True
        assert rec.get("rc") == 143
        path = board / "trajectories.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        ends = [e for e in rows if e.get("kind") == "run_end" and e.get("agent") == "runner"]
        starts = [e for e in rows if e.get("kind") == "run_start" and e.get("agent") == "runner"]
        assert starts, rows
        assert len(ends) == 1
        assert ends[0].get("exit") == 143
        assert ends[0].get("interrupted") is True
        assert ends[0].get("run_id") == starts[0].get("run_id")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_initial_heartbeat_interrupt_terminates_child(tmp_path, monkeypatch):
    """SIGTERM during the first beat must not be swallowed by _safe."""
    tk = _tickets()
    started = {}
    original_popen = subprocess.Popen

    def capture_popen(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        started["proc"] = proc
        return proc

    monkeypatch.setattr(subprocess, "Popen", capture_popen)

    def interrupted_beat():
        raise InterruptedError()

    cmd = "%s -c 'import time; time.sleep(60)'" % sys.executable
    with pytest.raises(InterruptedError):
        tk._watch_run_capped(
            cmd, str(tmp_path), dict(os.environ), str(tmp_path / "watch.log"),
            60, 4096, on_beat=interrupted_beat, beat_secs=1)
    assert started["proc"].poll() is not None


def test_stale_inflight_message_id_is_reclaimed(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(Path(sock_dir) / "reclaim.sock")
    Path(sock_path).touch()
    sa = _adapters()
    sa.write_endpoint(str(board), "bob", {
        "seat": "bob", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-1", "fence": 1, "heartbeat_epoch": time.time(),
        "last_inflight_id": "crash-mid", "last_inflight_epoch": 1})
    with mock.patch.object(sa, "_poke_claude", return_value=True) as poke:
        label = sa.wake_seat(str(board), "bob", "hello", harness="claude",
                             message_id="crash-mid")
    assert label == "woken"
    assert poke.call_count == 1
    ep = sa.read_endpoint(str(board), "bob")
    assert ep.get("last_delivery_id") == "crash-mid"
    assert ep.get("last_inflight_id") is None


def test_codex_hook_heartbeat_requires_matching_identity(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    registered_at = "2026-01-01T00:00:00Z"
    sa.write_endpoint(str(board), "cx", {
        "seat": "cx", "provider": "codex", "mode": "native",
        "thread": "thread-live", "pid": None, "at": registered_at,
        "lease_id": "lease-cx", "fence": 1, "heartbeat_epoch": 1,
        "heartbeat_at": registered_at})
    monkeypatch.delenv("TICKETS_SESSION_LEASE", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    assert sa.heartbeat_session(str(board), "cx") is None
    assert sa.read_endpoint(str(board), "cx")["heartbeat_at"] == registered_at
    assert sa.heartbeat_session(str(board), "cx", thread="other-thread") is None
    matched = sa.heartbeat_session(str(board), "cx", thread="thread-live")
    assert matched is not None
    assert matched["heartbeat_at"] != registered_at
    assert matched["heartbeat_epoch"] > 1
    leased = sa.heartbeat_session(str(board), "cx", presented_lease="lease-cx")
    assert leased is not None
    _run(board, "join", "cx", "--roles", "docs")
    ev = json.dumps({"hook_event_name": "SessionStart", "cwd": str(board.parent)})
    before_mismatch = sa.read_endpoint(str(board), "cx")["heartbeat_epoch"]
    mismatch = _run(board, "codex-hook", "--agent", "cx", "--worktree", str(board.parent),
                    env={"TICKETS_CACHE_DIR": cache_dir, "CODEX_THREAD_ID": "wrong"},
                    stdin=ev)
    assert mismatch.returncode == 0, mismatch.stderr
    assert sa.read_endpoint(str(board), "cx")["heartbeat_epoch"] == before_mismatch
    match = _run(board, "codex-hook", "--agent", "cx", "--worktree", str(board.parent),
                 env={"TICKETS_CACHE_DIR": cache_dir, "CODEX_THREAD_ID": "thread-live"},
                 stdin=ev)
    assert match.returncode == 0, match.stderr
    after = sa.read_endpoint(str(board), "cx")
    assert after["heartbeat_epoch"] > before_mismatch
    assert after["heartbeat_at"] != registered_at


def test_native_failure_clears_on_remote_rejoin_and_live_lease_is_online(
        board, cache_dir, sock_dir, monkeypatch):
    from test_t640_continuous_wake import _install_remote, _remote

    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(Path(sock_dir) / "leak.sock")
    Path(sock_path).touch()
    sa = _adapters()
    _run(board, "join", "grok-worker", "--roles", "docs", "--harness", "claude",
         "--wake-mode", "continuous")
    sa.write_endpoint(str(board), "grok-worker", {
        "seat": "grok-worker", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-native", "fence": 1, "heartbeat_epoch": time.time()})
    with mock.patch.object(sa, "_poke_claude", return_value=False):
        label = sa.wake_seat(str(board), "grok-worker", "hello", harness="claude",
                             message_id="native-fail")
    assert label == "refused"
    tk = _tickets()
    tk._note_native_wake_result(str(board), "grok-worker", label, "native-fail")
    rec = tk._agent_rec(str(board), "grok-worker") or {}
    assert rec.get("adapter_failure", {}).get("state") == "failed"
    assert rec.get("adapter_failure", {}).get("provider") == "claude"

    _run(board, "join", "grok-worker", "--roles", "docs", "--harness", "remote",
         "--wake-mode", "continuous")
    rec = tk._agent_rec(str(board), "grok-worker") or {}
    assert "adapter_failure" not in rec, rec

    wrapper = _install_remote(board, "grok-worker")
    registered, lease = _remote(wrapper, "register", "--agent", "grok-worker",
                                "--bridge-id", "grok-session", "--ttl", "20")
    assert registered.returncode == 0, registered.stderr
    assert lease.get("status") == "online"
    snap = json.loads(_run(board, "ui", "--json", agent="sender").stdout)
    grok = next(a for a in snap["agents"] if a["name"] == "grok-worker")
    assert grok["adapter_online"] is True
    assert grok["adapter_state"] != "failed"
    assert grok["adapter_state"] in ("online", "queued")
    assert grok["adapter_bridge_id"] == "grok-session"


def test_unscoped_native_failure_does_not_override_live_remote_lease(board):
    from test_t640_continuous_wake import _install_remote, _remote

    _run(board, "join", "grok-worker", "--roles", "docs", "--harness", "remote",
         "--wake-mode", "continuous")
    tk = _tickets()
    tk._agent_set(str(board), "grok-worker", adapter_failure={
        "state": "failed",
        "reason": "native wake refused",
        "at": "now",
    })
    wrapper = _install_remote(board, "grok-worker")
    registered, lease = _remote(wrapper, "register", "--agent", "grok-worker",
                                "--bridge-id", "grok-live", "--ttl", "20")
    assert registered.returncode == 0, registered.stderr
    assert lease.get("status") == "online"
    snap = json.loads(_run(board, "ui", "--json", agent="sender").stdout)
    grok = next(a for a in snap["agents"] if a["name"] == "grok-worker")
    assert grok["adapter_online"] is True
    assert grok["adapter_state"] != "failed"
    assert grok["adapter_state"] in ("online", "queued")
    assert grok["adapter_bridge_id"] == "grok-live"


def test_unscoped_watcher_failure_clears_on_codex_and_cursor_rejoin(board):
    tk = _tickets()
    _run(board, "join", "cx", "--roles", "docs", "--harness", "claude")
    tk._agent_set(str(board), "cx", adapter_failure={
        "state": "failed",
        "reason": "local harness exit 1",
        "at": "now",
    })
    snap = json.loads(_run(board, "ui", "--json", agent="sender").stdout)
    row = next(a for a in snap["agents"] if a["name"] == "cx")
    assert row["adapter_state"] == "failed"

    _run(board, "join", "cx", "--roles", "docs", "--harness", "codex")
    rec = tk._agent_rec(str(board), "cx") or {}
    assert "adapter_failure" not in rec, rec
    snap = json.loads(_run(board, "ui", "--json", agent="sender").stdout)
    row = next(a for a in snap["agents"] if a["name"] == "cx")
    assert row["adapter_state"] != "failed"

    _run(board, "join", "cx", "--roles", "docs", "--harness", "custom",
         "--cmd", "true {prompt_file}")
    tk._agent_set(str(board), "cx", adapter_failure={
        "state": "failed",
        "reason": "local harness exit 1",
        "at": "now",
    })
    _run(board, "join", "cx", "--roles", "docs", "--harness", "cursor")
    rec = tk._agent_rec(str(board), "cx") or {}
    assert "adapter_failure" not in rec, rec
    snap = json.loads(_run(board, "ui", "--json", agent="sender").stdout)
    row = next(a for a in snap["agents"] if a["name"] == "cx")
    assert row["adapter_state"] != "failed"


def test_same_provider_rejoin_keeps_scoped_watcher_failure(board):
    tk = _tickets()
    _run(board, "join", "cx", "--roles", "docs", "--harness", "codex")
    tk._agent_set(str(board), "cx", adapter_failure={
        "state": "failed",
        "reason": "local harness exit 1",
        "at": "now",
        "provider": "codex",
        "harness": "codex",
    })
    _run(board, "join", "cx", "--roles", "docs", "--harness", "codex")
    rec = tk._agent_rec(str(board), "cx") or {}
    assert rec.get("adapter_failure", {}).get("state") == "failed"
    assert rec.get("adapter_failure", {}).get("provider") == "codex"
    snap = json.loads(_run(board, "ui", "--json", agent="sender").stdout)
    row = next(a for a in snap["agents"] if a["name"] == "cx")
    assert row["adapter_state"] == "failed"


def test_claude_scoped_failure_is_ignored_after_codex_join_without_clear(board):
    tk = _tickets()
    _run(board, "join", "cx", "--roles", "docs", "--harness", "codex")
    tk._agent_set(str(board), "cx", adapter_failure={
        "state": "failed",
        "reason": "native wake refused",
        "at": "now",
        "provider": "claude",
        "harness": "claude",
    })
    snap = json.loads(_run(board, "ui", "--json", agent="sender").stdout)
    row = next(a for a in snap["agents"] if a["name"] == "cx")
    assert row["adapter_state"] != "failed"
    assert tk._local_adapter_failure(
        tk._agent_rec(str(board), "cx"), "codex") == {}


def _cursor_shaped_failing_agent(path):
    path.write_text("#!/bin/sh\nexit 1\n")
    path.chmod(0o755)


@pytest.mark.parametrize("harness_name,expected_provider", [
    ("cursor", "cursor"),
    ("cursor+claude", "claude"),
])
def test_cursor_shaped_watcher_stops_after_three_failed_runs(
        board, harness_name, expected_provider):
    """agent -p is custom to _harness_of_cmd; cost gate must still bind the seat."""
    seat = "cx-%s" % harness_name.replace("+", "-")
    _run(board, "join", seat, "--roles", "docs", "--harness", harness_name,
         "--wake-mode", "continuous")
    bin_dir = board.parent.parent / "bin"
    bin_dir.mkdir(exist_ok=True)
    _cursor_shaped_failing_agent(bin_dir / "agent")
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=seat,
               HOME=str(board.parent.parent / "home"),
               PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    env.pop("TICKETS_STOP_HOOK", None)
    proc = subprocess.Popen(
        [sys.executable, str(TOOL), "watch", "--agent", seat, "--every", "1",
         "--persist", "--exec", "agent -p --force paid-turn"],
        env=env, cwd=str(board.parent),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    log_path = board / "agents" / ("%s.watch.log" % seat)
    rec_path = board / "agents" / ("%s.json" % seat)
    try:
        deadline = time.time() + 25
        rec = {}
        while time.time() < deadline:
            if rec_path.exists():
                rec = json.loads(rec_path.read_text())
                failure = rec.get("adapter_failure") or {}
                if failure.get("state") == "failed" and int(failure.get("attempts") or 0) >= 3:
                    break
            time.sleep(0.2)
        else:
            log = log_path.read_text() if log_path.exists() else ""
            pytest.fail("did not reach terminal failed: rec=%s log=%s" % (rec, log[-800:]))
        time.sleep(2)
        log = log_path.read_text()
        assert log.count("run 1 trigger=") == 1
        assert log.count("run 2 trigger=") == 1
        assert log.count("run 3 trigger=") == 1
        assert "run 4 trigger=" not in log
        failure = rec.get("adapter_failure") or {}
        assert failure.get("state") == "failed"
        assert failure.get("attempts") == 3
        assert failure.get("harness") == harness_name
        assert failure.get("provider") == expected_provider
        starts = []
        traj = board / "trajectories.jsonl"
        if traj.exists():
            starts = [json.loads(ln) for ln in traj.read_text().splitlines()
                      if ln.strip() and json.loads(ln).get("kind") == "run_start"]
        assert len(starts) == 3
    finally:
        proc.terminate()
        proc.wait(timeout=10)
