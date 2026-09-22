"""T-657 / STORE-001: malformed committed ticket JSON fails closed (T-817)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TICKETS_PY = Path(__file__).resolve().parents[1] / "tickets.py"
SOUND_NOTES = "cause=c; change=ch; proof=p; deps=none"


def sh(repo: Path, *args, check=True):
    r = subprocess.run(list(args), cwd=repo, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError("%s failed: %s%s" % (args, r.stdout, r.stderr))
    return r


@pytest.fixture
def board_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    sh(root, "git", "init", "-q", "-b", "main")
    board = root / ".tickets"
    board.mkdir()
    return root


def tickets(repo: Path, *args, agent="worker"):
    env = os.environ.copy()
    env["TICKET_AGENT"] = agent
    env.pop("TICKET_SEAT", None)
    env.pop("TICKETS_WATCH_PINNED", None)
    env["TICKETS_DIR"] = str(repo / ".tickets")
    env["PYTHONPATH"] = str(TICKETS_PY.parent) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(TICKETS_PY), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def write_valid_ticket(board: Path, tid: str, **fields):
    data = {
        "id": tid,
        "title": tid,
        "status": "open",
        "owner": "",
        "deps": [],
        "notes": [],
        "created": "2026-09-09T00:00:00Z",
        "updated": "2026-09-09T00:00:00Z",
        "lane": "ready",
    }
    data.update(fields)
    (board / f"{tid}.json").write_text(json.dumps(data, indent=2) + "\n")


def test_load_all_surfaces_malformed_committed_ticket(board_repo):
    write_valid_ticket(board_repo / ".tickets", "T-001")
    (board_repo / ".tickets" / "T-002.json").write_text("{not json\n")
    r = tickets(board_repo, "board", "--quiet")
    assert r.returncode == 1
    assert "corrupt ticket T-002" in (r.stdout + r.stderr)
    assert "Traceback" not in (r.stdout + r.stderr)


def test_show_surfaces_malformed_ticket_without_traceback(board_repo):
    (board_repo / ".tickets" / "T-001.json").write_text("{bad\n")
    r = tickets(board_repo, "show", "T-001")
    assert r.returncode == 1
    assert "corrupt ticket T-001" in (r.stdout + r.stderr)
    assert "Traceback" not in (r.stdout + r.stderr)


def test_mutation_refuses_when_board_has_corrupt_ticket(board_repo):
    board = board_repo / ".tickets"
    write_valid_ticket(board, "T-001")
    corrupt_path = board / "T-002.json"
    corrupt_path.write_text('{"id": "T-002", "status": "open", "title": ')
    before = corrupt_path.read_bytes()
    t001_before = (board / "T-001.json").read_bytes()
    r = tickets(board_repo, "create", "New work")
    assert r.returncode == 1
    assert "corrupt ticket T-002" in (r.stdout + r.stderr)
    assert corrupt_path.read_bytes() == before
    assert (board / "T-001.json").read_bytes() == t001_before
    assert not (board / "T-003.json").exists()


def test_update_refuses_and_preserves_bytes_with_corrupt_neighbor(board_repo):
    board = board_repo / ".tickets"
    write_valid_ticket(board, "T-001")
    (board / "T-002.json").write_text('{"id":')
    before = {p.name: p.read_bytes() for p in board.iterdir() if p.is_file()}
    r = tickets(board_repo, "update", "T-001", "probe")
    assert r.returncode == 1
    assert "corrupt ticket T-002" in (r.stdout + r.stderr)
    after = {p.name: p.read_bytes() for p in board.iterdir() if p.is_file() and not p.name.endswith(".lock")}
    before_nolock = {k: v for k, v in before.items() if not k.endswith(".lock")}
    assert after == before_nolock
    assert not list(board.glob("T-*.lock")) or all(
        p.name.endswith(".json.lock") for p in board.glob("T-*.lock")
    )
    assert not (board / "T-001.lock").exists()
    assert not (board / "T-002.lock").exists()


def test_claim_status_assign_refuse_without_leaving_locks(board_repo):
    board = board_repo / ".tickets"
    write_valid_ticket(board, "T-001")
    (board / "T-002.json").write_text('{"id":')
    for argv in (
        ("claim", "T-001"),
        ("status", "T-001", "in-progress"),
        ("assign", "T-001", "--owner", "worker"),
    ):
        before = {p.name: p.read_bytes() for p in board.iterdir() if p.is_file()}
        r = tickets(board_repo, *argv)
        assert r.returncode == 1, argv
        assert "corrupt ticket T-002" in (r.stdout + r.stderr), argv
        assert not (board / "T-001.lock").exists(), argv
        assert not (board / "T-002.lock").exists(), argv
        after = {
            p.name: p.read_bytes()
            for p in board.iterdir()
            if p.is_file() and not p.name.endswith(".lock")
        }
        before_nolock = {k: v for k, v in before.items() if not k.endswith(".lock")}
        assert after == before_nolock, argv


def test_orphan_partial_residue_does_not_hide_committed_ticket(board_repo):
    board = board_repo / ".tickets"
    write_valid_ticket(board, "T-001", title="Committed work")
    (board / ".T-001.json.partial").write_text('{"status":')
    r = tickets(board_repo, "list")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Committed work" in (r.stdout + r.stderr)


def test_dangling_committed_symlink_fails_without_traceback(board_repo):
    board = board_repo / ".tickets"
    write_valid_ticket(board, "T-001")
    (board / "T-002.json").symlink_to("/nonexistent/path.json")
    r = tickets(board_repo, "board", "--quiet")
    assert r.returncode == 1
    assert "corrupt ticket T-002" in (r.stdout + r.stderr)
    assert "dangling symlink" in (r.stdout + r.stderr)
    assert "Traceback" not in (r.stdout + r.stderr)


def test_symlinked_committed_ticket_with_malformed_target_fails(board_repo):
    board = board_repo / ".tickets"
    bad = board_repo / "bad-target.json"
    bad.write_text("{broken\n")
    (board / "T-001.json").symlink_to(bad)
    r = tickets(board_repo, "board", "--quiet")
    assert r.returncode == 1
    assert "corrupt ticket T-001" in (r.stdout + r.stderr)


def test_t642_store001_malformed_committed_json_conformance(board_repo):
    """Strict T-642 negative case: committed canonical records must not vanish silently."""
    board = board_repo / ".tickets"
    write_valid_ticket(board, "T-001", title="good")
    (board / "T-002.json").write_text("{bad\n")
    board_before = {p.name: p.read_bytes() for p in board.iterdir() if p.is_file()}
    r = tickets(board_repo, "graph")
    assert r.returncode == 1
    assert "corrupt ticket T-002" in (r.stdout + r.stderr)
    board_after = {p.name: p.read_bytes() for p in board.iterdir() if p.is_file()}
    assert board_after == board_before
