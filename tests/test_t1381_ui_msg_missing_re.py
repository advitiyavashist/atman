"""T-1381: UI POST /msg with a missing --re must 400 without posting or hanging.

Before the fix, the UI posted the message then died on SystemExit from load()
inside deliver_wakes -> _task_life_actionable, so the composer saw a dropped
connection, retried, and double-posted while the addressee was never woken.
CLI already refuses the same input. Isolation: temp HOME/cache, session vars
unset, decoy socket only.
"""
import json
import os
import shutil
import tempfile

import session_adapters as sa

from test_t683_session_adapters import FakeInbox
from test_t1106_ui_harness_wake import _isolated, _write_fake_claude, _agent
from test_wakeup import board, run  # noqa: F401
from ui_server_harness import UiServer


def _messages(board_dir):
    path = board_dir / "messages.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def test_ui_post_missing_re_refuses_like_cli(board, monkeypatch):
    """POST /msg with re=T-9999 -> 400, nothing posted, 0 wakes; CLI same refuse."""
    isolated = _isolated(board)
    monkeypatch.setenv("HOME", isolated["HOME"])
    monkeypatch.setenv("TICKETS_CACHE_DIR", isolated["TICKETS_CACHE_DIR"])
    for var in ("TICKET_SEAT", "TICKET_SESSION_ID",
                "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET"):
        monkeypatch.delenv(var, raising=False)

    run(board, "join", "pat", agent="pat", env=isolated)
    run(board, "join", "lead", "--roles", "backend", "--harness", "claude",
        "--wake-mode", "continuous", agent="lead", env=isolated)

    sock_dir = tempfile.mkdtemp(prefix="t1381", dir="/tmp")
    inbox = None
    try:
        sock = os.path.join(sock_dir, "lead.sock")
        inbox = FakeInbox(sock)
        _write_fake_claude(board, "lead", sock)

        before = len(_messages(board))
        cli = run(board, "msg", "cli nope", "--to", "lead", "--re", "T-9999",
                  "--task", agent="pat", env=isolated)
        assert cli.returncode == 1, cli.stderr
        assert "no such ticket" in (cli.stderr + cli.stdout).lower()
        assert not any(m.get("text") == "cli nope" for m in _messages(board))
        assert len(_messages(board)) == before
        assert not inbox.received

        srv = UiServer(board, probe_prefix="t1381-missing-re", env=isolated,
                       operator="pat")
        try:
            before_ui = [m for m in _messages(board)
                         if m.get("text") in ("ui nope", "cli nope")]
            status, out = srv.post("/msg", {
                "text": "ui nope", "to": "lead", "re": "T-9999", "kind": "task",
            })
            assert status == 400 and not out.get("ok"), out
            err = (out.get("error") or "").lower()
            assert "no such ticket" in err and "t-9999" in err, out
            after = _messages(board)
            assert not any(m.get("text") == "ui nope" for m in after), after
            assert [m for m in after if m.get("text") in ("ui nope", "cli nope")] == before_ui
            assert not inbox.received
            assert not (_agent(board, "lead").get("wake_delivery") or {}).get("label")
        finally:
            srv.stop()
    finally:
        if inbox is not None:
            inbox.close()
        shutil.rmtree(sock_dir, ignore_errors=True)


def test_task_life_actionable_survives_missing_ticket(board):
    """Defence in depth: load()'s SystemExit must not escape _task_life_actionable."""
    import tickets as t

    msg = {"re": "T-9999", "kind": "task", "text": "orphan task", "to": "anyone"}
    # Must return (not raise / not sys.exit) so a wake path stays alive.
    assert t._task_life_actionable(str(board), msg) is True
