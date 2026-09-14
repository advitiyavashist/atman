"""T-918: held peer_message_status is not woken; each status has its own label."""

import json
import os
import socket
from pathlib import Path

import pytest

import session_adapters as sa

from test_t683_session_adapters import board, cache_dir, sock_dir  # noqa: F401
from test_t857_claude_uds import AckInbox


class StatusInbox(AckInbox):
    """Reply with a Claude peer_message_status control frame."""

    def __init__(self, path, status, token="tok"):
        self.status = status
        super().__init__(path, ack=True, token=token)

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
                            if obj.get("type") == "user":
                                reply = {
                                    "type": "control",
                                    "action": "peer_message_status",
                                    "status": self.status,
                                }
                                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                except OSError:
                    pass


def test_held_peer_status_is_not_ack_ok():
    held = {"type": "control", "action": "peer_message_status", "status": "held"}
    assert sa._claude_ack_ok(held) is False
    assert sa._claude_receipt_label(held) == "held"
    assert sa._claude_ack_ok({"type": "ack", "ok": True}) is True
    assert sa._claude_receipt_label({"type": "ack", "ok": True}) == "delivered-confirmed"


@pytest.mark.parametrize("status,label", [
    ("held", "held"),
    ("delivered", "delivered-confirmed"),
    ("refused", "refused"),
    ("dropped", "dropped"),
    ("expired", "expired"),
])
def test_peer_message_status_maps_to_own_receipt(status, label):
    frame = {"type": "control", "action": "peer_message_status", "status": status}
    assert sa._claude_receipt_label(frame) == label
    assert sa._claude_ack_ok(frame) is False


def test_unknown_peer_status_is_not_woken():
    frame = {"type": "control", "action": "peer_message_status", "status": "queued"}
    assert sa._claude_receipt_label(frame) is None
    assert sa._claude_ack_ok(frame) is False


@pytest.mark.parametrize("status,label", [
    ("held", "held"),
    ("delivered", "delivered-confirmed"),
    ("refused", "refused"),
    ("dropped", "dropped"),
    ("expired", "expired"),
])
def test_wake_seat_reports_peer_status_without_heartbeat(
        board, cache_dir, sock_dir, monkeypatch, status, label):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    monkeypatch.setattr(sa, "CLAUDE_ACK_WAIT_SECS", 2.0)
    sock_path = str(Path(sock_dir) / ("claude-%s.sock" % status))
    inbox = StatusInbox(sock_path, status)
    sa.write_endpoint(str(board), "cos", {
        "seat": "cos", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "tok", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-c", "fence": 1, "heartbeat_epoch": 7})
    got = sa.wake_seat(str(board), "cos", "please act", harness="claude",
                       message_id="peer-%s" % status)
    inbox.close()
    assert got == label, got
    assert got != "woken"
    ep = sa.read_endpoint(str(board), "cos")
    assert ep is not None
    assert ep["heartbeat_epoch"] == 7
    assert ep.get("last_delivery_status") == label
    assert ep.get("last_delivery_id") == "peer-%s" % status


def test_held_receipt_prints_named_recovery_action():
    assert "crossSessionInbound accept" in sa.CLAUDE_HELD_RECOVERY
    assert "approve in the recipient session" in sa.CLAUDE_HELD_RECOVERY
    src = Path(__file__).resolve().parents[1].joinpath("tickets.py").read_text()
    assert "CLAUDE_HELD_RECOVERY" in src
    assert 'label == "held"' in src


@pytest.mark.parametrize("frame", [
    {"type": "ack", "ok": True},
    {"ok": True},
    {"status": "accepted"},
])
def test_generic_write_ack_is_delivery_confirmed_not_woken(frame):
    assert sa._claude_ack_ok(frame) is True
    assert sa._claude_receipt_label(frame) == "delivered-confirmed"
    assert sa._claude_receipt_label(frame) != "woken"


def test_write_only_ack_does_not_refresh_wake_heartbeat(
        board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    monkeypatch.setattr(sa, "CLAUDE_ACK_WAIT_SECS", 2.0)
    path = str(Path(sock_dir) / "write-only.sock")
    inbox = AckInbox(path)
    try:
        sa.write_endpoint(str(board), "worker", {
            "seat": "worker", "provider": "claude", "mode": "native",
            "socket": path, "token": "tok", "pid": os.getpid(), "at": "now",
            "lease_id": "lease-write", "fence": 1, "heartbeat_epoch": 7})
        label = sa.wake_seat(str(board), "worker", "delivery only", harness="claude",
                             message_id="write-only-message")
        endpoint = sa.read_endpoint(str(board), "worker")
        assert label == "delivered-confirmed", label
        assert label != "woken"
        assert endpoint["heartbeat_epoch"] == 7
        assert endpoint.get("last_delivery_status") == "delivered-confirmed"
        assert endpoint.get("last_delivery_id") == "write-only-message"
    finally:
        inbox.close()
