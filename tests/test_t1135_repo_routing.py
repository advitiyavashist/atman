"""Real Git repository boundaries and both automatic-claim entry points."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ticket_board.repo_routing import filter_for_checkout
from test_merge_repo_identity import _git

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repos(tmp_path):
    paths = [tmp_path / name for name in ("steer", "atman")]
    for path in paths:
        path.mkdir()
        _git(path, "init", "-q")
        _git(path, "commit", "--allow-empty", "-m", "initial")
        _git(path, "remote", "add", "origin", "https://github.com/example/%s.git" % path.name)
    return paths


def test_explicit_repo_wins_and_ssh_matches(repos):
    steer, atman = repos
    wrong = {"repo": "https://github.com/example/atman.git", "worktree": str(steer)}
    right = {"repo": "git@github.com:example/steer.git", "worktree": str(atman)}
    unattributed = {}
    assert filter_for_checkout([wrong, unattributed, right], str(steer)) == (
        [unattributed, right], [wrong])


def test_hints_and_linked_worktree(repos, tmp_path):
    steer, atman = repos
    linked = tmp_path / "worker"
    _git(steer, "worktree", "add", "-b", "worker", str(linked))
    local = {"worktree": str(steer)}
    foreign = {"worktree": str(atman)}
    branch = {"branch": "worker"}
    unknown = {"branch": "missing"}
    stale = {"worktree": str(tmp_path / "missing"), "branch": "worker"}
    relative = {"worktree": "."}
    # Only `foreign` resolves to another repository. An unknown branch, a
    # worktree path that no longer exists and a relative hint establish
    # nothing, so they stay claimable rather than being skipped.
    assert filter_for_checkout([local, foreign, branch, unknown, stale, relative], str(linked)) == (
        [local, branch, unknown, stale, relative], [foreign])


def test_unknown_checkout_preserves_legacy_and_git_env_cannot_redirect(repos, tmp_path, monkeypatch):
    steer, atman = repos
    ticket = {"repo": "https://github.com/example/atman.git"}
    assert filter_for_checkout([ticket, {}], str(tmp_path)) == ([ticket, {}], [])
    monkeypatch.setenv("GIT_DIR", str(atman / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(atman))
    assert filter_for_checkout([ticket], str(steer)) == ([], [ticket])
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    _git(steer, "remote", "remove", "origin")
    assert filter_for_checkout([{}], str(steer)) == ([{}], [])


@pytest.mark.parametrize("entry", ["tickets.py", "src/ticket_board/cli.py"])
@pytest.mark.parametrize("has_matching", [True, False])
def test_next_never_attempts_cross_repo_claim(repos, monkeypatch, capsys, entry, has_matching):
    steer, _ = repos
    spec = importlib.util.spec_from_file_location("routing_cli", ROOT / entry)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.chdir(steer)
    wrong = {"id": "T-001", "repo": "https://github.com/example/atman.git", "status": "open"}
    right = {"id": "T-002", "repo": "git@github.com:example/steer.git", "status": "open"}
    tickets = [wrong, right] if has_matching else [wrong]
    for name, value in {
        "print_usage_header": None, "_refuse_limited_seat": None,
        "roles_for": ["backend"], "load_all": tickets, "_held_claimed": [],
        "unblocked": tickets, "can_do": True, "_reservation_blocks": False,
        "_ticket_on_hold": False, "_reopen_blocks_automation": False,
        "active_sprint": None, "cost_rank": 2, "_reserved_agent": "",
        "checkin": None, "detail": "claimed matching ticket", "knowledge_context": "",
        "worktree_warning": "", "_next_refusal_parts": ([], []),
    }.items():
        monkeypatch.setattr(cli, name, lambda *a, _v=value, **k: _v, raising=False)
    monkeypatch.setattr(cli, "_filter_ready", lambda ts, roles: ts)
    monkeypatch.setattr(cli, "_worktree_gc", lambda: SimpleNamespace(is_automated=lambda t: False))
    attempts = []

    def claim(board, tid, owner, **kw):
        attempts.append(tid)
        return right, []

    monkeypatch.setattr(cli, "try_claim_one_active", claim)
    args = SimpleNamespace(owner="worker", role="backend", another=False)
    if has_matching:
        cli.cmd_next(args, "unused")
        assert attempts == ["T-002"]
    else:
        with pytest.raises(SystemExit):
            cli.cmd_next(args, "unused")
        assert attempts == []
        assert "repository is not this checkout's" in capsys.readouterr().out


def test_unattributed_ticket_stays_claimable(repos, tmp_path):
    """The T-1135 REJECT: absence of a repository is not a mismatch.

    Every ticket `atm create` wrote before this branch existed carries no
    repo, no worktree and no branch, while any real clone has an origin.
    Skipping those made `atm next` refuse on ordinary boards.
    """
    steer, atman = repos
    bare = {"id": "T-001"}
    stale_tree = {"id": "T-002", "worktree": str(tmp_path / "deleted-long-ago")}
    relative = {"id": "T-003", "worktree": "."}
    unknown_branch = {"id": "T-004", "branch": "some/branch-that-is-nowhere"}
    foreign = {"id": "T-005", "repo": "https://github.com/example/atman.git"}
    ready, skipped = filter_for_checkout(
        [bare, stale_tree, relative, unknown_branch, foreign], str(steer))
    assert [t["id"] for t in ready] == ["T-001", "T-002", "T-003", "T-004"]
    assert [t["id"] for t in skipped] == ["T-005"]


def test_target_repo_routes_without_touching_the_review_pin(repos):
    steer, atman = repos
    here = {"id": "T-001", "target_repo": "github.com/example/steer"}
    elsewhere = {"id": "T-002", "target_repo": "git@github.com:example/atman.git"}
    # An explicit repo still wins over the create-time hint.
    pinned = {"id": "T-003", "repo": "https://github.com/example/steer.git",
              "target_repo": "github.com/example/atman"}
    ready, skipped = filter_for_checkout([here, elsewhere, pinned], str(steer))
    assert [t["id"] for t in ready] == ["T-001", "T-003"]
    assert [t["id"] for t in skipped] == ["T-002"]


@pytest.mark.parametrize("entry", ["tickets.py", "src/ticket_board/cli.py"])
def test_fresh_clone_with_origin_can_still_claim_a_new_ticket(tmp_path, entry):
    """End-to-end repro from the REJECT, against the real CLI.

    A new repo with an origin, `init`, `join`, `create`, then `next` must
    claim the ticket -- this exited 1 at c4e6c775.
    """
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "--allow-empty", "-m", "initial")
    _git(repo, "remote", "add", "origin", "https://github.com/example/project.git")
    tool = ROOT / entry
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "home"),
        "TICKETS_DIR": str(repo / ".tickets"),
        "PYTEST_CURRENT_TEST": "tests/test_t1135_repo_routing.py::fresh (call)",
    }
    (tmp_path / "home").mkdir()

    def cli(*args, agent=None):
        e = dict(env)
        if agent:
            e["TICKET_AGENT"] = agent
        return subprocess.run([sys.executable, str(tool), *args], cwd=str(repo),
                              env=e, capture_output=True, text=True)

    assert cli("init").returncode == 0
    assert cli("join", "alice", "--roles", "backend", agent="alice").returncode == 0
    made = cli("create", "plain work", "--role", "backend", agent="alice")
    assert made.returncode == 0, made.stderr + made.stdout
    nxt = cli("next", agent="alice")
    assert nxt.returncode == 0, nxt.stderr + nxt.stdout
    assert "T-001" in nxt.stdout
    # The ticket was typed here, so it is stamped here and still routes here.
    record = json.loads((repo / ".tickets" / "T-001.json").read_text())
    assert record["target_repo"] == "github.com/example/project"
    assert "repo" not in record, "create must not forge a review pin"


def test_assign_target_repo_attributes_and_clears_an_existing_ticket(tmp_path):
    """A legacy ticket can be attributed without hand-editing the board.

    The 294 tickets that predate any repo field are claimable from
    anywhere by design; this is how a master stops one being offered to
    seats on the wrong repository.
    """
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "--allow-empty", "-m", "initial")
    _git(repo, "remote", "add", "origin", "https://github.com/example/project.git")
    board = repo / ".tickets"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "home"),
        "TICKETS_DIR": str(board),
        "PYTEST_CURRENT_TEST": "tests/test_t1135_repo_routing.py::assign (call)",
    }
    (tmp_path / "home").mkdir()

    def cli(*args, agent="boss"):
        return subprocess.run([sys.executable, str(ROOT / "tickets.py"), *args],
                              cwd=str(repo), env=dict(env, TICKET_AGENT=agent),
                              capture_output=True, text=True)

    assert cli("init").returncode == 0
    made = cli("create", "cross-repo work", "--role", "backend",
               "--target-repo", "https://github.com/example/other.git")
    assert made.returncode == 0, made.stderr + made.stdout
    record = json.loads((board / "T-001.json").read_text())
    assert record["target_repo"] == "https://github.com/example/other.git"
    # It names another repository, so this checkout must not be offered it.
    assert filter_for_checkout([record], str(repo)) == ([], [record])

    fixed = cli("assign", "T-001", "--target-repo", "https://github.com/example/project.git")
    assert fixed.returncode == 0, fixed.stderr + fixed.stdout
    record = json.loads((board / "T-001.json").read_text())
    assert filter_for_checkout([record], str(repo)) == ([record], [])

    cleared = cli("assign", "T-001", "--target-repo", "-")
    assert cleared.returncode == 0, cleared.stderr + cleared.stdout
    record = json.loads((board / "T-001.json").read_text())
    assert "target_repo" not in record
    # Unknown means claimable from anywhere, never "skip everywhere".
    assert filter_for_checkout([record], str(repo)) == ([record], [])
