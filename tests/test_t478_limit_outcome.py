"""T-478: run_end.outcome=limit is an exit signal, not a log grep.

Throwaway board (TICKETS_DIR via the wakeup fixture). GIT_DIR/COMMON/WORK_TREE
cleared so an ambient checkout cannot leak into the fixture repo (T-392).
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from test_trajectories import _fake_harness, events
from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"

# Anthropic Messages API error envelope (not an invented string).
ANTHROPIC_RATE_LIMIT = (
    '{"type":"error","error":{"type":"rate_limit_error",'
    '"message":"This request would exceed your account rate limit."}}'
)
# Every healthy Codex turn writes this. MASTER item 7: not a usage limit.
CODEX_RATE_LIMITS_TELEMETRY = (
    '{"type":"token_count","info":null,"rate_limits":{"limit_id":"premium"}}'
)


def _isolate_git(monkeypatch):
    for var in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)


def _commit(repo, msg="work"):
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", msg],
        check=True,
        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def _alice_on_docs(board, monkeypatch):
    _isolate_git(monkeypatch)
    repo = board.parent
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    _commit(repo, "ignore")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"],
                   check=True)
    run(board, "join", "alice", "--roles", "docs", "--tool", "claude", "--model", "opus",
        agent="alice", cwd=repo)
    run(board, "next", "--role", "docs", agent="alice", cwd=repo)
    return repo, "T-001"


def test_exit0_prose_about_limits_with_bound_write_is_not_limit(board, tmp_path, monkeypatch):
    """r-opus-authz-1: exit=0, bound write, log says 'rate limit' / 'resets at'."""
    repo, tid = _alice_on_docs(board, monkeypatch)
    sh = tmp_path / "talk.sh"
    sh.write_text(
        "#!/bin/sh\n"
        "echo 'discussing the rate limit; session resets at 23:00Z'\n"
        "%s %s update %s working-through-the-limit-talk\n"
        % (sys.executable, TOOL, tid))
    sh.chmod(sh.stat().st_mode | stat.S_IEXEC)
    r = run(board, "watch", "--agent", "alice", "--once", "--exec", str(sh),
            "--cwd", str(repo), agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    e = events(board, kind="run_end")[-1]
    assert e["exit"] == 0
    assert e.get("bound_write") is True
    assert "outcome" not in e, e


def test_exit1_harness_limit_text_is_limit_and_does_not_count_a_turn(
        board, tmp_path, monkeypatch):
    repo, tid = _alice_on_docs(board, monkeypatch)
    fake = _fake_harness(tmp_path, "echo 'Claude usage limit reached'\nexit 1\n")
    r = run(board, "watch", "--agent", "alice", "--once", "--exec", str(fake),
            "--cwd", str(repo), agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    e = events(board, kind="run_end")[-1]
    assert e["exit"] == 1 and e["outcome"] == "limit"
    assert "bound_write" not in e
    report = json.loads(run(board, "turns", "--json", cwd=repo).stdout)
    by = {row["ticket"]: row for row in report["tickets"]}
    assert by[tid]["turns"] is None


def test_exit0_structured_rate_limit_error_without_write_is_limit(
        board, tmp_path, monkeypatch):
    """Real Anthropic error envelope; decision: exit=0 + no bound write => limit."""
    repo, tid = _alice_on_docs(board, monkeypatch)
    fake = _fake_harness(tmp_path, "echo '%s'\n" % ANTHROPIC_RATE_LIMIT)
    r = run(board, "watch", "--agent", "alice", "--once", "--exec", str(fake),
            "--cwd", str(repo), agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    e = events(board, kind="run_end")[-1]
    assert e["exit"] == 0 and e["outcome"] == "limit"
    assert "bound_write" not in e
    report = json.loads(run(board, "turns", "--json", cwd=repo).stdout)
    by = {row["ticket"]: row for row in report["tickets"]}
    assert by[tid]["turns"] is None


def test_exit0_codex_rate_limits_telemetry_is_not_limit(board, tmp_path, monkeypatch):
    """Rule-8: healthy Codex telemetry must not become outcome=limit."""
    repo, _tid = _alice_on_docs(board, monkeypatch)
    fake = _fake_harness(tmp_path, "echo '%s'\n" % CODEX_RATE_LIMITS_TELEMETRY)
    r = run(board, "watch", "--agent", "alice", "--once", "--exec", str(fake),
            "--cwd", str(repo), agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    e = events(board, kind="run_end")[-1]
    assert e["exit"] == 0
    assert "outcome" not in e, e


def test_exit0_structured_rate_limit_with_bound_write_is_not_limit(
        board, tmp_path, monkeypatch):
    """Productive run wins over a dumped API error blob."""
    repo, tid = _alice_on_docs(board, monkeypatch)
    sh = tmp_path / "dump.sh"
    sh.write_text(
        "#!/bin/sh\n"
        "echo '%s'\n"
        "%s %s update %s still-working\n"
        % (ANTHROPIC_RATE_LIMIT, sys.executable, TOOL, tid))
    sh.chmod(sh.stat().st_mode | stat.S_IEXEC)
    r = run(board, "watch", "--agent", "alice", "--once", "--exec", str(sh),
            "--cwd", str(repo), agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    e = events(board, kind="run_end")[-1]
    assert e["exit"] == 0 and e.get("bound_write") is True
    assert "outcome" not in e, e
