"""T-1047: live steer reaches a running Claude seat; no receipt is not delivered."""

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import steer as st  # noqa: E402
sys.path.insert(0, str(ROOT))
import session_adapters as sa  # noqa: E402
import tickets as tool  # noqa: E402

from test_t683_session_adapters import _run, board, cache_dir, sock_dir  # noqa: F401
from test_t857_claude_uds import AckInbox


def _ticket_json(board, tid="T-001"):
    return json.loads((Path(board) / ("%s.json" % tid)).read_text())


def _assign_ticket(board, seat, tid="T-001", harness="claude", env=None,
                   persistent=False):
    e = dict(env or {})
    args = ["join", seat, "--roles", "backend", "--harness", harness]
    if persistent:
        args.append("--persistent")
    joined = _run(board, *args, agent=seat, env=e)
    assert joined.returncode == 0, joined.stderr
    assigned = _run(board, "assign", tid, "--owner", seat, agent=seat, env=e)
    assert assigned.returncode == 0, assigned.stderr
    return joined, assigned


def test_helpers_never_call_unconfirmed_delivered():
    assert st.is_delivered("delivered-unconfirmed") is False
    assert st.is_delivered("delivered-confirmed") is True
    assert st.is_delivered("woken") is True
    assert st.is_delivered("queued-offline") is False
    assert st.report_receipt("delivered-unconfirmed") == "unconfirmed (no receipt)"
    assert "delivered" not in st.report_receipt("delivered-unconfirmed")
    assert st.report_receipt("delivered-confirmed").startswith("delivered")
    assert "Claude" in st.harness_refuse_reason("codex")
    assert st.harness_refuse_reason("claude") == ""
    payload = st.frame_payload("redirect", "boss", "stop GPU", "T-1", "ste-aaa")
    assert "do not silently rewrite ticket scope" in payload.lower()
    assert "ste-aaa" in payload
    ask = st.frame_payload("ask", "boss", "stuck?", "T-1", "ste-bbb")
    assert "STEER-REPLY ste-bbb:" in ask
    assert st.parse_steer_reply("STEER-REPLY ste-bbb: still rebasing", "ste-bbb")
    last, src = st.last_output({"stall": {"last_output_at": "2026-09-16T10:00:00Z"}},
                               transcript_ts="ignored")
    assert last == "2026-09-16T10:00:00Z" and src == "stall"


