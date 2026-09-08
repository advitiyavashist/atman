"""T-438: `tickets merge --push` must fast-forward origin/<trunk> or fail loudly."""
import os
import subprocess

from test_wakeup import board, run
from test_merge_repo_identity import _create, _git, _ignore_board, _status


def _bare_remote(tmp_root, name="origin.git"):
    bare = tmp_root / name
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    return bare


def _remote_main_sha(bare, branch="main"):
    return _git(bare, "rev-parse", "refs/heads/%s" % branch)


def _setup_repo_with_origin(board):
    repo = board.parent
    bare = _bare_remote(board.parent.parent)
    _git(repo, "remote", "add", "origin", str(bare))
    trunk = _git(repo, "symbolic-ref", "--short", "HEAD")
    _git(repo, "push", "-u", "origin", trunk)
    _ignore_board(repo)
    run(board, "master", "take", "--owner", "ceo")
    return repo, bare, trunk


def test_merge_push_fast_forwards_origin_trunk(board):
    repo, bare, trunk = _setup_repo_with_origin(board)
    tid = _create(board, "Pushable work", role="backend")
    run(board, "claim", tid, agent="alice")
    _git(repo, "checkout", "-b", "alice/push-me")
    (repo / "p.txt").write_text("push")
    _git(repo, "add", "p.txt")
    _git(repo, "commit", "-m", "push")
    r = run(board, "review", tid, "--notes", "ready", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    _git(repo, "checkout", trunk)
    before_remote = _remote_main_sha(bare, trunk)

    r = run(board, "merge", "alice/push-me", "--push", "--no-test", agent="ceo", cwd=repo)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "PUSH FAILED" not in r.stdout
    assert "pushed to origin/%s" % trunk in r.stdout

    local_main = _git(repo, "rev-parse", trunk)
    remote_main = _remote_main_sha(bare, trunk)
    assert local_main == remote_main
    assert remote_main != before_remote
    assert _status(board, tid) == "done"


def test_merge_push_failure_leaves_ticket_in_review(board):
    repo, bare, trunk = _setup_repo_with_origin(board)
    tid = _create(board, "Push blocked", role="backend")
    run(board, "claim", tid, agent="alice")
    _git(repo, "checkout", "-b", "alice/blocked")
    (repo / "b.txt").write_text("blocked")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "blocked")
    r = run(board, "review", tid, "--notes", "ready", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    _git(repo, "checkout", trunk)

    hijack = board.parent.parent / "hijack"
    hijack.mkdir()
    subprocess.run(["git", "clone", "-q", str(bare), str(hijack)], check=True)
    _git(hijack, "checkout", trunk)
    (hijack / "other.txt").write_text("other")
    _git(hijack, "add", "other.txt")
    _git(hijack, "commit", "-m", "hijack")
    _git(hijack, "push", "origin", trunk)

    r = run(board, "merge", "alice/blocked", "--push", "--no-test", agent="ceo", cwd=repo)
    assert r.returncode == 4, r.stderr + r.stdout
    out = r.stdout + r.stderr
    assert "PUSH FAILED" in out
    assert _status(board, tid) == "review"
