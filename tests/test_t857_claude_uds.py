"""T-857: Claude UDS inbox is JSON user envelope + ack-gated woken."""

import json
import os
import socket
import threading
import time

import session_adapters as sa

from test_t683_session_adapters import FakeInbox, _run, board, cache_dir, sock_dir  # noqa: F401


class AckInbox:
    def __init__(self, path, ack=True, token="tok"):
        self.path = str(path)
        self.token = token
        self.ack = ack
        self.frames = []
        try:
            os.unlink(self.path)
        except OSError:
            pass
        self._stop = threading.Event()
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(4)
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
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            raw = line.decode("utf-8", "replace")
                            try:
                                obj = json.loads(raw)
                            except ValueError:
                                self.frames.append(raw)
                                continue
                            self.frames.append(obj)
                            if obj.get("type") == "user" and self.ack:
                                conn.sendall(b'{"type":"ack","ok":true}\n')
                except OSError:
                    pass

    def close(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


def test_user_envelope_plus_ack_is_woken(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(__import__("pathlib").Path(sock_dir) / "claude-ack.sock")
    inbox = AckInbox(sock_path)
    sa.write_endpoint(str(board), "cos", {
        "seat": "cos", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "tok", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-c", "fence": 1, "heartbeat_epoch": 1})
    before = sa.read_endpoint(str(board), "cos")["heartbeat_epoch"]
    label = sa.wake_seat(str(board), "cos", "nonce-idle please reply", harness="claude",
                          message_id="claude-ack-1")
    inbox.close()
    assert label == "woken", label
    assert inbox.frames[0] == {"type": "auth", "token": "tok"}
    user = inbox.frames[1]
    assert user["type"] == "user"
    assert user["message"]["role"] == "user"
    assert "nonce-idle please reply" in user["message"]["content"]
    ep = sa.read_endpoint(str(board), "cos")
    assert ep["heartbeat_epoch"] >= before
    assert ep.get("last_delivery_status") == "woken"


def test_no_ack_is_delivered_unconfirmed_without_heartbeat(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(__import__("pathlib").Path(sock_dir) / "claude-silent.sock")
    inbox = AckInbox(sock_path, ack=False)
    sa.write_endpoint(str(board), "cos", {
        "seat": "cos", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "tok", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-c", "fence": 1, "heartbeat_epoch": 11})
    label = sa.wake_seat(str(board), "cos", "hello", harness="claude",
                          message_id="claude-silent-1")
    inbox.close()
    assert label == "delivered-unconfirmed", label
    ep = sa.read_endpoint(str(board), "cos")
    assert ep["heartbeat_epoch"] == 11
    assert ep.get("last_delivery_status") == "delivered-unconfirmed"
    assert ep.get("last_attempt_status") is None
    assert inbox.frames[0]["type"] == "auth"
    assert inbox.frames[1]["type"] == "user"


def test_msg_task_prints_woken_only_when_inbox_acks(board, cache_dir, sock_dir):
    sock_path = str(__import__("pathlib").Path(sock_dir) / "claude-msg.sock")
    inbox = FakeInbox(sock_path)
    r = _run(board, "join", "cos-seat", "--roles", "ops", "--persistent",
             env={"TICKETS_CACHE_DIR": cache_dir,
                  "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
                  "CLAUDE_CODE_MESSAGING_TOKEN": "tok",
                  "TICKET_SESSION_PID": str(os.getpid())})
    assert r.returncode == 0, r.stderr
    sent = _run(board, "msg", "please act", "--to", "cos-seat", "--task",
                agent="sender", env={"TICKETS_CACHE_DIR": cache_dir})
    assert sent.returncode == 0, sent.stderr
    assert "wake: cos-seat -> woken" in sent.stdout
    payload = inbox.wait_for_message()
    inbox.close()
    assert '"type": "user"' in payload or '"type":"user"' in payload
    assert "please act" in payload
    assert "\nplease act\n" not in payload
