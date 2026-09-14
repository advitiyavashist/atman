"""T-857: Claude UDS inbox takes a JSON user envelope; woken needs a receipt."""

import json
import os
import socket
import threading
import time
import uuid

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


def test_verified_frame_shape_and_receipt_gated_woken(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    # Production never waits for a receipt; raise the budget so the branch
    # that would honour one is exercised deterministically.
    monkeypatch.setattr(sa, "CLAUDE_ACK_WAIT_SECS", 2.0)
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
    assert user["msgV"] == 1
    assert user["priority"] == "next"
    assert uuid.UUID(user["msg_id"]).version == 4
    assert user["message"]["role"] == "user"
    assert "nonce-idle please reply" in user["message"]["content"]
    ep = sa.read_endpoint(str(board), "cos")
    assert ep["heartbeat_epoch"] >= before
    assert ep.get("last_delivery_status") == "woken"


def test_raw_text_payload_is_not_a_wake():
    """A bare line after auth is dropped by the JSONL inbox, so it never woke."""
    frame = sa._claude_user_envelope("please act\nsecond line")
    line = json.dumps(frame)
    assert "\n" not in line
    assert json.loads(line)["message"]["content"] == "please act\nsecond line"
    assert sa._claude_ack_ok(None) is False
    assert sa._claude_ack_ok("ok") is False


def test_no_ack_is_delivered_unconfirmed_without_heartbeat(board, cache_dir, sock_dir, monkeypatch):
    """Live Claude Code never acks, so the wake must not stall waiting."""
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    assert sa.CLAUDE_ACK_WAIT_SECS == 0.0
    sock_path = str(__import__("pathlib").Path(sock_dir) / "claude-silent.sock")
    inbox = AckInbox(sock_path, ack=False)
    sa.write_endpoint(str(board), "cos", {
        "seat": "cos", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "tok", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-c", "fence": 1, "heartbeat_epoch": 11})
    t0 = time.time()
    label = sa.wake_seat(str(board), "cos", "hello", harness="claude",
                          message_id="claude-silent-1")
    elapsed = time.time() - t0
    inbox.close()
    assert elapsed < 1.0, elapsed
    assert label == "delivered-unconfirmed", label
    ep = sa.read_endpoint(str(board), "cos")
    assert ep["heartbeat_epoch"] == 11
    assert ep.get("last_delivery_status") == "delivered-unconfirmed"
    assert ep.get("last_attempt_status") is None
    assert inbox.frames[0]["type"] == "auth"
    assert inbox.frames[1]["type"] == "user"


def test_msg_task_receipt_is_delivered_unconfirmed_without_an_ack(board, cache_dir, sock_dir):
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
    # FakeInbox does ack, but production does not wait for one, so the CLI
    # must report what it actually knows.
    assert "wake: cos-seat -> delivered-unconfirmed" in sent.stdout
    payload = inbox.wait_for_message()
    inbox.close()
    assert '"type": "user"' in payload or '"type":"user"' in payload
    assert "please act" in payload
    assert "\nplease act\n" not in payload
