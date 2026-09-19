"""T-1106: missing harness is unknown; UI posts take the cmd_msg wake path."""
import json

from test_wakeup import board, run  # noqa: F401
from ui_server_harness import UiServer


def _agent(board_dir, name):
    path = board_dir / "agents" / ("%s.json" % name)
    return json.loads(path.read_text()) if path.exists() else {}


def test_harness_badge_never_defaults_to_claude(board):
    run(board, "join", "bare", agent="bare")
    run(board, "join", "coded", "--harness", "codex", agent="coded")
    srv = UiServer(board, probe_prefix="t1106-harness")
    try:
        snap = srv.get("/board.json")
        by_name = {a["name"]: a for a in snap["agents"]}
        assert by_name["bare"]["harness"] == "unknown"
        assert by_name["coded"]["harness"] == "codex"
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


def test_ui_post_wakes_like_cli(board):
    """Same first-wake label from atm msg and POST /msg (separate seats)."""
    # Blind the parent harness socket so join does not register this Cursor
    # session as a live endpoint (that would make the first wake consume it).
    blind = {"CLAUDE_CODE_MESSAGING_SOCKET": "/no/such/t1106.sock"}
    run(board, "join", "owner", agent="owner", env=blind)
    for name in ("lead-cli", "lead-ui"):
        run(board, "join", name, "--roles", "backend",
            "--wake-mode", "continuous", agent=name, env=blind)
    cli = run(board, "msg", "cli wake probe", "--to", "lead-cli", agent="owner",
              env=blind)
    assert cli.returncode == 0, cli.stderr
    cli_label = (_agent(board, "lead-cli").get("wake_delivery") or {}).get("label")
    assert cli_label, "atm msg should record wake_delivery"

    srv = UiServer(board, probe_prefix="t1106-wake")
    try:
        status, out = srv.post("/msg", {
            "from": "owner", "text": "ui wake probe", "to": "lead-ui",
        })
        assert status == 200 and out["ok"], out
        ui_label = (_agent(board, "lead-ui").get("wake_delivery") or {}).get("label")
        assert ui_label, "UI post should record wake_delivery"
        assert ui_label == cli_label
        snap = srv.get("/board.json")
        lead = next(a for a in snap["agents"] if a["name"] == "lead-ui")
        assert (lead.get("wake_delivery") or {}).get("label") == ui_label
    finally:
        srv.stop()
