"""T-1106: missing harness is unknown; UI posts take the cmd_msg wake path."""
import json
import os
import shutil
import tempfile

import session_adapters as sa

from test_t683_session_adapters import FakeInbox
from test_wakeup import board, run  # noqa: F401
from ui_server_harness import UiServer


def _agent(board_dir, name):
    path = board_dir / "agents" / ("%s.json" % name)
    return json.loads(path.read_text()) if path.exists() else {}


def _isolated(board_dir):
    home = board_dir.parent.parent / "home"
    cache = board_dir.parent / "cache"
    cache.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    return {"HOME": str(home), "TICKETS_CACHE_DIR": str(cache)}


def _write_fake_claude(board_dir, seat, sock_path):
    sa.write_endpoint(str(board_dir), seat, {
        "seat": seat, "provider": "claude", "mode": "native",
        "socket": sock_path, "token": "tok", "pid": os.getpid(), "at": "now",
        "lease_id": "lease-%s" % seat, "fence": 1, "heartbeat_epoch": 1,
    })


def test_harness_badge_never_defaults_to_claude(board):
    run(board, "join", "bare", agent="bare")
    run(board, "join", "coded", "--harness", "codex", agent="coded")
    srv = UiServer(board, probe_prefix="t1106-harness")
    try:
        snap = srv.get("/board.json")
        by_name = {a["name"]: a for a in snap["agents"]}
        assert by_name["bare"]["harness"] == "unknown"
        assert by_name["bare"]["adapter_provider"] == "unknown"
        assert by_name["bare"]["adapter_provider"] != "custom"
        assert by_name["coded"]["harness"] == "codex"
        assert by_name["coded"]["adapter_provider"] == "codex"
        assert by_name["bare"]["harness"] != "claude"
        status, out = srv.post("/msg", {
            "from": "bare", "text": "no harness on this sender", "to": "",
        })
        assert status == 200 and out["ok"], out
        snap = srv.get("/board.json")
        msg = next(m for m in snap["messages"] if m.get("text") == "no harness on this sender")
        assert msg["harness"] == "unknown"
    finally:
        srv.stop()


def test_ui_post_wakes_like_cli(board, monkeypatch):
    """Same first-wake label and a real socket delivery from atm msg and POST /msg."""
    isolated = _isolated(board)
    monkeypatch.setenv("HOME", isolated["HOME"])
    monkeypatch.setenv("TICKETS_CACHE_DIR", isolated["TICKETS_CACHE_DIR"])
    for var in ("TICKET_SEAT", "TICKET_SESSION_ID",
                "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET"):
        monkeypatch.delenv(var, raising=False)

    run(board, "join", "owner", agent="owner", env=isolated)
    for name in ("lead-cli", "lead-ui", "bystander"):
        run(board, "join", name, "--roles", "backend", "--harness", "claude",
            "--wake-mode", "continuous", agent=name, env=isolated)

    sock_dir = tempfile.mkdtemp(prefix="t1106", dir="/tmp")
    inboxes = {}
    try:
        for name in ("lead-cli", "lead-ui", "bystander"):
            path = os.path.join(sock_dir, "%s.sock" % name)
            inboxes[name] = FakeInbox(path)
            _write_fake_claude(board, name, path)

        cli = run(board, "msg", "cli wake probe", "--to", "lead-cli",
                  agent="owner", env=isolated)
        assert cli.returncode == 0, cli.stderr
        cli_label = (_agent(board, "lead-cli").get("wake_delivery") or {}).get("label")
        assert cli_label, "atm msg should record wake_delivery"
        assert inboxes["lead-cli"].wait_for_message(), "atm msg should hit the fake socket"
        assert not inboxes["lead-ui"].received
        assert not inboxes["bystander"].received

        srv = UiServer(board, probe_prefix="t1106-wake", env=isolated)
        try:
            status, out = srv.post("/msg", {
                "from": "owner", "text": "ui wake probe", "to": "lead-ui",
            })
            assert status == 200 and out["ok"], out
            ui_label = (_agent(board, "lead-ui").get("wake_delivery") or {}).get("label")
            assert ui_label, "UI post should record wake_delivery"
            assert ui_label == cli_label
            assert inboxes["lead-ui"].wait_for_message(), "UI post should hit the fake socket"
            assert not inboxes["bystander"].received
            snap = srv.get("/board.json")
            lead = next(a for a in snap["agents"] if a["name"] == "lead-ui")
            assert (lead.get("wake_delivery") or {}).get("label") == ui_label
        finally:
            srv.stop()
    finally:
        for inbox in inboxes.values():
            inbox.close()
        shutil.rmtree(sock_dir, ignore_errors=True)
