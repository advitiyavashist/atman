"""T-656 / DEPS-001: explicit claim paths enforce unfinished-dependency gates (T-817).

Preserves ready-lane semantics: plan records land in lane=capture and must be
sounded before claim. Release still requires accept (or release_override).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TICKETS_PY = Path(__file__).resolve().parents[1] / "tickets.py"
PLAN = (
    '[{"key":"upstream","title":"Build API","role":"backend","deps":[]},'
    '{"key":"downstream","title":"Use API","role":"backend","deps":["upstream"]}]'
)
SOUND_NOTES = "cause=need API; change=build it; proof=tests; deps=none"


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


def tickets(repo: Path, *args, agent="worker", stdin=""):
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
        input=stdin,
        capture_output=True,
        text=True,
    )


def seed_dependency_pair(repo: Path):
    r = tickets(repo, "plan", stdin=PLAN)
    assert r.returncode == 0, r.stdout + r.stderr
    # Master/CoS sound path: implementer cannot sound their own claimed work,
    # but these are still open/capture so a non-owner seat can sound them.
    for tid in ("T-001", "T-002"):
        r = tickets(repo, "sound", tid, "--notes", SOUND_NOTES, agent="master")
        assert r.returncode == 0, tid + ": " + r.stdout + r.stderr
        assert ticket(repo, tid).get("lane") == "ready"
    r = tickets(repo, "join", "worker", "--roles", "backend")
    assert r.returncode == 0, r.stdout + r.stderr


def ticket(repo: Path, tid: str) -> dict:
    return json.loads((repo / ".tickets" / f"{tid}.json").read_text())


def mark_released(repo: Path, tid: str):
    """Plant a release_override and reopen dependents blocked by the accept gate."""
    path = repo / ".tickets" / f"{tid}.json"
    data = json.loads(path.read_text())
    data["release_override"] = {
        "kind": "owner",
        "by": "master",
        "at": "2026-09-09T00:00:00Z",
        "reason": "t817-test",
    }
    path.write_text(json.dumps(data, indent=2) + "\n")
    # Raw JSON plant skips save()'s reopen hook; restore dependents to open.
    for child_path in (repo / ".tickets").glob("T-*.json"):
        child = json.loads(child_path.read_text())
        if tid in (child.get("deps") or []) and child.get("status") == "blocked":
            child["status"] = "open"
            child.pop("unverified_block", None)
            child_path.write_text(json.dumps(child, indent=2) + "\n")


def claim_locks(repo: Path):
    """O_EXCL claim locks only (ignore coordination *.json.lock files)."""
    return sorted(
        p.name for p in (repo / ".tickets").glob("T-*.lock")
        if not p.name.endswith(".json.lock")
    )


def test_explicit_claim_refuses_unmet_dependencies(board_repo):
    seed_dependency_pair(board_repo)
    r = tickets(board_repo, "claim", "T-002")
    assert r.returncode == 1
    assert "unfinished dependencies" in (r.stdout + r.stderr)
    assert "T-001" in (r.stdout + r.stderr)
    assert ticket(board_repo, "T-002")["status"] == "open"
    assert not ticket(board_repo, "T-002").get("owner")
    assert not (board_repo / ".tickets" / "T-002.lock").exists()


def test_status_in_progress_refuses_unmet_dependencies(board_repo):
    seed_dependency_pair(board_repo)
    r = tickets(board_repo, "status", "T-002", "in-progress")
    assert r.returncode == 1
    assert "unfinished dependencies" in (r.stdout + r.stderr)
    assert ticket(board_repo, "T-002")["status"] == "open"
    assert not claim_locks(board_repo)


def test_assign_owner_refuses_unmet_dependencies(board_repo):
    seed_dependency_pair(board_repo)
    r = tickets(board_repo, "assign", "T-002", "--owner", "worker")
    assert r.returncode == 1
    assert "unfinished dependencies" in (r.stdout + r.stderr)
    assert ticket(board_repo, "T-002")["status"] == "open"
    assert not claim_locks(board_repo)


def test_claim_succeeds_after_dependency_released(board_repo):
    seed_dependency_pair(board_repo)
    assert tickets(board_repo, "claim", "T-001").returncode == 0
    assert tickets(board_repo, "done", "T-001", "--force", "--notes", "ready").returncode == 0
    # Done without accept is not released on current main; plant override.
    mark_released(board_repo, "T-001")
    r = tickets(board_repo, "claim", "T-002")
    assert r.returncode == 0, r.stdout + r.stderr
    assert ticket(board_repo, "T-002")["status"] == "claimed"
    assert ticket(board_repo, "T-002")["owner"] == "worker"


def test_t642_deps001_dependency_ordering_blocked_explicit_claim(board_repo):
    """Strict T-642 negative case: claim must fail before upstream is released."""
    seed_dependency_pair(board_repo)
    assert tickets(board_repo, "next").returncode == 0
    assert ticket(board_repo, "T-001")["status"] == "claimed"
    detail = tickets(board_repo, "show", "T-002", "--json")
    assert detail.returncode == 0
    blocked = json.loads(detail.stdout)
    assert blocked["status"] == "open"
    assert blocked["deps"] == ["T-001"]
    assert tickets(board_repo, "next", "--another").returncode == 1
    blocked_claim = tickets(board_repo, "claim", "T-002")
    assert blocked_claim.returncode == 1
    assert "unfinished dependencies" in (blocked_claim.stdout + blocked_claim.stderr)
    assert ticket(board_repo, "T-002")["status"] == "open"
    assert tickets(
        board_repo, "done", "T-001", "--force", "--notes", "API ready"
    ).returncode == 0
    mark_released(board_repo, "T-001")
    assert tickets(board_repo, "claim", "T-002").returncode == 0
    assert ticket(board_repo, "T-002")["status"] == "claimed"
