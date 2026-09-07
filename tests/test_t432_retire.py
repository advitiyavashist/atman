"""T-432: tickets retire <name> removes a probe seat; refuses while holding a ticket."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def _env(board):
    e = dict(os.environ, TICKETS_DIR=str(board), HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    return e


def run(board, *args, agent="", entry="root", cwd=None):
    e = _env(board)
    e["TICKET_AGENT"] = agent or ""
    if entry == "pkg":
        e["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
        cmd = [sys.executable, "-m", "ticket_board", *args]
    else:
        cmd = [sys.executable, str(TOOL), *args]
    where = cwd or (board.parent if board.parent.is_dir() else Path("/"))
    return subprocess.run(cmd, capture_output=True, text=True, env=e, cwd=where)


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
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
    r = run(b, "create", "Backend work", "--role", "backend", cwd=repo)
    assert r.returncode == 0, r.stderr
    return b


@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_retire_empty_probe_seat(board, entry):
    run(board, "join", "probe", "--roles", "backend", agent="probe", entry=entry)
    assert (board / "agents" / "probe.json").is_file()
    r = run(board, "retire", "probe", agent="master", entry=entry)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "retired probe" in r.stdout
    assert not (board / "agents" / "probe.json").exists()
    wf = json.loads((board / "workforce.json").read_text())
    assert "probe" not in wf
    roles = json.loads((board / "roles.json").read_text())
    assert "probe" not in roles


@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_retire_refuses_when_holding_ticket(board, entry):
    run(board, "join", "worker", "--roles", "backend", agent="worker", entry=entry)
    assert run(board, "next", agent="worker", entry=entry).returncode == 0
    r = run(board, "retire", "worker", agent="master", entry=entry)
    assert r.returncode != 0
    assert "holds a ticket" in r.stderr + r.stdout
    assert (board / "agents" / "worker.json").is_file()
    wf = json.loads((board / "workforce.json").read_text())
    assert "worker" in wf


@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_retire_refuses_when_holding_ticket_in_review(board, entry):
    repo = board.parent
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "worker-branch"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "worker branch"],
        check=True,
        env=dict(
            os.environ,
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t",
        ),
    )
    run(board, "join", "worker", "--roles", "backend", agent="worker", entry=entry)
    assert run(board, "next", agent="worker", entry=entry, cwd=repo).returncode == 0
    assert run(
        board,
        "review",
        "T-001",
        "--notes",
        "paths touched",
        "--force",
        agent="worker",
        entry=entry,
        cwd=repo,
    ).returncode == 0
    r = run(board, "retire", "worker", agent="master", entry=entry)
    assert r.returncode != 0
    assert "holds a ticket" in r.stderr + r.stdout
    assert (board / "agents" / "worker.json").is_file()
