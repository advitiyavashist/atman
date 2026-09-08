"""T-351: adversarial pre-merge verification of T-311 two-copy.

Independent of the author's fixture tests. Each case maps to the ticket body.
Verdict: all five must pass for MERGE-READY on grok-worker/t311-two-copy@cec0c74.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ROOT_TOOL = ROOT / "tickets.py"
PKG_TOOL = ROOT / "src" / "ticket_board" / "cli.py"


def _env(board, agent="", extra=None):
    e = dict(
        os.environ,
        TICKETS_DIR=str(board),
        TICKET_AGENT=agent or "",
        HOME=str(board.parent.parent / "home"),
        PYTHONPATH=str(ROOT / "src"),
    )
    e.pop("TICKETS_STOP_HOOK", None)
    if extra:
        e.update(extra)
    return e


def run_py_tool(tool, board, *args, agent="", cwd=None, env=None):
    where = cwd or board.parent
    return subprocess.run(
        [sys.executable, str(tool), *args],
        capture_output=True,
        text=True,
        env=_env(board, agent, env),
        cwd=where,
    )


def run_console(board, console_bin, *args, agent="", cwd=None, env=None):
    where = cwd or board.parent
    e = _env(board, agent, env)
    # pip console resolves ticket_coordination as a top-level module beside cli.py
    pkg_dir = str(ROOT / "src" / "ticket_board")
    e["PYTHONPATH"] = pkg_dir + os.pathsep + e.get("PYTHONPATH", "")
    return subprocess.run(
        [str(console_bin), *args],
        capture_output=True,
        text=True,
        env=e,
        cwd=where,
    )


def load_events(board):
    path = board / "trajectories.jsonl"
    if not path.exists():
        return []
    out = []
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


def cmp_events(root_events, pkg_events):
    """Every field except 'at' must match record-by-record."""
    assert len(root_events) == len(pkg_events), (
        "event count mismatch: root=%d pkg=%d" % (len(root_events), len(pkg_events))
    )
    for i, (a, b) in enumerate(zip(root_events, pkg_events)):
        a_cmp = {k: v for k, v in a.items() if k != "at"}
        b_cmp = {k: v for k, v in b.items() if k != "at"}
        assert a_cmp == b_cmp, "record %d differs:\nroot=%r\npkg=%r" % (i, a_cmp, b_cmp)


def _prep_repo(board):
    repo = board.parent
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "ignore"],
        check=True,
        env=dict(
            os.environ,
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t",
        ),
    )
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"], check=True)
    return repo


def _drive_sequence(run_fn, board, repo):
    """claim -> update -> review -> reopen -> done -> msg on one ticket."""
    run_fn(board, "join", "alice", "--roles", "backend", "--tool", "claude", "--model", "opus", agent="alice", cwd=repo)
    run_fn(board, "create", "Ship it", "--role", "backend", cwd=repo)
    r = run_fn(board, "next", "--role", "backend", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    tid = "T-002"
    run_fn(board, "update", tid, "halfway", agent="alice", cwd=repo)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "work"],
        check=True,
        env=dict(
            os.environ,
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t",
        ),
    )
    r = run_fn(board, "review", tid, "--notes", "paths and tests", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr + r.stdout
    r = run_fn(board, "reopen", tid, "--notes", "FIX-FIRST: drive remaining events",
               agent="carol", cwd=repo)
    assert r.returncode == 0, r.stderr + r.stdout
    r = run_fn(board, "done", tid, "--notes", "landed", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr + r.stdout
    run_fn(board, "msg", "follow-up ping", "--to", "bob", "--re", tid, agent="alice", cwd=repo)


@pytest.fixture(scope="module")
def installed_console():
    venv = ROOT / ".venv-t351"
    if not (venv / "bin" / "python").exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    py = venv / "bin" / "python"
    subprocess.run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip", "setuptools", "wheel"], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "-q", "-e", str(ROOT)], check=True)
    console = venv / "bin" / "tickets"
    assert console.exists(), "pip install -e . did not produce tickets console script"
    return console


# ---- case 1: end-to-end parity through both delivery paths ----------------

def _fresh_board(tmp_parent, name):
    repo = tmp_parent / name
    repo.mkdir(parents=True, exist_ok=True)
    (tmp_parent / "home").mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
        env=dict(
            os.environ,
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t",
        ),
    )
    b = repo / ".tickets"
    r = run_py_tool(ROOT_TOOL, b, "create", "Write docs", "--role", "docs", cwd=repo)
    assert r.returncode == 0, r.stderr
    return b, repo


def _strip_env_fields(events):
    """Git/worktree paths and wall-clock timings differ across throwaway repos."""
    skip = {"at", "worktree", "branch", "sha", "pin", "repo", "active_hours", "wait_hours"}
    return [{k: v for k, v in e.items() if k not in skip} for e in events]


def test_case1a_pip_install_console_runs_without_extra_pythonpath(installed_console, tmp_path):
    """Fresh pip install -e . must run without ModuleNotFoundError."""
    board, repo = _fresh_board(tmp_path, "pip-repo")
    r = subprocess.run(
        [str(installed_console), "create", "x", "--role", "docs"],
        capture_output=True,
        text=True,
        env=dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="alice", HOME=str(tmp_path / "home")),
        cwd=str(repo),
    )
    assert r.returncode == 0, (
        "pip-installed tickets console must import ticket_coordination without extra PYTHONPATH: "
        + (r.stderr or r.stdout)
    )


def test_case1b_root_and_installed_console_write_identical_records(tmp_path, installed_console):
    root_board, root_repo = _fresh_board(tmp_path, "root-repo")
    pkg_board, pkg_repo = _fresh_board(tmp_path, "pkg-repo")
    _prep_repo(root_board)
    _prep_repo(pkg_board)

    def root_run(b, *args, **kw):
        return run_py_tool(ROOT_TOOL, b, *args, **kw)

    def pkg_run(b, *args, **kw):
        return run_console(b, installed_console, *args, **kw)

    _drive_sequence(root_run, root_board, root_repo)
    _drive_sequence(pkg_run, pkg_board, pkg_repo)

    root_ev = [e for e in load_events(root_board) if e.get("kind") != "msg" or e.get("to") == "bob"]
    pkg_ev = [e for e in load_events(pkg_board) if e.get("kind") != "msg" or e.get("to") == "bob"]
    # drop join-announcement msgs; keep ticket-scoped msgs
    root_ev = [e for e in root_ev if not (e.get("kind") == "msg" and e.get("to") in (None, ""))]
    pkg_ev = [e for e in pkg_ev if not (e.get("kind") == "msg" and e.get("to") in (None, ""))]

    root_kinds = [e["kind"] for e in root_ev]
    pkg_kinds = [e["kind"] for e in pkg_ev]
    assert root_kinds == pkg_kinds, "kind sequence: root=%r pkg=%r" % (root_kinds, pkg_kinds)
    assert _strip_env_fields(root_ev) == _strip_env_fields(pkg_ev)


def test_case1c_cli_reopen_accepts_notes_like_root(board):
    """Root tickets.py reopen accepts --notes; packaged cli must too."""
    repo = _prep_repo(board)
    run_py_tool(PKG_TOOL, board, "join", "alice", "--roles", "backend", agent="alice", cwd=repo)
    run_py_tool(PKG_TOOL, board, "create", "Ship", "--role", "backend", cwd=repo)
    run_py_tool(PKG_TOOL, board, "next", "--role", "backend", agent="alice", cwd=repo)
    run_py_tool(PKG_TOOL, board, "update", "T-002", "progress", agent="alice", cwd=repo)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "work"],
        check=True,
        env=dict(
            os.environ,
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t",
        ),
    )
    run_py_tool(PKG_TOOL, board, "review", "T-002", "--notes", "ready", agent="alice", cwd=repo)
    r = run_py_tool(PKG_TOOL, board, "reopen", "T-002", "--notes", "why", agent="carol", cwd=repo)
    assert r.returncode == 0, r.stderr or r.stdout


# ---- case 2: parity tests go red without cli instrumentation ---------------

def test_case2_reverting_cli_instrumentation_makes_parity_red(tmp_path):
    """Revert traj_event body in a scratch copy; entrypoint parity tests must go red."""
    scratch = tmp_path / "cli_no_traj.py"
    src = PKG_TOOL.read_text()
    stripped = src.replace(
        "def traj_event(board, kind, agent=\"\", ticket=None, **fields):\n"
        "    \"\"\"Append one trajectory event, best-effort.\"\"\"\n"
        "    return _traj_safe(\n"
        "        lambda: _traj.event(board, kind, agent=agent, ticket=ticket, **fields))",
        "def traj_event(board, kind, agent=\"\", ticket=None, **fields):\n"
        "    \"\"\"Append one trajectory event, best-effort.\"\"\"\n"
        "    return None",
    )
    assert stripped != src, "failed to revert instrumentation hunk"
    scratch.write_text(stripped)

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import importlib.util, os, shutil, sys\n"
                "from pathlib import Path\n"
                "root = Path(%r)\n"
                "scratch = Path(%r)\n"
                "live = root / 'src/ticket_board/cli.py'\n"
                "bak = live.with_suffix('.bak')\n"
                "shutil.copy2(live, bak)\n"
                "shutil.copy2(scratch, live)\n"
                "try:\n"
                "  import pytest\n"
                "  rc = pytest.main(['-q', 'tests/test_trajectories_entrypoints.py::"
                "test_packaged_cli_records_claim_update_and_block', "
                "'tests/test_trajectories_entrypoints.py::test_the_two_writers_build_the_same_event'])\n"
                "  sys.exit(rc)\n"
                "finally:\n"
                "  shutil.copy2(bak, live)\n"
                "  bak.unlink()\n"
            )
            % (str(ROOT), str(scratch)),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
    )
    assert probe.returncode != 0, (
        "parity tests stayed green after reverting cli instrumentation; "
        "they may compare the module to itself\n" + probe.stdout + probe.stderr
    )


# ---- case 3: 5 KB msg body -> text_len only on cli path -------------------

def test_case3_five_kb_msg_carries_text_len_only_via_packaged_cli(board):
    body = "X" * 5120
    repo = _prep_repo(board)
    r = run_py_tool(PKG_TOOL, board, "join", "alice", "--roles", "docs", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    r = run_py_tool(
        PKG_TOOL,
        board,
        "msg",
        body,
        "--to",
        "bob",
        "--re",
        "T-001",
        agent="alice",
        cwd=repo,
    )
    assert r.returncode == 0, r.stderr
    raw = (board / "trajectories.jsonl").read_text()
    assert body not in raw
    msg = [e for e in load_events(board) if e.get("kind") == "msg" and e.get("to") == "bob"]
    assert len(msg) == 1
    assert msg[0]["text_len"] == 5120
    assert "text" not in msg[0]
    assert "note" not in msg[0]


# ---- case 4: rotation at ceiling on packaged cli path ----------------------

def test_case4_packaged_cli_rotates_at_ceiling_like_root(board):
    repo = _prep_repo(board)
    tiny = {"TICKETS_TRAJECTORIES_MAX_BYTES": "1200"}
    run_py_tool(
        PKG_TOOL,
        board,
        "join",
        "alice",
        "--roles",
        "backend",
        "--tool",
        "claude",
        "--model",
        "opus",
        agent="alice",
        cwd=repo,
        env=tiny,
    )
    run_py_tool(PKG_TOOL, board, "create", "Ship it", "--role", "backend", cwd=repo, env=tiny)
    run_py_tool(PKG_TOOL, board, "next", "--role", "backend", agent="alice", cwd=repo, env=tiny)
    for i in range(40):
        r = run_py_tool(
            PKG_TOOL,
            board,
            "update",
            "T-002",
            "n%02d" % i,
            agent="alice",
            cwd=repo,
            env=tiny,
        )
        assert r.returncode == 0, r.stderr
    assert list(board.glob("trajectories.*.jsonl")), "packaged cli must rotate trajectories.jsonl"
    r = run_py_tool(
        PKG_TOOL,
        board,
        "trajectories",
        "--ticket",
        "T-002",
        "--kind",
        "claim",
        "--json",
        agent="alice",
        cwd=repo,
        env=tiny,
    )
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert len(got) == 1, "reader must see claim in archive after rotation"

    # same ceiling constant as root
    root = __import__("importlib.util").util.spec_from_file_location("root_tickets", ROOT_TOOL)
    root_mod = __import__("importlib.util").util.module_from_spec(root)
    root.loader.exec_module(root_mod)
    sys.path.insert(0, str(ROOT / "src"))
    try:
        import importlib

        pkg = importlib.import_module("ticket_board.trajectories")
    finally:
        sys.path.pop(0)
    assert root_mod.TRAJ_MAX_BYTES == pkg.TRAJ_MAX_BYTES == 50 * 1024 * 1024


# ---- case 5: turns reads cli-only file same as root ------------------------

def test_case5_turns_agrees_on_cli_only_trajectories(board):
    repo = _prep_repo(board)
    run_py_tool(PKG_TOOL, board, "join", "alice", "--roles", "backend", "--tool", "claude", "--model", "opus", agent="alice", cwd=repo)
    run_py_tool(PKG_TOOL, board, "create", "Count turns", "--role", "backend", cwd=repo)
    run_py_tool(PKG_TOOL, board, "next", "--role", "backend", agent="alice", cwd=repo)
    # minimal run_end via watch fake harness
    fake = repo / "fake.sh"
    fake.write_text("#!/bin/sh\necho '{\"type\":\"result\",\"num_turns\":3}'\n")
    fake.chmod(fake.stat().st_mode | 0o111)
    run_py_tool(
        PKG_TOOL,
        board,
        "watch",
        "--agent",
        "alice",
        "--once",
        "--exec",
        str(fake),
        "--cwd",
        str(repo),
        agent="alice",
        cwd=repo,
    )
    assert (board / "trajectories.jsonl").exists()

    r_root = run_py_tool(ROOT_TOOL, board, "turns", "--json", cwd=repo)
    r_pkg = run_py_tool(PKG_TOOL, board, "turns", "--json", cwd=repo)
    assert r_root.returncode == 0, r_root.stderr
    assert r_pkg.returncode == 0, r_pkg.stderr
    a, b = json.loads(r_root.stdout), json.loads(r_pkg.stdout)
    assert a == b
