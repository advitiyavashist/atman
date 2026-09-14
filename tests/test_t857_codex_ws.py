"""T-857: Codex app-server control socket needs a WebSocket handshake."""

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import threading
import time
from unittest import mock

import session_adapters as sa

from test_t683_session_adapters import _run, board, cache_dir, sock_dir  # noqa: F401


_WS_GUID = "258EAFA5-E914-47DA-95AA-C5AB0DC85B11"


def _unmask(buf):
    opcode = buf[0] & 0x0F
    masked = bool(buf[1] & 0x80)
    n = buf[1] & 0x7F
    off = 2
    if n == 126:
        n = struct.unpack(">H", buf[2:4])[0]
        off = 4
    elif n == 127:
        n = struct.unpack(">Q", buf[2:10])[0]
        off = 10
    if masked:
        mask = buf[off:off + 4]
        off += 4
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(buf[off:off + n]))
    else:
        payload = buf[off:off + n]
    return opcode, payload, buf[off + n:]


def _server_frame(payload):
    n = len(payload)
    if n < 126:
        header = bytes([0x81, n])
    elif n < 65536:
        header = bytes([0x81, 126]) + struct.pack(">H", n)
    else:
        header = bytes([0x81, 127]) + struct.pack(">Q", n)
    return header + payload


class FakeCodexWs:
    """Unix WebSocket JSON-RPC that records handshake then initialize order."""

    def __init__(self, path, loaded_threads=None):
        self.path = str(path)
        try:
            os.unlink(self.path)
        except OSError:
            pass
        self.loaded = list(loaded_threads or [])
        self.events = []
        self.turn_starts = []
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
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        conn.settimeout(3)
        buf = b""
        try:
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            header, buf = buf.split(b"\r\n\r\n", 1)
            req = header.decode("ascii", "replace")
            if "Upgrade: websocket" not in req:
                self.events.append("bare-json")
                conn.close()
                return
            self.events.append("handshake")
            key = ""
            for line in req.split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
            acc = base64.b64encode(accept).decode("ascii")
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % acc
            ).encode("ascii"))
            saw_init = False
            while not self._stop.is_set():
                while len(buf) < 2:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                try:
                    op, payload, buf = _unmask(buf)
                except (struct.error, IndexError, ValueError):
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                    continue
                if op != 1:
                    continue
                msg = json.loads(payload.decode("utf-8"))
                method = msg.get("method")
                self.events.append(method)
                if method == "initialize":
                    saw_init = True
                    conn.sendall(_server_frame(json.dumps({
                        "jsonrpc": "2.0", "id": msg.get("id"),
                        "result": {"protocolVersion": 1},
                    }).encode("utf-8")))
                    continue
                if method == "initialized":
                    continue
                if not saw_init:
                    conn.close()
                    return
                rid = msg.get("id")
                if method == "thread/loaded/list":
                    result = {"data": [{"id": t} for t in self.loaded]}
                elif method == "turn/start":
                    self.turn_starts.append(msg.get("params") or {})
                    result = {"ok": True}
                else:
                    result = {}
                conn.sendall(_server_frame(json.dumps({
                    "jsonrpc": "2.0", "id": rid, "result": result,
                }).encode("utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except OSError:
            pass


def test_codex_ws_handshake_then_initialize_order(sock_dir, monkeypatch):
    sock = os.path.join(sock_dir, "c.sock")
    fake = FakeCodexWs(sock, loaded_threads=["thread-live"])
    monkeypatch.setenv("CODEX_APP_SERVER_CONTROL_SOCK", str(sock))
    try:
        resp = sa._codex_app_server_rpc("thread/loaded/list", {})
        assert resp and resp.get("id") == 1
        assert "thread-live" in sa._thread_ids_from_loaded_result(resp)
        assert fake.events[:3] == ["handshake", "initialize", "initialized"]
        assert "thread/loaded/list" in fake.events
    finally:
        fake.close()


def test_unloaded_thread_is_queued_offline_without_heartbeat(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock = os.path.join(sock_dir, "c.sock")
    fake = FakeCodexWs(sock, loaded_threads=[])
    monkeypatch.setenv("CODEX_APP_SERVER_CONTROL_SOCK", str(sock))
    _run(board, "join", "planner", "--roles", "docs", "--harness", "codex")
    sa.write_endpoint(str(board), "planner", {
        "seat": "planner", "provider": "codex", "mode": "native",
        "thread": "thread-missing", "pid": os.getpid(), "at": "then",
        "heartbeat_epoch": 1.0, "heartbeat_at": "then",
        "lease_id": "lease-1", "fence": 1,
    })
    try:
        with mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
            label = sa.wake_seat(str(board), "planner", "hello", harness="codex")
        assert label == "queued-offline"
        assert not fake.turn_starts
        ep = sa.read_endpoint(str(board), "planner")
        assert ep.get("heartbeat_epoch") == 1.0
        assert sa.native_wake_online(str(board), "planner") is False
        who = _run(board, "who", env={"TICKETS_CACHE_DIR": cache_dir,
                                      "CODEX_APP_SERVER_CONTROL_SOCK": str(sock)})
        assert "reachable=no" in who.stdout
    finally:
        fake.close()


def test_loaded_thread_turn_start_is_woken(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock = os.path.join(sock_dir, "c.sock")
    fake = FakeCodexWs(sock, loaded_threads=["thread-live"])
    monkeypatch.setenv("CODEX_APP_SERVER_CONTROL_SOCK", str(sock))
    sa.write_endpoint(str(board), "planner", {
        "seat": "planner", "provider": "codex", "mode": "native",
        "thread": "thread-live", "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time(),
        "lease_id": "lease-1", "fence": 1,
    })
    try:
        with mock.patch("subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess([], 0, "", "")
            label = sa.wake_seat(str(board), "planner", "act now", harness="codex")
        assert label == "woken"
        assert fake.turn_starts
        assert fake.turn_starts[0].get("threadId") == "thread-live"
        ep = sa.read_endpoint(str(board), "planner")
        assert float(ep.get("heartbeat_epoch") or 0) > 1
    finally:
        fake.close()
