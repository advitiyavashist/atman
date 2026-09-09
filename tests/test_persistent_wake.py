"""Persistent-session wake: `tickets join --persistent` registers a socket
endpoint outside the board; `tickets msg --to <seat>` pokes it after the
board write, so a message to an idle interactive session can actually start
a fresh turn instead of sitting unread in a file nothing polls.

Two ways of exercising this, deliberately:

  * The CLI, as a subprocess, for anything that is really about the command
    surface -- registration, the token never touching stdout/stderr, watch
    and spawn refusing to double up on a live seat.
  * `write_endpoint()` called directly, in-process, for anything that needs
    a controllable "live" pid -- `tickets join --persistent` run as a
    subprocess records ITS OWN pid, and that process has already exited by
    the time a later `tickets msg` subprocess checks liveness, so every test
    that needs a genuinely live endpoint registers one against THIS test
    process's own (very much alive) pid instead. See _tickets_module(),
    the same pattern tests/test_liveness.py uses for the same reason.

No network, no real Claude Code harness, no home-dir writes: TICKETS_CACHE_DIR
points every endpoint file at a throwaway directory per test.
"""

import importlib.util
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="", stdin="", env=None, cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID", "TERM_SESSION_ID"):
        e.pop(var, None)
    # A fresh session id per call, not a stripped-to-nothing one and not the
    # outer test process's own real one -- see tests/test_wakeup.py's `run()`
    # for the two failure modes that come from either of those. Nothing here
    # needs cross-call identity continuity (every call names its actor via
    # --owner/agent= explicitly), so a random id per call is enough.
    e["TICKET_SESSION_ID"] = uuid.uuid4().hex
    # Never let a real harness's messaging endpoint leak into a subprocess
    # that is not deliberately testing --persistent registration: if this
    # process happens to be running under one, an unrelated test poking a
    # REAL socket would be a very different kind of bug report.
    e.pop("CLAUDE_CODE_MESSAGING_SOCKET", None)
    e.pop("CLAUDE_CODE_MESSAGING_TOKEN", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(TOOL), *args], input=stdin, capture_output=True,
                          text=True, env=e, cwd=str(cwd or board.parent))


@pytest.fixture
def cache_dir(tmp_path):
    d = tmp_path / "cache"
    return str(d)


