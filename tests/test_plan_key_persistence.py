"""T-838 / T-016: persist plan keys so a later plan can depend on an earlier one.

`plan` used to resolve a JSON item's `key` only against the other items in that
same stdin payload. A second `plan` call referencing an earlier plan's key
failed with "no such key or id". Ticket ids always worked as a workaround.

Current-invocation keymap wins, then persisted `plan_key`, then raw id.
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


def plan(repo, payload):
    return run(board_of(repo), "plan", cwd=repo, input=json.dumps(payload))


def list_json(repo):
    return json.loads(run(board_of(repo), "list", "--json", cwd=repo).stdout)


def test_a_later_plan_call_can_depend_on_an_earlier_plans_key(repo):
    r1 = plan(repo, {"tickets": [{"title": "Foundation", "key": "foundation"}]})
    assert r1.returncode == 0, r1.stderr
    r2 = plan(repo, {"tickets": [{"title": "Depends on it", "deps": ["foundation"]}]})
    assert r2.returncode == 0, r2.stderr

    by_title = {t["title"]: t for t in list_json(repo)}
    foundation_id = by_title["Foundation"]["id"]
    assert by_title["Depends on it"]["deps"] == [foundation_id]


def test_the_key_survives_as_plan_key_on_the_ticket(repo):
    plan(repo, {"tickets": [{"title": "Foundation", "key": "foundation"}]})
    t = list_json(repo)[0]
    assert t["plan_key"] == "foundation"


def test_a_ticket_created_without_a_key_has_an_empty_plan_key(repo):
    r = run(board_of(repo), "create", "No key here", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert list_json(repo)[0]["plan_key"] == ""


def test_within_one_plan_call_the_key_still_resolves_as_before(repo):
    r = plan(repo, {"tickets": [
        {"title": "Foundation", "key": "foundation"},
        {"title": "Depends on it", "deps": ["foundation"]},
    ]})
    assert r.returncode == 0, r.stderr
    by_title = {t["title"]: t for t in list_json(repo)}
    assert by_title["Depends on it"]["deps"] == [by_title["Foundation"]["id"]]


def test_an_unresolvable_reference_still_fails_clearly_and_rolls_back(repo):
    run(board_of(repo), "create", "Pre-existing, unrelated", cwd=repo)
    before = {t["id"] for t in list_json(repo)}
    r = plan(repo, {"tickets": [{"title": "Broken", "deps": ["never-existed"]}]})
    assert r.returncode != 0
    assert "never-existed" in r.stdout + r.stderr
    after = {t["id"] for t in list_json(repo)}
    assert after == before


def test_a_ticket_id_still_works_directly_as_a_dep_workaround(repo):
    r1 = run(board_of(repo), "create", "Foundation", cwd=repo)
    tid = r1.stdout.split()[1]
    r2 = plan(repo, {"tickets": [{"title": "Depends on it", "deps": [tid]}]})
    assert r2.returncode == 0, r2.stderr
    by_title = {t["title"]: t for t in list_json(repo)}
    assert by_title["Depends on it"]["deps"] == [tid]


def test_current_invocation_key_precedes_a_persisted_same_key(repo):
    plan(repo, {"tickets": [{"title": "Old foundation", "key": "foundation"}]})
    r = plan(repo, {"tickets": [
        {"title": "New foundation", "key": "foundation"},
        {"title": "Depends on it", "deps": ["foundation"]},
    ]})
    assert r.returncode == 0, r.stderr
    by_title = {t["title"]: t for t in list_json(repo)}
    assert by_title["Depends on it"]["deps"] == [by_title["New foundation"]["id"]]
