"""T-926: `spawn <seat> --stop` is scoped to the board the operator selected.

`cmd_spawn`'s stop path called `_live_watch_pids(owner)` with no board, so it
SIGTERMed every loop anywhere on the machine that carried the same seat name.
One person runs one board per repo (Steer and Atman, here) and reuses seat
names across them, so stopping a worker on one board silently killed the same
name's live loop on the other -- a board the operator never named.

Three invariants, all exercised against real child processes:

  * stop on board A leaves board B's loop for the same seat operational;
    fleet-wide stop stays reachable, but only as explicit `--all-boards`;
  * a bare pid is not identity -- a stale pid file whose number the OS has
    since recycled must not be treated as this board's watcher, and no
    unrelated process is ever signalled;
  * when `ps` cannot be read, an empty process table is "unknown", not "no
    watcher": stop says so, or acts on a pid file it actually verified.

Throwaway boards and stub loops only; never the live board.
"""
from __future__ import annotations

import importlib.util
import io
import os
import signal
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
SEAT = "t926-seat"


@pytest.fixture(scope="module")
def tk():
    spec = importlib.util.spec_from_file_location("tickets_under_test_t926", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def procs(tmp_path):
    """Live processes shaped like watch loops, plus plain unrelated processes."""
    started = []

    # `ps` only shows a command line, so the stub has to be named tickets.py
    # on a release path, exactly as _parse_watch_table sees a real loop.
    release = tmp_path / "tickets-releases" / "t926"
    release.mkdir(parents=True, exist_ok=True)
    script = release / "tickets.py"
    script.write_text("import time\nwhile True: time.sleep(3600)\n")
    plain = tmp_path / "bystander.py"
    plain.write_text("import time\nwhile True: time.sleep(3600)\n")

    def _sleeper(prog, argv_tail, cwd):
        cwd = Path(cwd)
        cwd.mkdir(parents=True, exist_ok=True)
        p = subprocess.Popen(
            [sys.executable, str(prog)] + argv_tail,
            cwd=str(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True)
        started.append(p)
        return p

    def watch(agent, cwd, with_cwd_flag=True):
        tail = ["watch", "--agent", agent, "--every", "60"]
        if with_cwd_flag:
            tail += ["--cwd", str(cwd)]
        return _sleeper(script, tail, cwd)

    def unrelated(cwd):
        return _sleeper(plain, [], cwd)

    watch.unrelated = unrelated
    try:
        yield watch
    finally:
        for p in started:
            try:
                os.kill(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait(timeout=5)


def _board(tmp_path, name):
    repo = tmp_path / name
    (repo / ".tickets" / "agents").mkdir(parents=True)
    (repo / ".tickets" / ".fixture-board").write_text("t926\n")
    return str(repo / ".tickets")


def _stop(tk, board, owner, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        tk._spawn_stop(board, owner, **kw)
    return buf.getvalue()


def _settle(fn, want, tries=60):
    for _ in range(tries):
        got = fn()
        if got == want:
            return got
        time.sleep(0.05)
    return fn()


def _gone(p, timeout=10):
    try:
        p.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def _still_running(p, settle=0.5):
    """True when `p` is still executing.

    Not `os.kill(pid, 0)`: a SIGTERMed child of this test process stays a
    zombie until it is reaped, and signalling a zombie succeeds -- which
    would report a loop we just killed as untouched.
    """
    time.sleep(settle)
    return p.poll() is None


# ---- scope: the board the operator named, and only that board ----------

def test_stop_on_one_board_leaves_the_other_boards_loop_alive(tk, procs, tmp_path):
    board_a = _board(tmp_path, "repo-a")
    board_b = _board(tmp_path, "repo-b")
    a = procs(SEAT, Path(board_a).parent)
    b = procs(SEAT, Path(board_b).parent)
    _settle(lambda: tk._live_watch_pids(SEAT, board=board_a), [a.pid])

    out = _stop(tk, board_a, SEAT)

    assert str(a.pid) in out and "stopped 1 watcher" in out
    assert str(b.pid) not in out
    assert _gone(a), "the named board's loop must stop"
    assert _still_running(b), "stopping board A killed board B's loop for the same seat"


def test_all_boards_is_the_separate_explicit_fleet_intent(tk, procs, tmp_path):
    board_a = _board(tmp_path, "repo-a")
    board_b = _board(tmp_path, "repo-b")
    a = procs(SEAT, Path(board_a).parent)
    b = procs(SEAT, Path(board_b).parent)
    _settle(lambda: sorted(tk._live_watch_pids(SEAT)), sorted([a.pid, b.pid]))

    out = _stop(tk, board_a, SEAT, all_boards=True)

    assert str(a.pid) in out and str(b.pid) in out
    assert _gone(a) and _gone(b)


def test_empty_own_board_names_the_other_board_instead_of_reaching_into_it(tk, procs, tmp_path):
    board_a = _board(tmp_path, "repo-a")
    board_b = _board(tmp_path, "repo-b")
    b = procs(SEAT, Path(board_b).parent)
    _settle(lambda: tk._live_watch_pids(SEAT, board=board_b), [b.pid])

    out = _stop(tk, board_a, SEAT)

    assert "no running watcher" in out
    assert "--all-boards" in out and str(b.pid) in out, (
        "a loop on another board must be reported, not silently stopped")
    assert _still_running(b)


def test_own_board_loop_without_a_cwd_flag_is_still_found(tk, procs, tmp_path):
    """Older releases (and `tickets watch` by hand) record no --cwd.

    T-554's contract is that own-board discovery does not depend on which
    release is running. Scoping must not reintroduce that blindness.
    """
    board = _board(tmp_path, "repo-a")
    p = procs(SEAT, Path(board).parent, with_cwd_flag=False)
    assert _settle(lambda: tk._live_watch_pids(SEAT, board=board), [p.pid]) == [p.pid]

    out = _stop(tk, board, SEAT)

    assert str(p.pid) in out and _gone(p)


def test_cli_stop_does_not_reach_the_other_board(tk, procs, tmp_path):
    """The same invariant through the real CLI, so it is provable against any tree.

    `tickets spawn <seat> --stop` on board A, with board B running the same
    seat name: A's loop exits, B's keeps running and is never named.
    """
    board_a = _board(tmp_path, "repo-a")
    board_b = _board(tmp_path, "repo-b")
    a = procs(SEAT, Path(board_a).parent)
    b = procs(SEAT, Path(board_b).parent)
    _settle(lambda: sorted(tk._live_watch_pids(SEAT)), sorted([a.pid, b.pid]))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ, TICKETS_DIR=board_a, TICKET_AGENT="t926-operator", HOME=str(home))
    env.pop("TICKETS_STOP_HOOK", None)

    r = subprocess.run([sys.executable, str(TOOL), "spawn", SEAT, "--stop"],
                       capture_output=True, text=True, env=env, cwd=str(Path(board_a).parent))

    assert r.returncode == 0, r.stderr
    assert _gone(a), r.stdout
    assert _still_running(b), (
        "spawn --stop on board A killed the same seat's loop on board B: %s" % r.stdout)


# ---- a bare pid is not identity ----------------------------------------

def test_recycled_pid_under_a_stale_pid_file_is_refused(tk, procs, tmp_path):
    """A pid file older than the process it names cannot have been written by it."""
    board = _board(tmp_path, "repo-a")
    other = procs(SEAT, tmp_path / "elsewhere")
    pid_file = Path(tk.agents_dir(board)) / (SEAT + ".watch.pid")

    pid_file.write_text(str(other.pid))
    assert tk._watch_pid_claimed_on_board(board, SEAT, other.pid), (
        "a fresh pid file is this board's own record")

    old = time.time() - 3600
    os.utime(str(pid_file), (old, old))
    assert not tk._watch_pid_claimed_on_board(board, SEAT, other.pid), (
        "a pid the OS recycled after the file was written is a different process")
    assert other.pid not in tk._live_watch_pids(SEAT, board=board)


def test_stop_never_signals_an_unrelated_process(tk, procs, tmp_path):
    """A stale pid file pointing at some other program must not get a SIGTERM."""
    board = _board(tmp_path, "repo-a")
    bystander = procs.unrelated(tmp_path)
    pid_file = Path(tk.agents_dir(board)) / (SEAT + ".watch.pid")
    pid_file.write_text(str(bystander.pid))

    out = _stop(tk, board, SEAT)

    assert "no running watcher" in out
    assert _still_running(bystander), "spawn --stop signalled a process that is not a watcher"


# ---- an unreadable process table is unknown, not empty -----------------

def test_unreadable_process_table_is_reported_not_denied(tk, procs, tmp_path, monkeypatch):
    board = _board(tmp_path, "repo-a")
    p = procs(SEAT, Path(board).parent)
    _settle(lambda: tk._live_watch_pids(SEAT, board=board), [p.pid])
    monkeypatch.setattr(tk, "_parse_watch_table", lambda: ([], False))
    monkeypatch.setattr(tk, "_process_command", lambda pid: "")

    out = _stop(tk, board, SEAT)

    assert "unverified" in out
    assert "no running watcher" not in out, (
        "an unreadable process table reported as 'no watcher' gets a duplicate loop spawned")
    assert os.path.exists(tk._stop_file(board, SEAT)), (
        "the board's stop fence is still raised; only the liveness claim is withheld")
    assert _still_running(p)


def test_unreadable_process_table_falls_back_to_a_verified_pid_file(tk, procs, tmp_path, monkeypatch):
    board = _board(tmp_path, "repo-a")
    p = procs(SEAT, Path(board).parent)
    _settle(lambda: tk._live_watch_pids(SEAT, board=board), [p.pid])
    (Path(tk.agents_dir(board)) / (SEAT + ".watch.pid")).write_text(str(p.pid))
    monkeypatch.setattr(tk, "_parse_watch_table", lambda: ([], False))

    out = _stop(tk, board, SEAT)

    assert "verified pid file" in out and str(p.pid) in out
    assert _gone(p)


def test_verified_pid_file_refuses_a_pid_that_is_not_a_watcher(tk, procs, tmp_path, monkeypatch):
    board = _board(tmp_path, "repo-a")
    bystander = procs.unrelated(tmp_path)
    (Path(tk.agents_dir(board)) / (SEAT + ".watch.pid")).write_text(str(bystander.pid))
    monkeypatch.setattr(tk, "_parse_watch_table", lambda: ([], False))

    pid, state = tk._validated_owned_watch_pid(board, SEAT)
    assert (pid, state) == (0, "none")

    out = _stop(tk, board, SEAT)
    assert "unverified" in out
    assert _still_running(bystander)
