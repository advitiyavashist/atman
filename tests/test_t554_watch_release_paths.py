"""T-554: watch-loop discovery must not depend on which release is asking.

`_live_watch_pids` used to keep a `ps` row only when `realpath(__file__)` of
the INVOKING tool appeared in the command line. Every release recut therefore
blinded the tool to the whole running fleet at once: loops still executing an
older `tickets-releases/<sha>/tickets.py` (or the `~/.claude/tools/tickets.py`
shim) matched nothing, so `spawn <seat> --stop` printed "no running watcher"
and the next `spawn <seat>` started a DUPLICATE loop beside the live one.

Reproduced on the live box at b738f1d: four seats (cursor-fable, cursor-modal,
optimizer, opus-authz) were running on 0f21ae7 / 21ca63c and `spawn --list`
showed "-" for every one of them.
"""
from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"

SHA_A = "0f21ae778a0ff8d47a057567b22ea306629897db"
SHA_B = "21ca63c367be2dcced542132b0a941147f33ced6"
SEAT = "t554-seat"


@pytest.fixture(scope="module")
def tk():
    spec = importlib.util.spec_from_file_location("tickets_under_test_t554", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- the predicate, on command lines ps actually prints -----------------

def _release_cmd(sha, agent, cwd="/repo"):
    return ("/opt/homebrew/bin/python3 "
            "/Users/<operator>/.claude/tools/tickets-releases/%s/tickets.py watch "
            "--agent %s --every 60 --cwd %s "
            '--exec claude -p "$(tickets prompt)" --model opus '
            "--run-timeout 90 --prompt-kind  --heartbeat 0" % (sha, agent, cwd))


def test_two_foreign_release_paths_are_recognised(tk):
    for sha in (SHA_A, SHA_B):
        assert tk._watch_cmd_agent(_release_cmd(sha, "cursor-modal")) == "cursor-modal"


def test_shim_path_is_recognised(tk):
    cmd = ("/usr/bin/python3 /Users/<operator>/.claude/tools/tickets.py watch "
           "--agent optimizer --every 60 --cwd /repo")
    assert tk._watch_cmd_agent(cmd) == "optimizer"


def test_repo_checkout_path_is_recognised(tk):
    cmd = "python3 /Users/<operator>/Downloads/atman/tickets.py watch --agent grok-worker --cwd /repo"
    assert tk._watch_cmd_agent(cmd) == "grok-worker"


def test_neither_release_is_the_invoking_tool(tk):
    """Teeth: if either fake path happened to be this checkout, the tests above
    would pass on the pre-fix realpath() match and prove nothing."""
    tool = tk._tickets_tool_path()
    for sha in (SHA_A, SHA_B):
        assert "/tickets-releases/%s/" % sha not in tool


@pytest.mark.parametrize("cmd", [
    "/usr/bin/python3 /Users/<operator>/.claude/tools/tickets.py spawn cursor-modal --stop",
    "/usr/bin/python3 /some/where/tickets.py next --role backend",
    "grep -n tickets.py watch --agent optimizer",
    "/usr/bin/python3 /some/where/other.py watch --agent optimizer",
    "",
])
def test_non_watch_command_lines_are_rejected(tk, cmd):
    assert tk._watch_cmd_agent(cmd) == ""


def test_exec_payload_cannot_hijack_the_agent_name(tk):
    """The real script token is argv[1]; a `tickets.py watch --agent X` string
    quoted inside --exec must not win the match."""
    cmd = (_release_cmd(SHA_A, "cursor-modal")
           + " --exec-extra /x/tickets.py watch --agent IMPOSTOR")
    assert tk._watch_cmd_agent(cmd) == "cursor-modal"


def test_unbalanced_quote_does_not_raise(tk):
    cmd = _release_cmd(SHA_A, "composer").replace('"$(tickets prompt)"', '"$(tickets prompt)')
    assert tk._watch_cmd_agent(cmd) == "composer"


# ---- the process table, with two fake release paths --------------------

def _fake_release(root, sha):
    d = root / "tickets-releases" / sha
    d.mkdir(parents=True)
    p = d / "tickets.py"
    p.write_text("import time, sys\nwhile True: time.sleep(3600)\n")
    return p


@pytest.fixture
def loops(tmp_path):
    """Two live processes shaped exactly like watch loops on two OTHER releases."""
    started = []

    def start(sha, agent, cwd):
        script = _fake_release(tmp_path, sha)
        p = subprocess.Popen(
            [sys.executable, str(script), "watch", "--agent", agent,
             "--every", "60", "--cwd", str(cwd)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True)
        started.append(p)
        return p

    try:
        yield start
    finally:
        for p in started:
            try:
                os.kill(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait(timeout=5)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / ".tickets" / "agents").mkdir(parents=True)
    return r


def _settle(fn, want, tries=40):
    for _ in range(tries):
        got = fn()
        if got == want:
            return got
        time.sleep(0.05)
    return fn()


def test_live_pids_finds_loops_on_other_release_paths(tk, loops, repo, tmp_path):
    a = loops(SHA_A, SEAT, repo)
    b = loops(SHA_B, SEAT, repo)
    board = str(repo / ".tickets")
    got = _settle(lambda: tk._live_watch_pids(SEAT, board=board), sorted([a.pid, b.pid]))
    assert got == sorted([a.pid, b.pid]), (
        "spawn --stop / the duplicate guard are blind to loops on another release path")


def test_cwd_board_filter_still_excludes_another_repo(tk, loops, repo, tmp_path):
    mine = loops(SHA_A, SEAT, repo)
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    theirs = loops(SHA_B, SEAT, other_repo)
    board = str(repo / ".tickets")
    got = _settle(lambda: tk._live_watch_pids(SEAT, board=board), [mine.pid])
    assert got == [mine.pid]
    assert theirs.pid not in got
    # fleet-wide (no board) still sees both -- that is what --stop uses
    allpids = _settle(lambda: tk._live_watch_pids(SEAT), sorted([mine.pid, theirs.pid]))
    assert allpids == sorted([mine.pid, theirs.pid])


def test_atman_cwd_counts_when_board_pid_file_matches(tk, loops, repo, tmp_path):
    """Steer board + Atman worktree: --cwd is not under the board repo, but
    agents/<seat>.watch.pid on this board still identifies the live loop."""
    other_repo = tmp_path / "atman-worktree"
    other_repo.mkdir()
    theirs = loops(SHA_B, SEAT, other_repo)
    board = str(repo / ".tickets")
    (repo / ".tickets" / "agents" / (SEAT + ".watch.pid")).write_text(str(theirs.pid))
    got = _settle(lambda: tk._live_watch_pids(SEAT, board=board), [theirs.pid])
    assert got == [theirs.pid]
    stray = tmp_path / "unrelated"
    stray.mkdir()
    ghost = loops(SHA_A, SEAT, stray)
    assert ghost.pid not in tk._live_watch_pids(SEAT, board=board)


def test_owner_filter_still_separates_seats(tk, loops, repo):
    mine = loops(SHA_A, SEAT, repo)
    loops(SHA_B, SEAT + "-other", repo)
    board = str(repo / ".tickets")
    assert _settle(lambda: tk._live_watch_pids(SEAT, board=board), [mine.pid]) == [mine.pid]


# ---- run boundary: absence of the T-237 run file is not "idle" ---------

def _write_run(repo, owner, rec):
    import json
    p = repo / ".tickets" / "agents" / (owner + ".run")
    p.write_text(json.dumps(rec))


def test_run_file_is_authoritative_when_it_matches_the_pid(tk, repo):
    board = str(repo / ".tickets")
    _write_run(repo, SEAT, {"pid": 4242, "active": True, "run": 3})
    assert tk._watcher_run_active(board, SEAT, 4242) is True
    _write_run(repo, SEAT, {"pid": 4242, "active": False, "run": 3, "rc": 0})
    assert tk._watcher_run_active(board, SEAT, 4242) is False


def test_missing_run_file_falls_back_to_child_process(tk, repo):
    """A loop on a pre-T-237 release writes no agents/<seat>.run at all, so its
    absence must not be read as "between runs"."""
    board = str(repo / ".tickets")
    assert not (repo / ".tickets" / "agents" / (SEAT + ".run")).exists()

    busy = subprocess.Popen(["/bin/sh", "-c", "sleep 30 & wait"], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    idle = subprocess.Popen(["/bin/sh", "-c", "exec sleep 30"], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert _settle(lambda: tk._watcher_run_active(board, SEAT, busy.pid), True) is True
        assert tk._watcher_run_active(board, SEAT, idle.pid) is False
    finally:
        for p in (busy, idle):
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def test_run_file_for_a_different_pid_falls_back(tk, repo):
    """A run file left by a PREVIOUS watcher must not answer for this pid."""
    board = str(repo / ".tickets")
    _write_run(repo, SEAT, {"pid": 999999, "active": True})
    idle = subprocess.Popen(["/bin/sh", "-c", "exec sleep 30"], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert tk._watcher_run_active(board, SEAT, idle.pid) is False
    finally:
        try:
            os.killpg(os.getpgid(idle.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            idle.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
