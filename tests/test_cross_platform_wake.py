import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

TOOL = Path(__file__).resolve().parent.parent / "tickets.py"


def run(board, *args, cwd=None, env_extra=None):
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="test-runner",
               PATH=str(Path(sys.executable).parent) + ":" + os.environ.get("PATH", ""))
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(TOOL)] + list(args),
                          capture_output=True, text=True, cwd=cwd or str(board.parent), env=env)


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    r = run(b, "create", "Setup repo", "--role", "infra", cwd=repo)
    assert r.returncode == 0, r.stderr
    return b


def test_cross_platform_wake_agy_to_cursor(board):
    # Register cursor persistent seat
    r = run(board, "join", "cursor-worker", "--roles", "backend", "--harness", "cursor",
            "--lifecycle", "persistent", env_extra={"CURSOR_CONVERSATION_ID": "chat-cursor-123"})
    assert r.returncode == 0, r.stderr

    # Join agy sender
    r = run(board, "join", "agy-sender", "--roles", "ops", "--harness", "agy")
    assert r.returncode == 0, r.stderr

    # Send message from agy to cursor
    r = run(board, "msg", "--owner", "agy-sender", "--to", "cursor-worker", "Please review T-100")
    assert r.returncode == 0, r.stderr
    assert "posted:" in r.stdout
    assert "wake: cursor-worker ->" in r.stdout

    # Check inbox of cursor-worker
    r = run(board, "inbox", "--owner", "cursor-worker", "--keep")
    assert r.returncode == 0, r.stderr
    assert "Please review T-100" in r.stdout


def test_cross_platform_wake_cursor_to_agy(board):
    # Register agy persistent seat
    r = run(board, "join", "agy-worker", "--roles", "backend", "--harness", "agy",
            "--lifecycle", "persistent", env_extra={"AGY_CONVERSATION_ID": "conv-agy-456"})
    assert r.returncode == 0, r.stderr

    # Join cursor sender
    r = run(board, "join", "cursor-sender", "--roles", "backend", "--harness", "cursor")
    assert r.returncode == 0, r.stderr

    # Send message from cursor to agy
    r = run(board, "msg", "--owner", "cursor-sender", "--to", "agy-worker", "Tests passing on main")
    assert r.returncode == 0, r.stderr
    assert "posted:" in r.stdout
    assert "wake: agy-worker ->" in r.stdout

    # Check agy-inbox hook
    r = run(board, "hook-run", "--agent", "agy-worker", "--event", "agy-inbox")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout.strip())
    assert "injectSteps" in data
    assert "Tests passing on main" in data["injectSteps"][0]["ephemeralMessage"]


def test_broadcast_all_wakes_all_persistent_agents(board):
    # Register persistent seats across multiple harnesses
    for name, harness in [("cursor-p", "cursor"), ("agy-p", "agy"), ("devin-p", "devin")]:
        r = run(board, "join", name, "--roles", "backend", "--harness", harness,
                "--lifecycle", "persistent")
        assert r.returncode == 0, r.stderr

    # Broadcast message to all
    r = run(board, "msg", "--owner", "lead", "--to", "all", "All hands: deploy window open")
    assert r.returncode == 0, r.stderr
    assert "posted:" in r.stdout
    assert "wake: cursor-p ->" in r.stdout
    assert "wake: agy-p ->" in r.stdout
    assert "wake: devin-p ->" in r.stdout

    # Verify each inbox received the broadcast
    for name in ["cursor-p", "agy-p", "devin-p"]:
        r_inbox = run(board, "inbox", "--owner", name, "--keep")
        assert "All hands: deploy window open" in r_inbox.stdout


def test_broadcast_mention_all_in_body(board):
    # Register persistent seats
    run(board, "join", "cursor-1", "--roles", "backend", "--harness", "cursor", "--lifecycle", "persistent")
    run(board, "join", "agy-1", "--roles", "backend", "--harness", "agy", "--lifecycle", "persistent")

    # Send message with @all in text
    r = run(board, "msg", "--owner", "lead", "Heads up @all: sync in 5m")
    assert r.returncode == 0, r.stderr
    assert "wake: cursor-1 ->" in r.stdout
    assert "wake: agy-1 ->" in r.stdout


def test_pending_exit_code_zero_on_unread_mail(board):
    # worker with role docs (no ready tickets for docs)
    run(board, "join", "worker", "--roles", "docs", "--harness", "cursor", "--lifecycle", "ephemeral")
    r_empty = run(board, "pending", "--agent", "worker")
    assert r_empty.returncode == 1

    # Send mail
    run(board, "msg", "--owner", "lead", "--to", "worker", "--task", "Need assistance")
    r_pending = run(board, "pending", "--agent", "worker")
    assert r_pending.returncode == 0
    assert "task_messages:" in r_pending.stdout
