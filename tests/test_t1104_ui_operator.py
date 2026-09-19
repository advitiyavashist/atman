"""T-1104: atm ui composer posts as the operator only, never a seat."""
import json

from test_wakeup import board, run  # noqa: F401
from ui_server_harness import UiServer


def _live(board_dir):
    path = board_dir / "messages.jsonl"
    return path.read_text() if path.exists() else ""


def _last_rec(board_dir):
    lines = [ln for ln in _live(board_dir).splitlines() if ln.strip()]
    assert lines, "expected a message on the board"
    return json.loads(lines[-1])


def test_composer_posts_as_operator_only(board):
    """Payload from:<seat> is refused. A real post is the operator + via=ui-operator."""
    run(board, "join", "owner", agent="owner")
    run(board, "join", "worker", agent="worker")
    srv = UiServer(board, probe_prefix="t1104-op", operator="owner")
    try:
        status, out = srv.post("/msg", {
            "from": "worker", "text": "impersonating a seat", "to": "",
        })
        assert status == 400 and not out["ok"], out
        err = (out.get("error") or "").lower()
        assert "operator" in err or "from" in err
        assert "impersonating a seat" not in _live(board)

        status, out = srv.post("/msg", {"text": "hello from the operator", "to": "worker"})
        assert status == 200 and out["ok"], out
        rec = _last_rec(board)
        assert rec["from"] == "owner"
        assert rec["via"] == "ui-operator"
        assert rec.get("sender_kind") == "operator"
        assert rec["text"] == "hello from the operator"
        assert rec["to"] == "worker"
        assert rec.get("unverified") is False
        snap = srv.get("/board.json")
        assert snap.get("operator") == "owner"
    finally:
        srv.stop()


def test_composer_disabled_without_operator(board):
    """No --operator: composer copy names the flag; POST /msg is 400."""
    run(board, "join", "worker", agent="worker")
    srv = UiServer(board, probe_prefix="t1104-none")
    try:
        page = srv.get("/", raw=True).decode()
        assert "set an operator: atm ui --operator" in page
        assert 'id="cFrom"' in page
        assert "(pick agent)" not in page
        status, out = srv.post("/msg", {
            "from": "worker", "text": "no-operator-should-not-land", "to": "",
        })
        assert status == 400 and not out["ok"], out
        assert "operator" in (out.get("error") or "").lower()
        assert "no-operator-should-not-land" not in _live(board)
        snap = srv.get("/board.json")
        assert snap.get("operator") == ""
    finally:
        srv.stop()


def test_matching_operator_from_is_accepted(board):
    """from matching the operator is allowed; still recorded as ui-operator."""
    run(board, "join", "owner", agent="owner")
    srv = UiServer(board, probe_prefix="t1104-match", operator="owner")
    try:
        status, out = srv.post("/msg", {
            "from": "owner", "text": "operator named themselves", "to": "",
        })
        assert status == 200 and out["ok"], out
        rec = _last_rec(board)
        assert rec["from"] == "owner"
        assert rec["via"] == "ui-operator"
    finally:
        srv.stop()
