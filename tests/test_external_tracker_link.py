"""T-014: board tickets can carry a free-text external-tracker id (Jira,
GitHub, or nothing), so the mapping from board ticket to tracker id lives on
the ticket instead of buried in a hand-maintained doc.

Runs the real CLI as a subprocess against a throwaway board, same pattern as
test_note_attribution.py, so this exercises exactly what `tickets` returns to
an agent -- not the in-process functions.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="", cwd=None, input=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    where = cwd or (board.parent if board.parent.is_dir() else Path("/"))
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, env=e, cwd=where, input=input)


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


def test_create_stores_external_and_show_json_carries_it(repo):
    r = run(board_of(repo), "create", "Mirror this in Jira", "--external", "MED-748",
            cwd=repo)
    assert r.returncode == 0, r.stderr
    tid = r.stdout.split()[1]
    shown = json.loads(run(board_of(repo), "show", tid, "--json", cwd=repo).stdout)
    assert shown["external"] == "MED-748"


def test_a_ticket_with_no_external_defaults_to_empty_string(repo):
    r = run(board_of(repo), "create", "Board-only, nothing to mirror", cwd=repo)
    tid = r.stdout.split()[1]
    shown = json.loads(run(board_of(repo), "show", tid, "--json", cwd=repo).stdout)
    assert shown["external"] == ""


def test_external_is_visible_in_show_list_and_map_text_output(repo):
    r = run(board_of(repo), "create", "Visible everywhere", "--external", "GH-99", cwd=repo)
    tid = r.stdout.split()[1]
    for verb in (["show", tid], ["list"], ["map"]):
        out = run(board_of(repo), *verb, cwd=repo).stdout
        assert "ext=GH-99" in out, "missing from `tickets %s`: %s" % (" ".join(verb), out)


def test_list_external_looks_up_the_mirroring_ticket_and_only_that_one(repo):
    a = run(board_of(repo), "create", "Tracked in Jira", "--external", "MED-748", cwd=repo)
    run(board_of(repo), "create", "Board-only sibling", cwd=repo)
    tid_a = a.stdout.split()[1]
    out = run(board_of(repo), "list", "--external", "MED-748", cwd=repo).stdout
    assert tid_a in out
    assert "Board-only sibling" not in out


def test_list_external_with_no_match_says_no_tickets_not_an_error(repo):
    run(board_of(repo), "create", "Tracked in Jira", "--external", "MED-748", cwd=repo)
    r = run(board_of(repo), "list", "--external", "MED-999", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert "no tickets" in r.stdout


def test_assign_external_changes_it_and_is_noted(repo):
    a = run(board_of(repo), "create", "Wrong id at first", "--external", "MED-1", cwd=repo)
    tid = a.stdout.split()[1]
    r = run(board_of(repo), "assign", tid, "--external", "MED-2", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert "external=MED-2" in r.stdout
    shown = json.loads(run(board_of(repo), "show", tid, "--json", cwd=repo).stdout)
    assert shown["external"] == "MED-2"
    assert any("external=MED-2" in n["text"] for n in shown["notes"])


def test_assign_external_to_empty_string_clears_it(repo):
    a = run(board_of(repo), "create", "Was tracked, now board-only", "--external", "MED-1",
            cwd=repo)
    tid = a.stdout.split()[1]
    run(board_of(repo), "assign", tid, "--external", "", cwd=repo)
    shown = json.loads(run(board_of(repo), "show", tid, "--json", cwd=repo).stdout)
    assert shown["external"] == ""


def test_plan_bulk_create_accepts_external_per_ticket(repo):
    payload = json.dumps({"tickets": [
        {"title": "First", "external": "MED-10"},
        {"title": "Second"},
    ]})
    r = run(board_of(repo), "plan", cwd=repo, input=payload)
    assert r.returncode == 0, r.stderr
    tickets = json.loads(run(board_of(repo), "list", "--json", cwd=repo).stdout)
    by_title = {t["title"]: t for t in tickets}
    assert by_title["First"]["external"] == "MED-10"
    assert by_title["Second"]["external"] == ""
