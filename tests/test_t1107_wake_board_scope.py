"""T-1107: a scratch board must not wake the session that spawned it.

The defect shape, reproduced 2026-09-19: an agent running inside a live Claude
Code session tested the wake path on a throwaway board, and the poke landed in
the operator's real window. The transport (CLAUDE_CODE_MESSAGING_SOCKET, a
Codex thread id, a Cursor conversation id) is process-global -- every child
inherits it -- so a board that never belonged to that session could register
it and poke it.

The isolation such a test is supposed to use (its own HOME, its own
TICKETS_CACHE_DIR) is exactly what defeats a cache-based check: the scratch
cache is empty by construction, so "is this session bound elsewhere?" answers
no and the wake goes through. These tests therefore all give the scratch board
a cache root and a HOME of its OWN, never shared with the board that models
the live session.

Safety: the only socket any test here points at is a decoy listener it created
itself, under a short /tmp dir (AF_UNIX path limit), and the conftest guard has
already removed the suite runner's own transport from the environment.
"""
import importlib.util
import json
import os
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

import session_adapters as sa
from test_wakeup import board, run  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


class DecoyInbox:
    """Stands in for a live session's injector socket, and counts frames."""

    def __init__(self, path):
        self.path = str(path)
        self.frames = []
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(8)
        self._srv.settimeout(0.2)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                continue
            with conn:
                conn.settimeout(2)
                buf = b""
                try:
                    while True:
                        data = conn.recv(4096)
                        if not data:
                            break
                        buf += data
                except OSError:
                    pass
            for line in buf.split(b"\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if obj.get("type") != "auth":
                    self.frames.append(obj)

    def wait_for(self, n, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline and len(self.frames) < n:
            time.sleep(0.02)
        return [f["message"]["content"] for f in self.frames]

    def nothing_arrived(self, settle=0.4):
        time.sleep(settle)
        return self.frames == []

    def close(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        self._thread.join(timeout=2)


@pytest.fixture
def decoy():
    d = tempfile.mkdtemp(prefix="t1107", dir="/tmp")
    inbox = DecoyInbox(Path(d) / "live.sock")
    try:
        yield inbox
    finally:
        inbox.close()
        shutil.rmtree(d, ignore_errors=True)


def _make_board(root, name):
    tickets = Path(root) / name / ".tickets"
    (tickets / "agents").mkdir(parents=True)
    return str(tickets)


def _inherit_session(monkeypatch, decoy, announced_board=""):
    """Put a live session's transport in the environment, as a child sees it."""
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", decoy.path)
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "tok")
    monkeypatch.setenv("TICKET_SESSION_PID", str(os.getpid()))
    if announced_board:
        monkeypatch.setenv(sa.TRANSPORT_BOARD_ENV, str(announced_board))
    else:
        monkeypatch.delenv(sa.TRANSPORT_BOARD_ENV, raising=False)


def _isolate(monkeypatch, root, name="cache"):
    """The isolation a wake test is supposed to use: own HOME, own cache."""
    home = Path(root) / (name + "-home")
    cache = Path(root) / name
    home.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TICKETS_CACHE_DIR", str(cache))
    return str(cache)


def _pretend_real_session_env(monkeypatch, root):
    """Model an operator's own (non-isolated) environment inside tmp_path.

    _real_home() reads the password database, so it cannot be faked with
    $HOME; patching it is how a test gets the "nothing is isolated here" path
    without touching the operator's real HOME or ~/.cache/atman.
    """
    home = Path(root) / "real-home"
    (home / ".cache" / "atman").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sa, "_real_home", lambda: str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TICKETS_CACHE_DIR", str(home / ".cache" / "atman"))
    return str(home)


def _claude_record(board_path, sock, seat="lead", at="2026-09-19T08:00:00Z"):
    return {"seat": seat, "agent_id": seat, "provider": "claude", "mode": "native",
            "socket": sock, "token": "tok", "pid": os.getpid(), "at": at,
            "capabilities": {"native_inject": True},
            "board": sa.board_key(board_path), "lease_id": "lease-" + seat,
            "fence": 1, "heartbeat_epoch": 10 ** 12}


# ---- the real defect shape ---------------------------------------------


def test_isolated_scratch_board_cannot_register_or_wake_the_inherited_session(
        tmp_path, monkeypatch, decoy):
    """Temp HOME + temp TICKETS_CACHE_DIR + an inherited socket: no frame."""
    live = _make_board(tmp_path, "live-repo")
    scratch = _make_board(tmp_path, "scratch-repo")
    # The live session announced its own board; the child inherits that
    # announcement unchanged, which is why it names somebody else's board.
    _inherit_session(monkeypatch, decoy, announced_board=live)
    cache = _isolate(monkeypatch, tmp_path, "scratch-cache")

    reg = sa.register_persistent(scratch, "lead", "claude", "2026-09-19T09:00:00Z")
    assert reg.get("ok") is False
    assert "inherited claude session transport" in reg.get("reason", "")
    assert sa.read_endpoint(scratch, "lead") is None
    assert list(Path(cache).rglob("*.json")) == []

    # The pre-fix leak shape: a record that stamped the scratch board as its
    # own owner, in a cache no other board shares.
    sa.write_endpoint(scratch, "lead", _claude_record(scratch, decoy.path))
    label = sa.wake_seat(scratch, "lead", "scratch must not land", harness="claude")
    assert "inherited claude session transport" in label
    # Item 4: the "is it online" surfaces have to say the same thing.
    assert sa.has_live_native_session(scratch, "lead") is False
    assert sa.native_wake_online(scratch, "lead") is False
    assert sa.public_adapter_state(
        scratch, "lead", "claude", False, True)["adapter_native_online"] is False
    assert decoy.nothing_arrived()


def test_isolated_board_without_the_socket_stamp_still_cannot_wake(
        tmp_path, monkeypatch, decoy):
    """A record with no board stamp at all (pre-change) is not a loophole."""
    scratch = _make_board(tmp_path, "scratch-repo")
    _inherit_session(monkeypatch, decoy)
    _isolate(monkeypatch, tmp_path, "scratch-cache")
    record = _claude_record(scratch, decoy.path)
    record.pop("board")
    sa.write_endpoint(scratch, "lead", record)

    assert "refused" in sa.wake_seat(scratch, "lead", "no", harness="claude")
    assert decoy.nothing_arrived()


def test_codex_and_cursor_transports_are_refused_the_same_way(tmp_path, monkeypatch):
    scratch = _make_board(tmp_path, "scratch-repo")
    _isolate(monkeypatch, tmp_path, "scratch-cache")
    monkeypatch.setenv("TICKET_SESSION_PID", str(os.getpid()))
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-from-the-parent")
    monkeypatch.setenv("CURSOR_CONVERSATION_ID", "conv-from-the-parent")
    for harness in ("codex", "cursor"):
        reg = sa.register_persistent(scratch, "lead", harness, "now")
        assert reg.get("ok") is False, harness
        assert "inherited %s session transport" % harness in reg.get("reason", "")


# ---- ownership is not first-registrar ----------------------------------


def test_scratch_board_loses_whichever_order_and_never_locks_the_real_board_out(
        tmp_path, monkeypatch, decoy):
    live = _make_board(tmp_path, "live-repo")
    scratch = _make_board(tmp_path, "scratch-repo")

    # 1. The scratch board goes first, with its own cache, and is refused.
    _inherit_session(monkeypatch, decoy, announced_board=live)
    _isolate(monkeypatch, tmp_path, "scratch-cache")
    early = sa.register_persistent(scratch, "lead", "claude", "2026-09-19T08:00:00Z")
    assert early.get("ok") is False
    # It also left a hand-written record behind, stamped for itself.
    sa.write_endpoint(scratch, "lead", _claude_record(scratch, decoy.path))

    # 2. The session's own board registers afterwards and is NOT locked out.
    _isolate(monkeypatch, tmp_path, "live-cache")
    reg = sa.register_persistent(live, "lead", "claude", "2026-09-19T10:00:00Z")
    assert reg.get("ok") is True, reg
    assert sa.has_live_native_session(live, "lead") is True
    assert sa.wake_seat(live, "lead", "live board may wake",
                        harness="claude") == "delivered-unconfirmed"
    assert decoy.wait_for(1) == ["live board may wake"]

    # 3. And the scratch board still cannot, after the fact.
    _isolate(monkeypatch, tmp_path, "scratch-cache")
    assert "refused" in sa.wake_seat(scratch, "lead", "still no", harness="claude")
    assert decoy.nothing_arrived() is False and len(decoy.frames) == 1


def test_a_board_that_announces_the_transport_may_use_it(tmp_path, monkeypatch, decoy):
    """The escape hatch is board-scoped, so inheriting it cannot help."""
    mine = _make_board(tmp_path, "my-repo")
    _isolate(monkeypatch, tmp_path, "cache")
    _inherit_session(monkeypatch, decoy, announced_board=mine)
    assert sa.register_persistent(mine, "lead", "claude", "now").get("ok") is True
    assert sa.wake_seat(mine, "lead", "mine", harness="claude") == "delivered-unconfirmed"
    assert len(decoy.wait_for(1)) == 1


# ---- regressions the first attempt introduced --------------------------


def test_symlinked_board_path_registers_and_wakes(tmp_path, monkeypatch, decoy):
    real = _make_board(tmp_path, "real-repo")
    link = tmp_path / "linked-repo"
    link.symlink_to(Path(real).parent)
    linked_board = str(link / ".tickets")
    _isolate(monkeypatch, tmp_path, "cache")
    _inherit_session(monkeypatch, decoy, announced_board=real)

    reg = sa.register_persistent(linked_board, "lead", "claude", "t1")
    assert reg.get("ok") is True, reg
    assert sa.read_endpoint(linked_board, "lead")["board"] == sa.board_key(real)
    assert sa.wake_seat(linked_board, "lead", "through the link",
                        harness="claude") == "delivered-unconfirmed"
    # Re-register (same session, same board by realpath) through the link.
    monkeypatch.setenv("TICKETS_SESSION_LEASE", reg["lease_id"])
    assert sa.register_persistent(linked_board, "lead", "claude", "t2").get("ok") is True
    monkeypatch.delenv("TICKETS_SESSION_LEASE")
    # And through the real path: same board, so the stamp must not refuse it.
    assert sa.wake_refusal(real, "lead", sa.read_endpoint(linked_board, "lead")) == ""
    assert len(decoy.wait_for(1)) == 1


def test_moved_repo_can_re_register_and_wake(tmp_path, monkeypatch, decoy):
    before = _make_board(tmp_path, "before-move")
    _isolate(monkeypatch, tmp_path, "cache")
    _inherit_session(monkeypatch, decoy, announced_board=before)
    assert sa.register_persistent(before, "lead", "claude", "t1").get("ok") is True

    after = tmp_path / "after-move"
    os.rename(Path(before).parent, after)
    moved = str(after / ".tickets")
    monkeypatch.setenv(sa.TRANSPORT_BOARD_ENV, moved)
    reg = sa.register_persistent(moved, "lead", "claude", "t2")
    assert reg.get("ok") is True, reg
    assert sa.wake_seat(moved, "lead", "after the move",
                        harness="claude") == "delivered-unconfirmed"
    assert len(decoy.wait_for(1)) == 1


def test_one_live_session_may_hold_two_boards(tmp_path, monkeypatch, decoy):
    """Stated contract: it may, and both boards can wake it.

    An operator's session joined to a product repo and to an ops repo is one
    session on two boards; refusing the second board stranded the seat with
    "no live endpoint" and nothing to recover with.
    """
    _pretend_real_session_env(monkeypatch, tmp_path)
    one = _make_board(tmp_path, "repo-one")
    two = _make_board(tmp_path, "repo-two")
    # No announcement: an operator's own environment needs none.
    _inherit_session(monkeypatch, decoy)

    assert sa.register_persistent(one, "lead", "claude", "t1").get("ok") is True
    assert sa.register_persistent(two, "lead", "claude", "t2").get("ok") is True
    assert sa.wake_seat(one, "lead", "from one", harness="claude") == "delivered-unconfirmed"
    assert sa.wake_seat(two, "lead", "from two", harness="claude") == "delivered-unconfirmed"
    assert sorted(decoy.wait_for(2)) == ["from one", "from two"]


def test_an_isolated_session_may_announce_two_boards(tmp_path, monkeypatch, decoy):
    one = _make_board(tmp_path, "repo-one")
    two = _make_board(tmp_path, "repo-two")
    third = _make_board(tmp_path, "repo-three")
    _isolate(monkeypatch, tmp_path, "cache")
    _inherit_session(monkeypatch, decoy,
                     announced_board=os.pathsep.join([one, two]))
    assert sa.register_persistent(one, "lead", "claude", "t1").get("ok") is True
    assert sa.register_persistent(two, "lead", "claude", "t2").get("ok") is True
    assert sa.register_persistent(third, "lead", "claude", "t3").get("ok") is False
    assert sa.wake_seat(one, "lead", "from one", harness="claude") == "delivered-unconfirmed"
    assert sa.wake_seat(two, "lead", "from two", harness="claude") == "delivered-unconfirmed"
    assert sorted(decoy.wait_for(2)) == ["from one", "from two"]


def test_dead_foreign_record_does_not_block_this_board_forever(tmp_path, monkeypatch, decoy):
    here = _make_board(tmp_path, "here-repo")
    elsewhere = _make_board(tmp_path, "elsewhere-repo")
    _isolate(monkeypatch, tmp_path, "cache")
    _inherit_session(monkeypatch, decoy, announced_board=here)

    # A long-dead record for another board, sitting in this board's cache.
    dead = _claude_record(elsewhere, str(tmp_path / "gone.sock"), at="2020-01-01T00:00:00Z")
    dead["pid"] = 999999999
    sa.write_endpoint(here, "lead", dead)
    assert sa.wake_seat(here, "lead", "nope", harness="claude") in (
        "endpoint stale (removed)", "no live endpoint")
    assert sa.has_live_native_session(here, "lead") is False

    # ...and the seat can recover on its own board.
    assert sa.register_persistent(here, "lead", "claude", "now").get("ok") is True
    assert sa.wake_seat(here, "lead", "recovered", harness="claude") == "delivered-unconfirmed"
    assert len(decoy.wait_for(1)) == 1


def test_live_foreign_stamp_is_refused_not_delivered(tmp_path, monkeypatch, decoy):
    """A record copied into another board's cache still cannot poke."""
    here = _make_board(tmp_path, "here-repo")
    elsewhere = _make_board(tmp_path, "elsewhere-repo")
    _isolate(monkeypatch, tmp_path, "cache")
    _inherit_session(monkeypatch, decoy, announced_board=here)
    sa.write_endpoint(here, "lead", _claude_record(elsewhere, decoy.path))
    label = sa.wake_seat(here, "lead", "copied", harness="claude")
    assert "registered for another board" in label
    assert decoy.nothing_arrived()


# ---- the scrub ---------------------------------------------------------


def test_conftest_guard_removed_the_suite_runners_own_transport():
    for var in sa.AMBIENT_TRANSPORT_VARS + (sa.TRANSPORT_BOARD_ENV,):
        assert var not in os.environ, var


def test_quickstart_gate_env_scrubs_the_session_transport(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("tickets_t1107", ROOT / "tickets.py")
    tickets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tickets)
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/decoy/never-used.sock")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "tok")
    monkeypatch.setenv("CODEX_THREAD_ID", "thread")
    monkeypatch.setenv("CURSOR_CONVERSATION_ID", "conv")
    monkeypatch.setenv(sa.TRANSPORT_BOARD_ENV, "/somebody/elses/.tickets")
    env = tickets._gate_env(str(tmp_path), str(tmp_path / ".tickets"), seat="alice")
    for var in sa.AMBIENT_TRANSPORT_VARS + (sa.TRANSPORT_BOARD_ENV,):
        assert var not in env, var
    assert env["TICKETS_CACHE_DIR"] == os.path.join(str(tmp_path), "cache")


def test_wakeup_run_does_not_pass_the_parent_socket_to_children(board, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/parent/live.sock")
    monkeypatch.setenv("TICKETS_CACHE_DIR", str(tmp_path / "cache"))
    run(board, "join", "lead", "--persistent", "--wake-mode", "continuous", agent="lead")
    ep = sa.read_endpoint(str(board), "lead")
    assert (ep or {}).get("socket", "") != "/parent/live.sock"


def test_supervisor_launch_env_scrubs_parent_transport(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("tickets_t1114", ROOT / "tickets.py")
    tickets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tickets)
    for var in sa.AMBIENT_TRANSPORT_VARS + (sa.TRANSPORT_BOARD_ENV,):
        monkeypatch.setenv(var, "parent-transport")
    env = tickets._supervisor_launch_env(str(tmp_path / ".tickets"), "child")
    for var in sa.AMBIENT_TRANSPORT_VARS + (sa.TRANSPORT_BOARD_ENV,):
        assert var not in env, var
    assert env["TICKET_AGENT"] == env["TICKET_SEAT"] == "child"
    assert env["TICKET_SESSION_ID"].startswith("launch:child:")


@pytest.mark.parametrize("lookup", ["missing-pwd", "unmapped-uid", "empty-home"])
def test_unknown_account_home_refuses_unannounced_transport(
        tmp_path, monkeypatch, decoy, lookup):
    from types import SimpleNamespace

    def getpwuid(uid):
        if lookup == "unmapped-uid":
            raise KeyError(uid)
        return SimpleNamespace(pw_dir="")

    monkeypatch.setattr(sa, "pwd", None if lookup == "missing-pwd" else
                        SimpleNamespace(getpwuid=getpwuid))
    scratch = _make_board(tmp_path, "scratch")
    # Even a cache matching HOME must not turn an unknown account home into
    # evidence that this is the operator's own environment.
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TICKETS_CACHE_DIR", str(home / ".cache" / "atman"))
    _inherit_session(monkeypatch, decoy)
    assert sa._real_home() == ""
    assert "unknown account HOME" in sa.isolated_board_env()
    reg = sa.register_persistent(scratch, "lead", "claude", "now")
    assert reg.get("ok") is False
    assert "unknown account HOME" in reg.get("reason", "")
    assert sa.read_endpoint(scratch, "lead") is None
    sa.write_endpoint(scratch, "lead", _claude_record(scratch, decoy.path))
    assert "refused" in sa.wake_seat(scratch, "lead", "no", harness="claude")
    assert decoy.nothing_arrived()
    # Explicit board provenance remains available on accounts without pwd.
    monkeypatch.setenv(sa.TRANSPORT_BOARD_ENV, scratch)
    assert sa.borrowed_transport(scratch, sa.ambient_session_key("claude")) == ""
