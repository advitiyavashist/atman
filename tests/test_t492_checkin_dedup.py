"""T-492: packaged checkin must be the root delivery path, not a second copy.

cos-opus measured six fields dropped when cli.py rebuilt the agent record.
The fix is structural: cli.py must not define checkin/_apply at all, and both
entry points must share the flocked read-modify-write in tickets.py.
"""
import ast
import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / "src" / "ticket_board" / "cli.py"

SIX_FIELDS = {
    "harness": "cursor",
    "inbox_seen": "2026-09-07T12:00:00Z",
    "joined_at": "2026-09-07T11:00:00Z",
    "limit_until": "2026-09-12T19:41:00Z",
    "model": "grok-4.6",
    "roles": ["verification"],
}


def _load_root():
    spec = importlib.util.spec_from_file_location("tickets_t492", str(ROOT / "tickets.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_pkg():
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    import ticket_board.cli as m  # noqa: E402
    return m


@pytest.fixture(params=["root", "packaged"])
def mod(request):
    return _load_root() if request.param == "root" else _load_pkg()


def _seed(board, owner="bob", extra=None):
    rec = {
        "owner": owner,
        "inbox_seen": SIX_FIELDS["inbox_seen"],
        "joined_at": SIX_FIELDS["joined_at"],
        "harness": SIX_FIELDS["harness"],
        "model": SIX_FIELDS["model"],
        "roles": list(SIX_FIELDS["roles"]),
        "limit": {
            "at": "2026-09-07T12:00:00Z",
            "until": SIX_FIELDS["limit_until"],
            "note": "credits out",
        },
        "limit_until": SIX_FIELDS["limit_until"],
    }
    if extra:
        rec.update(extra)
    path = Path(board) / "agents" / (owner + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=2))
    return rec, path


def _survived(path):
    after = json.loads(path.read_text())
    got = {
        "harness": after.get("harness"),
        "inbox_seen": after.get("inbox_seen"),
        "joined_at": after.get("joined_at"),
        "limit_until": (after.get("limit") or {}).get("until") or after.get("limit_until"),
        "model": after.get("model"),
        "roles": after.get("roles"),
    }
    return after, got


def test_six_fields_survive_checkin_on_both_entry_points(board, mod):
    rec, path = _seed(board)
    mod.checkin(str(board), "bob")
    after, got = _survived(path)
    assert got == SIX_FIELDS, got
    assert after.get("limit") == rec["limit"]


def test_limit_set_via_tickets_limit_survives_packaged_checkin(board):
    """Acceptance (2): a limit must still be there after packaged checkin."""
    pkg = _load_pkg()
    rec, path = _seed(board, extra={"limit": None})
    rec.pop("limit", None)
    rec.pop("limit_until", None)
    path.write_text(json.dumps(rec, indent=2))

    class Args:
        agent = "bob"
        clear = False
        until = "2026-10-05"
        note = "session capped"

    pkg.cmd_limit(Args(), str(board))
    pkg.checkin(str(board), "bob")
    after = json.loads(path.read_text())
    assert after.get("limit"), after
    assert after["limit"]["until"] == "2026-10-05"
    assert after.get("inbox_seen") == SIX_FIELDS["inbox_seen"]


def test_inbox_seen_is_not_wiped_by_checkin(board, mod):
    _, path = _seed(board)
    mod.checkin(str(board), "bob")
    after = json.loads(path.read_text())
    assert after.get("inbox_seen") == SIX_FIELDS["inbox_seen"]


def test_concurrent_checkins_do_not_lose_a_write(board, monkeypatch):
    """Flock is the point: a straddling limit must survive packaged checkin."""
    monkeypatch.setenv("TICKET_AGENT", "bob")
    pkg = _load_pkg()
    owning = sys.modules[pkg.checkin.__module__]
    b = str(board)
    pkg.checkin(b, "bob")

    class Args:
        agent = "bob"
        clear = False
        until = "2026-09-12 19:41"
        note = "credits out"

    b_read = threading.Event()
    writer_done = threading.Event()
    real_rec = owning._agent_rec

    def gated_rec(bd, own):
        rec = real_rec(bd, own)
        if threading.current_thread().name == "heartbeat" and not b_read.is_set():
            b_read.set()
            writer_done.wait(1.5)
        return rec

    owning._agent_rec = gated_rec
    try:
        t = threading.Thread(target=lambda: pkg.checkin(b, "bob"), name="heartbeat")
        t.start()
        assert b_read.wait(5), "packaged checkin never reached the root read"
        owning.cmd_limit(Args(), b)
        writer_done.set()
        t.join(10)
        assert not t.is_alive(), "heartbeat thread hung"
    finally:
        owning._agent_rec = real_rec

    rec = json.loads((Path(b) / "agents" / "bob.json").read_text())
    assert rec.get("limit", {}).get("until") == "2026-09-12 19:41", rec
    assert rec.get("seen"), rec


def test_cli_py_defines_no_second_checkin_or_apply_body():
    src = CLI_PATH.read_text()
    tree = ast.parse(src)
    defined = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert "checkin" not in defined, defined
    assert "_apply" not in defined, defined
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
            "checkin",
            "_apply",
        ):
            pytest.fail("cli.py still carries %s" % node.name)


def test_packaged_checkin_is_the_root_function():
    pkg = _load_pkg()
    src = Path(pkg.checkin.__code__.co_filename).resolve()
    assert src == (ROOT / "tickets.py").resolve(), src
    assert pkg.checkin.__name__ == "checkin"


def test_python_m_ticket_board_checkin_preserves_six_fields(board):
    _, path = _seed(board)
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="bob", PYTHONPATH="%s:%s" % (
        ROOT, ROOT / "src"))
    r = subprocess.run(
        [sys.executable, "-m", "ticket_board", "here"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    _, got = _survived(path)
    assert got == SIX_FIELDS, got
