"""T-015: `priority` was only editable via `assign --priority`, and nothing
in the top-level verb list pointed there -- a master re-ranking mid-setup
read `assign` as "set the owner" and concluded re-ranking was not possible.
`tickets priority <id> <n>` and `tickets retitle <id> <title>` are thin,
discoverable aliases into the same `assign` codepath; no behaviour is new,
only where you find it.

Deliberately no `tickets role <id> <value>` alias: `role` is already a
top-level verb for durable agent roles (list/show/take), so reusing it for
ticket roles would collide with an existing command instead of fixing a gap.

Runs the real CLI as a subprocess, same pattern as test_note_attribution.py.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="", cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    where = cwd or (board.parent if board.parent.is_dir() else Path("/"))
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, env=e, cwd=where)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    return r


def board_of(repo):
    return repo / ".tickets"


def show_json(repo, tid):
    return json.loads(run(board_of(repo), "show", tid, "--json", cwd=repo).stdout)


def test_priority_top_level_verb_is_listed_in_help(repo):
    out = run(board_of(repo), "--help", cwd=repo).stdout
    assert "priority" in out
    assert "retitle" in out


def test_priority_reranks_the_ticket(repo):
    a = run(board_of(repo), "create", "Reprioritize me", "--priority", "2", cwd=repo)
    tid = a.stdout.split()[1]
    r = run(board_of(repo), "priority", tid, "1", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert show_json(repo, tid)["priority"] == 1


def test_priority_records_a_note_same_as_assign_would(repo):
    a = run(board_of(repo), "create", "Reprioritize me", cwd=repo)
    tid = a.stdout.split()[1]
    run(board_of(repo), "priority", tid, "1", "--notes", "escalated by Advitiya", cwd=repo)
    notes = show_json(repo, tid)["notes"]
    assert any("priority=1" in n["text"] and "escalated by Advitiya" in n["text"]
               for n in notes)


def test_priority_requires_a_value_rather_than_silently_no_opping(repo):
    a = run(board_of(repo), "create", "Reprioritize me", cwd=repo)
    tid = a.stdout.split()[1]
    r = run(board_of(repo), "priority", tid, cwd=repo)
    assert r.returncode != 0
    assert "NO CHANGE WAS MADE" in r.stdout + r.stderr


def test_retitle_renames_the_ticket(repo):
    a = run(board_of(repo), "create", "Old title", cwd=repo)
    tid = a.stdout.split()[1]
    r = run(board_of(repo), "retitle", tid, "New title", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert show_json(repo, tid)["title"] == "New title"


def test_priority_and_assign_priority_are_the_same_underlying_change(repo):
    """The alias must not diverge from `assign` -- same note text, same
    field, so anything that already parses `assign: priority=N` notes
    still works when the alias is used instead."""
    a = run(board_of(repo), "create", "Via assign", cwd=repo)
    b = run(board_of(repo), "create", "Via alias", cwd=repo)
    tid_a, tid_b = a.stdout.split()[1], b.stdout.split()[1]
    run(board_of(repo), "assign", tid_a, "--priority", "1", cwd=repo)
    run(board_of(repo), "priority", tid_b, "1", cwd=repo)
    note_a = show_json(repo, tid_a)["notes"][-1]["text"]
    note_b = show_json(repo, tid_b)["notes"][-1]["text"]
    assert note_a == note_b == "assign: priority=1"


def test_role_verb_is_untouched_and_still_means_durable_agent_role(repo):
    """No ticket-role alias was added under `role` -- it already means
    something else (list/show/take a durable agent role). Confirms the
    existing verb still resolves and was not shadowed."""
    run(board_of(repo), "create", "just to bring the board into existence", cwd=repo)
    r = run(board_of(repo), "role", "list", agent="smoke", cwd=repo)
    assert r.returncode == 0, r.stderr