def test_steer_reaches_running_claude_and_is_recorded(
        board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    monkeypatch.setattr(sa, "CLAUDE_ACK_WAIT_SECS", 2.0)
    sock_path = str(Path(sock_dir) / "claude-steer.sock")
    inbox = AckInbox(sock_path)
    env = {"TICKETS_CACHE_DIR": cache_dir,
           "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
           "CLAUDE_CODE_MESSAGING_TOKEN": "tok",
           "TICKET_SESSION_PID": str(os.getpid())}
    _assign_ticket(board, "claude-runner", env=env, persistent=True)
    sa.write_endpoint(str(board), "claude-runner", {
        "seat": "claude-runner", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "tok", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-steer", "fence": 1, "heartbeat_epoch": 1})
    sent = _run(board, "steer", "claude-runner", "stop the GPU job; use fixtures",
                agent="boss", env={"TICKETS_CACHE_DIR": cache_dir})
    inbox.close()
    assert sent.returncode == 0, sent.stderr + sent.stdout
    assert "id=ste-" in sent.stdout
    assert "ticket=T-001" in sent.stdout
    assert "scope unchanged" in sent.stdout
    t = _ticket_json(board)
    assert t["title"] == "Write docs"
    notes = " ".join(n["text"] for n in t.get("notes") or [])
    assert "STEER redirect" in notes
    assert "stop the GPU job" in notes
    recs = t.get("steers") or []
    assert recs and recs[-1]["to"] == "claude-runner"
    assert recs[-1]["from"]
    user = [f for f in inbox.frames if isinstance(f, dict) and f.get("type") == "user"]
    assert user, inbox.frames
    assert user[0]["priority"] == "next"
    assert "STEER id=" in user[0]["message"]["content"]
    assert "stop the GPU job" in user[0]["message"]["content"]
    assert "Do not silently rewrite ticket scope" in user[0]["message"]["content"]


def test_ack_receipt_is_reported_delivered(board, cache_dir, sock_dir, monkeypatch):
    """Same-process inject: a real inbox ack is the only 'delivered' report."""
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    monkeypatch.setattr(sa, "CLAUDE_ACK_WAIT_SECS", 2.0)
    sock_path = str(Path(sock_dir) / "claude-steer-ack.sock")
    inbox = AckInbox(sock_path)
    env = {"TICKETS_CACHE_DIR": cache_dir,
           "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
           "CLAUDE_CODE_MESSAGING_TOKEN": "tok",
           "TICKET_SESSION_PID": str(os.getpid())}
    _assign_ticket(board, "acked", env=env, persistent=True)
    sa.write_endpoint(str(board), "acked", {
        "seat": "acked", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "tok", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-ack", "fence": 1, "heartbeat_epoch": 1})
    args = SimpleNamespace(seat="acked", text="use the fixture", ask="",
                           list=False, ticket="", owner="boss")
    tool.cmd_steer(args, str(board))
    inbox.close()
    t = _ticket_json(board)
    recs = t.get("steers") or []
    assert recs[-1]["delivered"] is True
    assert recs[-1]["receipt"] == "delivered-confirmed"


def test_unsteerable_harness_refuses_with_reason(board, cache_dir):
    env = {"TICKETS_CACHE_DIR": cache_dir}
    _assign_ticket(board, "codex-seat", harness="codex", env=env)
    sent = _run(board, "steer", "codex-seat", "please rebase onto main",
                agent="boss", env=env)
    assert sent.returncode == 0, sent.stderr + sent.stdout
    assert "refused" in sent.stdout
    assert "codex" in sent.stdout
    assert "not delivered" in sent.stdout
    assert "-> delivered (" not in sent.stdout
    t = _ticket_json(board)
    recs = t.get("steers") or []
    assert recs and recs[-1]["delivered"] is False
    assert "refused" in recs[-1]["receipt"]
    notes = " ".join(n["text"] for n in t.get("notes") or [])
    assert "STEER redirect" in notes
    assert "receipt=refused" in notes or "refused" in notes


def test_steer_without_receipt_is_never_reported_delivered(
        board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sock_path = str(Path(sock_dir) / "claude-silent-steer.sock")
    inbox = AckInbox(sock_path, ack=False)
    env = {"TICKETS_CACHE_DIR": cache_dir,
           "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
           "CLAUDE_CODE_MESSAGING_TOKEN": "tok",
           "TICKET_SESSION_PID": str(os.getpid())}
    _assign_ticket(board, "silent-claude", env=env, persistent=True)
    sa.write_endpoint(str(board), "silent-claude", {
        "seat": "silent-claude", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "tok", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-silent", "fence": 1, "heartbeat_epoch": 4})
    t0 = time.time()
    sent = _run(board, "steer", "silent-claude", "keep going but skip docs",
                agent="boss", env={"TICKETS_CACHE_DIR": cache_dir})
    elapsed = time.time() - t0
    inbox.close()
    assert elapsed < 1.5, elapsed
    assert sent.returncode == 0, sent.stderr + sent.stdout
    assert "unconfirmed (no receipt)" in sent.stdout
    assert "not delivered" in sent.stdout
    assert "-> delivered (" not in sent.stdout
    assert "delivered-unconfirmed" not in sent.stdout
    t = _ticket_json(board)
    recs = t.get("steers") or []
    assert recs and recs[-1]["delivered"] is False
    assert recs[-1]["receipt"] == "delivered-unconfirmed"
    note = [n["text"] for n in t.get("notes") or [] if n.get("kind") == "steer"][-1]
    assert "unconfirmed (no receipt)" in note
    assert "receipt=delivered (" not in note


def test_ask_records_question_and_keeps_scope(board, cache_dir, sock_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    monkeypatch.setattr(sa, "CLAUDE_ACK_WAIT_SECS", 2.0)
    sock_path = str(Path(sock_dir) / "claude-ask.sock")
    inbox = AckInbox(sock_path)
    env = {"TICKETS_CACHE_DIR": cache_dir,
           "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
           "CLAUDE_CODE_MESSAGING_TOKEN": "tok",
           "TICKET_SESSION_PID": str(os.getpid())}
    _assign_ticket(board, "ask-seat", env=env)
    sa.write_endpoint(str(board), "ask-seat", {
        "seat": "ask-seat", "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "tok", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-ask", "fence": 1, "heartbeat_epoch": 1})
    sent = _run(board, "steer", "ask-seat", "--ask", "are you stuck on the rebase?",
                agent="boss", env={"TICKETS_CACHE_DIR": cache_dir})
    inbox.close()
    assert sent.returncode == 0, sent.stderr + sent.stdout
    assert "STEER-REPLY" in sent.stdout
    t = _ticket_json(board)
    assert t["title"] == "Write docs"
    recs = t.get("steers") or []
    assert recs[-1]["kind"] == "ask"
    assert "stuck on the rebase" in recs[-1]["text"]
    user = [f for f in inbox.frames if isinstance(f, dict) and f.get("type") == "user"]
    assert "STEER-ASK" in user[0]["message"]["content"]
    assert "Do not end the current run" in user[0]["message"]["content"]


def test_steer_list_shows_last_output_and_steerable(board, cache_dir):
    env = {"TICKETS_CACHE_DIR": cache_dir}
    _assign_ticket(board, "listed", harness="codex", env=env)
    agents = Path(board) / "agents"
    agents.mkdir(exist_ok=True)
    rec_path = agents / "listed.json"
    rec = json.loads(rec_path.read_text())
    rec["stall"] = {"last_output_at": "2026-09-16T10:00:00Z",
                    "measured_s": 640, "source": "watch"}
    rec_path.write_text(json.dumps(rec))
    listed = _run(board, "steer", "--list", agent="boss", env=env)
    assert listed.returncode == 0, listed.stderr + listed.stdout
    assert "listed" in listed.stdout
    assert "2026-09-16T10:00:00Z" in listed.stdout
    assert "last-output source=stall" in listed.stdout
    assert "no mid-run steer" in listed.stdout
