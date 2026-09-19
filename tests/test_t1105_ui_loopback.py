"""T-1105: atm ui is loopback-only; writes need the per-launch token."""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

from test_wakeup import TOOL, board, run  # noqa: F401
from ui_server_harness import UiServer, _free_port


def _live(board_dir):
    path = board_dir / "messages.jsonl"
    return path.read_text() if path.exists() else ""


def test_host_header_must_be_loopback(board):
    """DNS-rebinding shape: matching Origin+Host that is not loopback is refused."""
    run(board, "join", "boss", agent="boss")
    srv = UiServer(board, probe_prefix="t1105-rebind")
    try:
        origin = "http://evil.test:%d" % srv.port
        req = urllib.request.Request(
            "http://127.0.0.1:%d/msg" % srv.port,
            data=json.dumps({
                "from": "boss", "text": "rebinding-should-not-land", "to": "",
            }).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Host": "evil.test:%d" % srv.port,
                "Origin": origin,
                "X-Atman-Token": srv.launch_token(),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status, out = r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            status, out = e.code, json.loads(e.read())
        assert status == 400 and not out["ok"], out
        assert "host" in (out.get("error") or "").lower()
        assert "rebinding-should-not-land" not in _live(board)
    finally:
        srv.stop()


def test_write_requires_launch_token(board):
    run(board, "join", "boss", agent="boss")
    srv = UiServer(board, probe_prefix="t1105-token")
    try:
        status, out = srv.post(
            "/msg",
            {"from": "boss", "text": "no-token-should-not-land", "to": ""},
            token=False,
        )
        assert status == 400 and not out["ok"], out
        assert "token" in (out.get("error") or "").lower()
        assert "no-token-should-not-land" not in _live(board)

        status, out = srv.post(
            "/msg",
            {"from": "boss", "text": "wrong-token-should-not-land", "to": ""},
            token="not-the-launch-token",
        )
        assert status == 400 and not out["ok"], out
        assert "wrong-token-should-not-land" not in _live(board)

        status, out = srv.post("/msg", {"from": "boss", "text": "token-ok", "to": ""})
        assert status == 200 and out["ok"], out
        assert "token-ok" in _live(board)
        page = srv.get("/", raw=True).decode()
        assert 'X-Atman-Token' in page or "writeHeaders" in page
        assert srv.launch_token()
        assert srv.launch_token() in page
    finally:
        srv.stop()


def test_non_loopback_host_refused(board):
    env = dict(os.environ, TICKETS_DIR=str(board))
    r = subprocess.run(
        [sys.executable, str(TOOL), "ui", "--host", "0.0.0.0", "--port", str(_free_port()),
         "--parent-pid", str(os.getpid())],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert r.returncode != 0
    err = (r.stderr or "") + (r.stdout or "")
    assert "loopback" in err.lower()
    assert "0.0.0.0" in err
