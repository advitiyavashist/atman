"""T-559: QUIET-BOX/desk_pytest_alive must match pytest argv tokens, not cwd.

The T-486 (e) helper used `pgrep -f 'desk-cursor-fable/.venv/bin/python -m
pytest'` and a cwd==desk-WT gate. Live desk merge pid 51475 is
`pytest -q -p no:cacheprovider -x --ignore=.worktrees --ignore=.claude` and
chdirs into /tmp/pytest-of-kavana mid-run, so both gates read CLEAR while the
suite is still alive (killed opus-verify waiter 71007). Contiguous pgrep
`pytest -x --ignore=.worktrees` is empty because -q/-p sit between pytest
and -x. `ps aux` regex `[p]ytest.*-x.*--ignore=\\.worktrees` matches agent
prompts that quote those tokens (optimizer 5983).

ACCEPT: argv tokens pytest AND -x AND --ignore=.worktrees (or pid), never
cwd, never the full ps-aux line. Isolated: fake pytest whose cwd is a tmp
worktree must still count as ALIVE. Never spawn --stop a live seat.
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

LIVE_HARNESS = (
    "/Users/kavana/Downloads/atman/.worktrees/desk-cursor-fable/.venv/bin/python "
    "-m pytest -q -p no:cacheprovider -x --ignore=.worktrees --ignore=.claude"
)
LIVE_PYTEST_BIN = (
    "pytest -q -p no:cacheprovider -x --ignore=.worktrees --ignore=.claude"
)
AGENT_PROMPT_BLOB = (
    "/opt/homebrew/bin/python3 "
    "/Users/kavana/.claude/tools/tickets-releases/"
    "b738f1dcc1824dc0a3d3434e318b2e6612998227/tickets.py watch "
    "--agent optimizer --every 60 --cwd /Users/kavana/Downloads/steer "
    "--exec claude -p "
    '"wait for pytest -x --ignore=.worktrees then spawn --stop"'
)


@pytest.fixture(scope="module")
def tk():
    spec = importlib.util.spec_from_file_location("tickets_under_test_t559", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- the predicate, on command lines ps actually prints -----------------

def test_python_m_pytest_with_gaps_is_desk_merge(tk):
    """Live 20603 / 51475 shape: flags sit between pytest and -x."""
    assert tk._is_desk_merge_pytest_cmd(LIVE_HARNESS) is True
    assert tk._is_desk_merge_pytest_cmd(LIVE_PYTEST_BIN) is True
    # the contiguous substring the old helper grepped is NOT in the line
    assert "pytest -x --ignore=.worktrees" not in LIVE_HARNESS
    assert "pytest -x --ignore=.worktrees" not in LIVE_PYTEST_BIN


def test_cwd_is_not_consulted(tk):
    """The predicate is cmd-only; a /tmp worktree cwd cannot make it CLEAR."""
    assert tk._is_desk_merge_pytest_cmd(LIVE_HARNESS) is True
    assert tk._is_desk_merge_pytest_cmd(LIVE_PYTEST_BIN) is True


@pytest.mark.parametrize("cmd", [
    AGENT_PROMPT_BLOB,
    "/usr/bin/python3 /Users/kavana/.claude/tools/tickets.py watch "
    "--agent cursor-demo --exec 'pytest -x --ignore=.worktrees'",
    "grep -E '[p]ytest.*-x.*--ignore=\\.worktrees'",
    "/usr/bin/python3 /some/agent.py -p 'run pytest -x --ignore=.worktrees'",
    "pytest -q --ignore=.worktrees",          # missing -x
    "pytest -q -x",                          # missing --ignore=.worktrees
    "python -m pytest -q --ignore=.worktrees",  # missing -x
    "",
])
def test_prompt_blob_and_incomplete_argv_are_rejected(tk, cmd):
    assert tk._is_desk_merge_pytest_cmd(cmd) is False


def test_ignore_equals_and_split_forms_both_count(tk):
    assert tk._is_desk_merge_pytest_cmd(
        "python -m pytest -x --ignore .worktrees") is True
    assert tk._is_desk_merge_pytest_cmd(
        "python -m pytest -x --ignore=.worktrees") is True


# ---- the process table, cwd is a tmp worktree --------------------

def _settle(fn, pred, tries=40):
    for _ in range(tries):
        got = fn()
        if pred(got):
            return got
        time.sleep(0.05)
    return fn()


def _sleeper(path):
    path.write_text("import time\nwhile True: time.sleep(3600)\n")
    return path


@pytest.fixture
def started():
    procs = []
    yield procs
    for p in procs:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _popen(args, cwd, started):
    p = subprocess.Popen(
        args, cwd=str(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True)
    started.append(p)
    return p


def test_fake_pytest_in_tmp_worktree_is_alive(tk, tmp_path, started):
    """Isolated ACCEPT: fake pytest whose cwd is a tmp worktree still ALIVE."""
    wt = tmp_path / "pytest-of-kavana" / "worktree"
    wt.mkdir(parents=True)
    script = _sleeper(tmp_path / "pytest")
    p = _popen(
        [sys.executable, str(script), "-q", "-p", "no:cacheprovider", "-x",
         "--ignore=.worktrees", "--ignore=.claude"],
        cwd=wt, started=started)
    got = _settle(lambda: tk._desk_pytest_pids(), lambda pids: p.pid in pids)
    assert p.pid in got, (
        "desk_pytest_alive missed a pytest whose cwd is a tmp worktree "
        "(the 51475 miss). Gate on argv tokens, never cwd.")
    try:
        cwd = os.readlink("/proc/%d/cwd" % p.pid)
    except OSError:
        cwd = os.path.realpath(str(wt))
    assert "desk-cursor-fable" not in cwd
    # do not call spawn --stop; just SIGKILL in the fixture teardown


def test_python_m_pytest_sleeper_is_alive(tk, tmp_path, started):
    """Live 20603 shape: python -m pytest -x --ignore=.worktrees (cwd=tmp)."""
    wt = tmp_path / "tmp-wt"
    wt.mkdir()
    fake_mod = tmp_path / "fakemods"
    fake_mod.mkdir()
    _sleeper(fake_mod / "pytest.py")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(fake_mod) + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.Popen(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-x", "--ignore=.worktrees"],
        cwd=str(wt), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True)
    started.append(p)
    got = _settle(lambda: tk._desk_pytest_pids(), lambda pids: p.pid in pids)
    assert p.pid in got


def test_agent_prompt_process_is_not_alive(tk, tmp_path, started):
    """HB205: a non-pytest process whose argv quotes the harness is not ALIVE."""
    wt = tmp_path / "tmp-wt"
    wt.mkdir()
    script = _sleeper(tmp_path / "agent.py")
    p = _popen(
        [sys.executable, str(script), "--prompt",
         "wait for pytest -x --ignore=.worktrees then spawn --stop"],
        cwd=wt, started=started)
    time.sleep(0.15)
    assert p.pid not in tk._desk_pytest_pids()
    assert tk._is_desk_merge_pytest_cmd(
        " ".join([sys.executable, str(script), "--prompt",
                  "wait for pytest -x --ignore=.worktrees then spawn --stop"])
    ) is False


def test_extra_pid_counts_even_if_argv_is_not_pytest(tk, tmp_path, started):
    """ACCEPT 'or the pid': T-554 waiter also checked `ps -p 51475`."""
    wt = tmp_path / "tmp-wt"
    wt.mkdir()
    script = _sleeper(tmp_path / "not-pytest.py")
    p = _popen([sys.executable, str(script)], cwd=wt, started=started)
    assert p.pid not in tk._desk_pytest_pids()
    got = _settle(lambda: tk._desk_pytest_pids(extra_pids=(p.pid,)),
                  lambda pids: p.pid in pids)
    assert p.pid in got
    os.kill(p.pid, signal.SIGKILL)
    p.wait(timeout=5)
    started.remove(p)
    gone = _settle(lambda: tk._desk_pytest_pids(extra_pids=(p.pid,)),
                   lambda pids: p.pid not in pids)
    assert p.pid not in gone
