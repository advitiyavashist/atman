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
        "thread": "thread-abc", "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time()})
    with mock.patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
        label = sa.wake_seat(str(board), "codex-seat", "hello", harness="codex")
    assert label == "queued"
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
    assert sa.read_endpoint(str(board), "codex-seat") is None


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
    assert label == "queued"
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
    label = sa.wake_seat(str(board), "grok-worker", "hello", harness="codex")
    assert "refused" in label
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
