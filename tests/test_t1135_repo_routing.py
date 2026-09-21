"""Real Git repository boundaries and both automatic-claim entry points."""
import importlib.util
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
    unknown = {}
    assert filter_for_checkout([wrong, unknown, right], str(steer)) == ([right], [wrong, unknown])


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
    assert filter_for_checkout([local, foreign, branch, unknown, stale, relative], str(linked)) == (
        [local, branch], [foreign, unknown, stale, relative])


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
        assert "repository-mismatched or unattributed" in capsys.readouterr().out
