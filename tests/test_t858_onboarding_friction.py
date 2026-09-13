"""T-858: onboarding CLI friction for a new leadership seat."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def run(board, *args, agent="operator", env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent,
             HOME=str(Path(board).resolve().parent.parent / "home"))
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, env=e, cwd=str(Path(board).parent))


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    assert run(b, "create", "Work", "--role", "docs").returncode == 0
    return b


def test_bound_alias_refuses_without_transfer_and_docs_mention_it(board):
    assert run(board, "join", "old-ceo", "--roles", "master", "--alias", "ceo").returncode == 0
    refused = run(board, "join", "new-ceo", "--roles", "master", "--alias", "ceo")
    assert refused.returncode != 0
    assert "--transfer" in (refused.stderr + refused.stdout)
    xfer = run(board, "join", "new-ceo", "--roles", "master", "--alias", "ceo", "--transfer")
    assert xfer.returncode == 0, xfer.stderr + xfer.stdout
    howto = (ROOT / "docs" / "handoffs" / "SEATS_AND_HOOKS.md").read_text()
    assert "--transfer" in howto
    help_out = run(board, "join", "--help").stdout
    assert "--transfer" in help_out


def test_self_prints_identity_roles_and_endpoint(board):
    assert run(board, "join", "atman-lead", "--roles", "master,review",
               "--alias", "ceo", "--harness", "cursor").returncode == 0
    out = run(board, "self", agent="atman-lead").stdout
    assert "identity: atman-lead" in out
    assert "roles: master, review" in out or "roles: master,review" in out
    assert "alias: ceo" in out
    assert "endpoint:" in out
    unset = run(board, "self", env={"TICKET_AGENT": ""}).stdout
    assert "identity: (unset)" in unset
    assert "script:" in unset


def test_join_ceo_loop_uses_current_cos_holder(board):
    coord = board / "coordination"
    coord.mkdir()
    (coord / "state.json").write_text(json.dumps({
        "schema": 1, "agents": {}, "roles": {
            "steer.chief-of-staff": {"holder": "atman-cos-live", "role": "steer.chief-of-staff"},
        }, "handovers": [], "history": [],
    }) + "\n")
    r = run(board, "join", "atman-new-ceo", "--roles", "master")
    assert r.returncode == 0, r.stderr
    assert "tickets msg --to atman-cos-live" in r.stdout
    assert "tickets msg --to cursor" not in r.stdout


def test_role_list_works_without_ticket_agent(board):
    coord = board / "coordination"
    coord.mkdir()
    (coord / "state.json").write_text(json.dumps({
        "schema": 1, "agents": {},
        "roles": {"steer.ceo": {"holder": "lead", "role": "steer.ceo"}},
        "handovers": [], "history": [],
    }) + "\n")
    env = {"TICKET_AGENT": ""}
    r = run(board, "role", "list", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "IDs must be" not in (r.stderr + r.stdout)
    assert "steer.ceo" in r.stdout


def test_objective_exit_updates_without_set(board):
    assert run(board, "objective", "Ship V1", agent="boss").returncode == 0
    silent = run(board, "objective", "--exit", "demo recorded", agent="boss")
    assert silent.returncode == 0, silent.stderr
    assert "exit criterion set" in silent.stdout
    rec = json.loads((board / "objective.json").read_text())
    assert rec["exit_criterion"] == "demo recorded"
    again = run(board, "objective", "--exit", "review queue empty", agent="boss")
    assert again.returncode == 0, again.stderr
    rec = json.loads((board / "objective.json").read_text())
    assert rec["exit_criterion"] == "review queue empty"
    empty = board.parent / "other" / ".tickets"
    empty.mkdir(parents=True)
    missing = run(empty, "objective", "--exit", "too soon", agent="boss")
    assert missing.returncode != 0
    assert "no objective set" in (missing.stderr + missing.stdout)


def test_review_followup_skips_legacy_and_self(board, monkeypatch):
    sys.path.insert(0, str(ROOT))
    import tickets as tk
    assert run(board, "join", "sol-planner", "--roles", "master").returncode == 0
    assert run(board, "join", "live-cos", "--roles", "review", agent="live-cos").returncode == 0
    assert run(board, "master", "take", agent="sol-planner").returncode == 0
    assert run(board, "master", "cos", "live-cos", agent="sol-planner").returncode == 0
    coord = board / "coordination"
    coord.mkdir(exist_ok=True)
    (coord / "state.json").write_text(json.dumps({
        "schema": 1, "agents": {},
        "roles": {
            "steer.ceo": {"holder": "sol-planner", "role": "steer.ceo"},
            "steer.chief-of-staff": {"holder": "live-cos", "role": "steer.chief-of-staff"},
        },
        "handovers": [], "history": [],
    }) + "\n")
    monkeypatch.setenv("TICKET_AGENT", "live-cos")
    monkeypatch.setenv("TICKETS_DIR", str(board))
    seats = tk._finish_followup(str(board), "T-001", "review")
    assert "cursor" not in seats
    assert "atman-ceo" not in seats
    assert "live-cos" not in seats
    assert seats == ["sol-planner"]
    msgs = (board / "messages.jsonl").read_text()
    assert '"to": "cursor"' not in msgs
    assert '"to": "atman-ceo"' not in msgs
    assert '"to": "sol-planner"' in msgs
