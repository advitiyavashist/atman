"""T-488: _sha_is_post_flag ancestry fallback when prefix table is silent."""

import json
import os
import subprocess

import ticket_board.scheduler as sched
from ticket_board.scheduler import (
    ERA_POST,
    ERA_PRE,
    ERA_UNKNOWN,
    _sha_is_post_flag,
    clear_sha_post_flag_cache,
    render_score_table,
    row_era,
    score_shadow,
)
from test_scheduler_score import (
    _finish_with_turns,
    _join,
    _score_agent,
    _stamp,
    _write_jsonl,
)
from test_wakeup import board, run  # noqa: F401


def _git_env():
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        env.pop(var, None)
    return env


def _init_linear_repo(tmp_path):
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    env = _git_env()

    def commit(msg):
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
        subprocess.run(
            ["git", "commit", "-q", "-m", msg, "--allow-empty"], cwd=repo, check=True, env=env)
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True, env=env).strip()

    pre_sha = commit("pre")
    flag_sha = commit("flag")
    post_sha = commit("post")
    return repo, pre_sha, flag_sha, post_sha


def _bind_repo(monkeypatch, repo, flag_sha):
    monkeypatch.setattr(sched, "FLAG_PIN", flag_sha)
    monkeypatch.setattr(sched, "_scheduler_repo_root", lambda: str(repo))
    clear_sha_post_flag_cache()


def test_ancestry_descendant_not_in_prefix_table_is_post(tmp_path, monkeypatch):
    repo, _pre, flag_sha, post_sha = _init_linear_repo(tmp_path)
    _bind_repo(monkeypatch, repo, flag_sha)
    assert _sha_is_post_flag(post_sha) is True
    evs = [
        {
            "v": 1,
            "at": "2026-09-08T10:00:00Z",
            "kind": "run_start",
            "ticket": "T-001",
            "agent": "alice",
            "run_no": 1,
            "release_sha": post_sha,
        }
    ]
    assert row_era(evs) == ERA_POST


def test_ancestry_ancestor_not_in_prefix_table_is_pre(tmp_path, monkeypatch):
    repo, pre_sha, flag_sha, _post = _init_linear_repo(tmp_path)
    _bind_repo(monkeypatch, repo, flag_sha)
    assert _sha_is_post_flag(pre_sha) is False
    evs = [
        {
            "v": 1,
            "at": "2026-05-01T10:00:00Z",
            "kind": "run_start",
            "ticket": "T-001",
            "agent": "alice",
            "run_no": 1,
            "release_sha": pre_sha,
        }
    ]
    assert row_era(evs) == ERA_PRE


def test_sha_absent_from_repo_is_unknown(tmp_path, monkeypatch):
    repo, _pre, flag_sha, _post = _init_linear_repo(tmp_path)
    _bind_repo(monkeypatch, repo, flag_sha)
    missing = "0" * 40
    assert _sha_is_post_flag(missing) is None


def test_git_missing_is_unknown(tmp_path, monkeypatch):
    repo, _pre, flag_sha, post_sha = _init_linear_repo(tmp_path)
    _bind_repo(monkeypatch, repo, flag_sha)

    def _no_git(*args, **kwargs):
        return None

    monkeypatch.setattr(sched, "_git_run", _no_git)
    clear_sha_post_flag_cache()
    assert _sha_is_post_flag(post_sha) is None


def test_prefix_table_wins_without_git_call(monkeypatch):
    calls = []

    def _track_git(*args, **kwargs):
        calls.append(args)
        return None

    monkeypatch.setattr(sched, "_git_run", _track_git)
    clear_sha_post_flag_cache()
    assert _sha_is_post_flag("21ca63cdeadbeef") is False
    assert _sha_is_post_flag("db6229ddeadbeef") is True
    assert calls == []


def test_scorecard_header_release_sha_resolution(board, tmp_path, monkeypatch):
    repo, pre_sha, flag_sha, post_sha = _init_linear_repo(tmp_path)
    _bind_repo(monkeypatch, repo, flag_sha)
    _join(board, "alice", "backend", "opus", "high")
    events = []
    _stamp(board, "T-001", "alice", "backend", 1)
    events.extend(_finish_with_turns("T-001", "alice", "opus", 2, "2026-05-01",
                                     release_sha=pre_sha))
    _stamp(board, "T-002", "alice", "backend", 1)
    events.extend(_finish_with_turns("T-002", "alice", "opus", 2, "2026-09-08",
                                     release_sha=post_sha))
    _stamp(board, "T-003", "alice", "backend", 1)
    events.extend(_finish_with_turns("T-003", "alice", "opus", 2, "2026-09-08"))
    _write_jsonl(board, events)
    tickets = [json.loads(p.read_text()) for p in board.glob("T-*.json")]
    workforce = json.loads((board / "workforce.json").read_text())
    roles = json.loads((board / "roles.json").read_text())
    rep = score_shadow(
        __import__("ticket_board.turns").turns.load_trajectory_events(str(board)),
        tickets, workforce, roles, str(board), _score_agent)
    assert rep["release_sha_resolution"]["distinct"] == 2
    assert rep["release_sha_resolution"]["post"] == 1
    assert rep["release_sha_resolution"]["pre"] == 1
    assert rep["release_sha_resolution"]["unresolved"] == 0
    text = render_score_table(rep)
    assert "release_sha resolution: 2 distinct shas, 1 post / 1 pre / 0 unresolved" in text
    by_id = {r["ticket"]: r for r in rep["rows"]}
    assert by_id["T-001"]["era"] == ERA_PRE
    assert by_id["T-002"]["era"] == ERA_POST
    assert by_id["T-003"]["era"] == ERA_UNKNOWN
