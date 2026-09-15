"""T-1004: first-run generated output is atm-first and ends at review.

Fresh quickstart, worker join/next, and coordinator master close must teach
the real lifecycle: workers submit an exact artifact with `atm review`;
accept, merge, and done stay distinct coordinator steps. `tickets` remains
the compatibility alias. Throwaway boards only.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "fake-home"),
        "PYTEST_CURRENT_TEST": "tests/test_t1004_first_run_output.py::simulated (call)",
    }
    for passthrough in ("TMPDIR", "TMP", "TEMP"):
        if os.environ.get(passthrough):
            e[passthrough] = os.environ[passthrough]
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run_board(tool, board, *args, agent=""):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    for var in ("TICKET_SEAT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
                "CURSOR_SESSION_ID", "TERM_SESSION_ID"):
        e.pop(var, None)
    if args and args[0] == "join" and len(args) > 1 and args[1]:
        actor = args[1]
    else:
        actor = agent or "__anonymous__"
    e["TICKET_SESSION_ID"] = "test-session-" + actor
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(tool), *args], capture_output=True, text=True,
        env=e, cwd=str(board.parent))


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t1004@test", "-c", "user.name=t1004", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t1004\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


_PRIMARY_TICKETS_CMD = re.compile(
    r"(?m)^[ \t]*(?:TICKET_AGENT=\S+[ \t]+)?tickets[ \t]+(next|update|review|ui|guide|join|spawn|done)\b"
)


def test_fresh_quickstart_teaches_atm_as_primary(tmp_path):
    """Golden fresh-board output: atm is the CLI; tickets is only the alias."""
    repo = make_repo(tmp_path / "fresh")
    r = subprocess.run(
        [sys.executable, str(ROOT / "tickets.py"), "quickstart", "--agent", "alice",
         "--roles", "backend"],
        capture_output=True, text=True, cwd=str(repo),
        env=clean_env(tmp_path, TICKET_AGENT="alice"))
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "The three commands that matter:" in out
    assert "atm next" in out
    assert "atm update" in out
    assert "atm review" in out
    assert "exact artifact" in out
    assert "atm ui" in out
    assert "compatibility alias" in out
    assert not _PRIMARY_TICKETS_CMD.search(out), out
    assert "tickets done" not in out
    assert "atm done" not in out


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_worker_join_and_next_end_at_atm_review(tool, board):
    """Worker join + next finish at atm review with an exact artifact."""
    joined = run_board(tool, board, "join", "worker", "--roles", "docs", agent="worker")
    assert joined.returncode == 0, joined.stderr + joined.stdout
    jout = joined.stdout
    assert "atm review" in jout
    assert "exact artifact" in jout
    assert "atm done <id>" not in jout
    assert "tickets done" not in jout
    loop = [ln for ln in jout.splitlines() if ln.startswith("Loop:")]
    assert loop, jout
    assert "atm review" in loop[0]
    assert "atm done" not in loop[0]
    assert loop[0].index("atm review") > loop[0].index("atm next")

    claimed = run_board(tool, board, "next", agent="worker")
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    nout = claimed.stdout
    assert "atm update T-001" in nout
    assert 'finish with `atm review T-001 --notes "exact SHA, paths, decisions"`.' in nout
    assert "tickets done" not in nout
    assert "atm done" not in nout
    assert "tickets review" not in nout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_coordinator_close_keeps_accept_merge_done_distinct(tool, board):
    """Coordinator master output names accept, merge, and done as three steps."""
    assert run_board(tool, board, "join", "worker", "--roles", "docs",
                     agent="worker").returncode == 0
    claimed = run_board(tool, board, "next", agent="worker")
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    reviewed = run_board(tool, board, "review", "T-001", "--force",
                         "--notes", "exact artifact abc", agent="worker")
    assert reviewed.returncode == 0, reviewed.stderr + reviewed.stdout

    master = run_board(tool, board, "master", agent="boss")
    assert master.returncode == 0, master.stderr + master.stdout
    out = master.stdout
    queue = [ln for ln in out.splitlines() if ln.startswith("REVIEW QUEUE")]
    assert queue, out
    line = queue[0]
    assert "atm accept" in line
    assert "atm merge" in line
    assert "atm done" in line
    assert line.index("atm accept") < line.index("atm merge") < line.index("atm done")
    assert "three distinct steps" in line
    assert "tickets done" not in line
    assert "review, merge, then" not in line