@pytest.fixture
def sock_dir():
    # AF_UNIX caps sun_path at ~104 bytes on macOS, and pytest's tmp_path
    # (buried under pytest-of-<user>/pytest-N/test-name-.../) blows straight
    # through that. A short directory directly under /tmp is the only place
    # short enough to bind a real socket in.
    d = tempfile.mkdtemp(prefix="tw", dir="/tmp")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def board(tmp_path, cache_dir):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    r = run(b, "create", "Write docs", "--role", "docs", cwd=repo, env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr
    return b


def _tickets_module():
    """Load tickets.py in-process -- used only where a test needs a
    genuinely live pid to back an endpoint. See module docstring."""
    spec = importlib.util.spec_from_file_location("tickets_under_test", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeInbox:
    """Stand-in for the harness's per-session Unix socket inbox: binds a real
    AF_UNIX socket, accepts one connection, and records every byte written to
    it before the peer closes."""

    def __init__(self, path):
        self.path = str(path)
        self.received = []
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(1)
        self._srv.settimeout(10)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            chunks = []
            try:
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
            except OSError:
                pass
        self.received.append(b"".join(chunks).decode("utf-8", "replace"))

    def wait_for_message(self, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.received:
                return self.received[-1]
            time.sleep(0.05)
        return None

    def close(self):
        try:
            self._srv.close()
        except OSError:
            pass


def dead_socket_path(sock_dir, name="dead.sock"):
    """A socket file that exists on disk but has nobody listening on it --
    bind, then close without unlisten/unlink, so connect() reports refused
    rather than hanging or succeeding."""
    path = str(Path(sock_dir) / name)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    s.close()  # file stays on disk; nothing will ever accept() on it
    return path


def dead_pid():
    """A pid that was real and is now guaranteed gone."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# ---- registration: outside the board, private, no token in output --------

def test_join_persistent_registers_endpoint_outside_board(board, cache_dir, sock_dir):
    sock_path = str(Path(sock_dir) / "alice.sock")
    token = "SEKRIT-TOKEN-DO-NOT-LEAK-12345"
    r = run(board, "join", "alice", "--roles", "backend", "--persistent",
            env={"TICKETS_CACHE_DIR": cache_dir,
                 "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
                 "CLAUDE_CODE_MESSAGING_TOKEN": token})
    assert r.returncode == 0, r.stderr
    assert "persistent: wake endpoint registered" in r.stdout

    # The endpoint lives under the cache dir, never under .tickets.
    endpoint_files = list(Path(cache_dir).rglob("alice.json"))
    assert len(endpoint_files) == 1, endpoint_files
    ep = json.loads(endpoint_files[0].read_text())
    assert ep["socket"] == sock_path
    assert ep["token"] == token
    assert ep["seat"] == "alice"
    assert isinstance(ep["pid"], int)

    # Nothing under the board directory -- which is what would get committed
    # in real use -- contains the token.
    for p in Path(board).rglob("*"):
        if p.is_file():
            assert token not in p.read_text(errors="ignore"), p

    # Private by construction: 0700 dirs, 0600 file.
    assert stat.S_IMODE(endpoint_files[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(endpoint_files[0].parent.stat().st_mode) == 0o700


def test_join_persistent_without_socket_env_registers_nothing(board, cache_dir):
    r = run(board, "join", "bob", "--roles", "backend", "--persistent",
            env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr
    assert "no wake endpoint recorded" in r.stdout
    assert not list(Path(cache_dir).rglob("bob.json"))


def test_token_never_appears_in_any_output(board, cache_dir, sock_dir):
    sock_path = str(Path(sock_dir) / "carol.sock")
    token = "TOKEN-MUST-NEVER-BE-PRINTED-98765"
    r1 = run(board, "join", "carol", "--roles", "backend", "--persistent",
             env={"TICKETS_CACHE_DIR": cache_dir,
                  "CLAUDE_CODE_MESSAGING_SOCKET": sock_path,
                  "CLAUDE_CODE_MESSAGING_TOKEN": token})
    assert token not in r1.stdout and token not in r1.stderr

    # Also true once a live endpoint exists and other commands report on it.
    tk = _tickets_module()
    os.environ["TICKETS_CACHE_DIR"] = cache_dir
    try:
        tk.write_endpoint(str(board), "carol", sock_path, token, os.getpid())
    finally:
        os.environ.pop("TICKETS_CACHE_DIR", None)

    for args in (["self"], ["who"], ["watch", "--agent", "carol", "--once", "--dry-run"]):
        r = run(board, *args, env={"TICKETS_CACHE_DIR": cache_dir})
        assert token not in r.stdout and token not in r.stderr, (args, r.stdout, r.stderr)

    r2 = run(board, "msg", "task: hi carol", "--to", "carol", "--owner", "dana",
             env={"TICKETS_CACHE_DIR": cache_dir})
    assert token not in r2.stdout and token not in r2.stderr


# ---- msg --to pokes a live endpoint ---------------------------------------

def test_msg_delivers_payload_to_live_seats_socket(board, cache_dir, sock_dir):
    tk = _tickets_module()
    sock_path = str(Path(sock_dir) / "erin.sock")
    inbox = FakeInbox(sock_path)
    try:
        os.environ["TICKETS_CACHE_DIR"] = cache_dir
        tk.write_endpoint(str(board), "erin", sock_path, "tok-erin", os.getpid())
    finally:
        os.environ.pop("TICKETS_CACHE_DIR", None)

    r = run(board, "msg", "task: please look at T-001", "--to", "erin", "--owner", "dana",
            env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr
    assert "wake: erin -> woken" in r.stdout

    delivered = inbox.wait_for_message()
    assert delivered is not None, "the fake inbox never received a connection"
    assert "tok-erin" in delivered  # the auth line went out
    assert "please look at T-001" in delivered
    assert "dana" in delivered
    inbox.close()


def test_msg_without_task_does_not_attempt_a_wake(board, cache_dir, sock_dir):
    tk = _tickets_module()
    sock_path = str(Path(sock_dir) / "frank.sock")
    inbox = FakeInbox(sock_path)
    try:
        os.environ["TICKETS_CACHE_DIR"] = cache_dir
        tk.write_endpoint(str(board), "frank", sock_path, "tok-frank", os.getpid())
    finally:
        os.environ.pop("TICKETS_CACHE_DIR", None)

    # An ordinary DM: `_message_wakes` says this does not wake the addressee
    # (the same rule `watch`'s wake gates already use), so no wake line and
    # no connection at all -- reusing the existing rule, not inventing one.
    r = run(board, "msg", "just an fyi, no rush", "--to", "frank", "--owner", "dana",
            env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr
    assert "wake:" not in r.stdout
    assert inbox.wait_for_message(timeout=1) is None
    inbox.close()


def test_msg_reports_refused_when_socket_is_dead_but_board_write_still_succeeds(board, cache_dir, sock_dir):
    tk = _tickets_module()
    sock_path = dead_socket_path(sock_dir, "gina.sock")
    try:
        os.environ["TICKETS_CACHE_DIR"] = cache_dir
        tk.write_endpoint(str(board), "gina", sock_path, "tok-gina", os.getpid())
    finally:
        os.environ.pop("TICKETS_CACHE_DIR", None)

    r = run(board, "msg", "task: still need this", "--to", "gina", "--owner", "dana",
            env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr
    assert "wake: gina -> refused" in r.stdout

    # Board first, always: the message is on the board regardless of the
    # dead socket.
    msgs = (board / "messages.jsonl").read_text()
    assert "still need this" in msgs


def test_msg_reports_no_live_endpoint_when_seat_never_registered_one(board, cache_dir):
    r = run(board, "msg", "task: anybody home?", "--to", "hank", "--owner", "dana",
            env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr
    assert "wake: hank -> no live endpoint" in r.stdout
    assert "anybody home?" in (board / "messages.jsonl").read_text()


# ---- staleness: cleaned up, never hangs ------------------------------------

@pytest.mark.parametrize("break_how", ["dead_pid", "missing_socket"])
def test_stale_endpoint_is_cleaned_up_and_does_not_hang(board, cache_dir, sock_dir, break_how):
    tk = _tickets_module()
    if break_how == "dead_pid":
        pid = dead_pid()
        sock_path = str(Path(sock_dir) / "ivan.sock")
        FakeInbox(sock_path).close()  # exists on disk; irrelevant, pid check short-circuits
    else:
        pid = os.getpid()  # very much alive
        sock_path = str(Path(sock_dir) / "does-not-exist.sock")  # never created

    try:
        os.environ["TICKETS_CACHE_DIR"] = cache_dir
        tk.write_endpoint(str(board), "ivan", sock_path, "tok-ivan", pid)
    finally:
        os.environ.pop("TICKETS_CACHE_DIR", None)

    endpoint_file = next(Path(cache_dir).rglob("ivan.json"))
    assert endpoint_file.exists()

    t0 = time.time()
    r = run(board, "msg", "task: cleanup check", "--to", "ivan", "--owner", "dana",
            env={"TICKETS_CACHE_DIR": cache_dir})
    elapsed = time.time() - t0
    assert r.returncode == 0, r.stderr
    assert "wake: ivan -> endpoint stale" in r.stdout
    assert elapsed < 10, "a stale endpoint must be detected locally, never by waiting on a hung connect"
    assert not endpoint_file.exists(), "a stale record must be removed, not just skipped"


# ---- two seats, independently -------------------------------------------

def test_two_persistent_seats_are_woken_independently(board, cache_dir, sock_dir):
    tk = _tickets_module()
    sock_a = str(Path(sock_dir) / "june.sock")
    sock_b = str(Path(sock_dir) / "kip.sock")
    inbox_a, inbox_b = FakeInbox(sock_a), FakeInbox(sock_b)
    try:
        os.environ["TICKETS_CACHE_DIR"] = cache_dir
        tk.write_endpoint(str(board), "june", sock_a, "tok-june", os.getpid())
        tk.write_endpoint(str(board), "kip", sock_b, "tok-kip", os.getpid())
    finally:
        os.environ.pop("TICKETS_CACHE_DIR", None)

    r1 = run(board, "msg", "task: for june only", "--to", "june", "--owner", "dana",
             env={"TICKETS_CACHE_DIR": cache_dir})
    assert "wake: june -> woken" in r1.stdout
    got_a = inbox_a.wait_for_message()
    assert got_a and "for june only" in got_a
    assert inbox_b.wait_for_message(timeout=1) is None, "kip's socket must not see june's message"

    r2 = run(board, "msg", "task: for kip only", "--to", "kip", "--owner", "dana",
             env={"TICKETS_CACHE_DIR": cache_dir})
    assert "wake: kip -> woken" in r2.stdout
    got_b = inbox_b.wait_for_message()
    assert got_b and "for kip only" in got_b
    assert "for june only" not in got_b

    inbox_a.close()
    inbox_b.close()


# ---- watch / spawn skip a live seat ---------------------------------------

def test_watch_skips_seat_with_live_endpoint(board, cache_dir, sock_dir):
    tk = _tickets_module()
    sock_path = str(Path(sock_dir) / "leo.sock")
    FakeInbox(sock_path).close()
    try:
        os.environ["TICKETS_CACHE_DIR"] = cache_dir
        tk.write_endpoint(str(board), "leo", sock_path, "tok-leo", os.getpid())
    finally:
        os.environ.pop("TICKETS_CACHE_DIR", None)

    r = run(board, "join", "leo", "--roles", "backend", env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr

    r = run(board, "watch", "--agent", "leo", "--once", "--dry-run", env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr
    assert "skip:" in r.stdout and "persistent session" in r.stdout
    assert "dry-run; would execute" not in r.stdout

    # --force steamrolls it, same flag that already overrides an empty wake gate.
    r = run(board, "watch", "--agent", "leo", "--once", "--dry-run", "--force",
            env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr
    assert "skip:" not in r.stdout


def test_spawn_skips_seat_with_live_endpoint(board, cache_dir, sock_dir):
    tk = _tickets_module()
    sock_path = str(Path(sock_dir) / "moe.sock")
    FakeInbox(sock_path).close()
    try:
        os.environ["TICKETS_CACHE_DIR"] = cache_dir
        tk.write_endpoint(str(board), "moe", sock_path, "tok-moe", os.getpid())
    finally:
        os.environ.pop("TICKETS_CACHE_DIR", None)

    r = run(board, "spawn", "moe", env={"TICKETS_CACHE_DIR": cache_dir})
    assert r.returncode == 0, r.stderr
    assert "skip:" in r.stdout and "persistent session" in r.stdout
    # Nothing was started: no worktree, no watcher pid file.
    assert not (Path(board).parent / ".worktrees" / "moe").exists()
    assert not (board / "agents" / "moe.watch.pid").exists()
