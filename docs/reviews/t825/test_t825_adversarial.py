"""Independent adversarial checks for T-804 at exact candidate 97d4b1c.

Run with:
    ATMAN_CANDIDATE_ROOT=/private/tmp/atman-t825-candidate \
      python3 -m pytest -q docs/reviews/t825/test_t825_adversarial.py
"""

import json
import hashlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


CANDIDATE = Path(
    os.environ.get(
        "ATMAN_CANDIDATE_ROOT",
        str(Path(__file__).resolve().parents[3]),
    )
).resolve()
TOOLS = [CANDIDATE / "tickets.py", CANDIDATE / "src/ticket_board/cli.py"]


def make_board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
        env=dict(
            os.environ,
            GIT_AUTHOR_NAME="review",
            GIT_AUTHOR_EMAIL="review@example.test",
            GIT_COMMITTER_NAME="review",
            GIT_COMMITTER_EMAIL="review@example.test",
        ),
    )
    board = repo / ".tickets"
    board.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    return repo, board, home


def run(tool, repo, board, home, *args, agent=""):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TICKETS_DIR": str(board),
        "TICKETS_CACHE_DIR": str(home / "cache"),
        "PYTEST_CURRENT_TEST": "docs/reviews/t825/test_t825_adversarial.py",
    }
    if agent:
        env["TICKET_AGENT"] = agent
    return subprocess.run(
        [sys.executable, str(tool), *args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
    )


def records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def alias_transfer(tool, tmp_path):
    repo, board, home = make_board(tmp_path)
    first = run(
        tool,
        repo,
        board,
        home,
        "join",
        "atman-ceo-cursor",
        "--roles",
        "master",
        "--harness",
        "cursor",
        "--alias",
        "ceo",
    )
    assert first.returncode == 0, first.stderr + first.stdout
    second = run(
        tool,
        repo,
        board,
        home,
        "join",
        "atman-ceo-codex",
        "--roles",
        "master",
        "--harness",
        "codex",
        "--alias",
        "ceo",
        "--transfer",
    )
    assert second.returncode == 0, second.stderr + second.stdout

    return board


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_alias_transfer_is_audited(tool, tmp_path):
    board = alias_transfer(tool, tmp_path)
    audit = records(board / "messages.jsonl")
    assert any(
        "alias ceo" in message.get("text", "")
        and "atman-ceo-cursor" in message.get("text", "")
        and "atman-ceo-codex" in message.get("text", "")
        for message in audit
    ), audit


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_alias_transfer_has_one_metadata_owner(tool, tmp_path):
    board = alias_transfer(tool, tmp_path)
    aliases = json.loads((board / "aliases.json").read_text())
    workforce = json.loads((board / "workforce.json").read_text())
    assert aliases == {"ceo": "atman-ceo-codex"}
    assert workforce["atman-ceo-codex"].get("role_alias") == "ceo"
    assert "role_alias" not in workforce["atman-ceo-cursor"]


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_alias_transfer_clears_stale_manual_limit_on_incoming_seat(tool, tmp_path):
    repo, board, home = make_board(tmp_path)
    old = run(
        tool, repo, board, home, "join", "atman-ceo-cursor", "--roles", "master",
        "--harness", "cursor", "--alias", "ceo",
    )
    assert old.returncode == 0, old.stderr + old.stdout
    target = run(
        tool, repo, board, home, "join", "atman-ceo-codex", "--roles", "master",
        "--harness", "codex",
    )
    assert target.returncode == 0, target.stderr + target.stdout
    agent_path = board / "agents" / "atman-ceo-codex.json"
    rec = json.loads(agent_path.read_text())
    rec["limit"] = {
        "at": "2026-09-12T15:00:00Z",
        "until": "23:00Z",
        "note": "old Claude-fable session limit",
    }
    agent_path.write_text(json.dumps(rec))

    moved = run(
        tool, repo, board, home, "join", "atman-ceo-codex", "--roles", "master",
        "--harness", "codex", "--alias", "ceo", "--transfer",
    )
    assert moved.returncode == 0, moved.stderr + moved.stdout
    assert "limit" not in json.loads(agent_path.read_text())


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_same_provider_rejoin_preserves_state_and_history(tool, tmp_path):
    repo, board, home = make_board(tmp_path)
    first = run(
        tool, repo, board, home, "join", "stable-seat", "--roles", "backend",
        "--harness", "cursor",
    )
    assert first.returncode == 0, first.stderr + first.stdout
    note = run(
        tool, repo, board, home, "msg", "keep this history", agent="stable-seat"
    )
    assert note.returncode == 0, note.stderr + note.stdout
    agent_path = board / "agents" / "stable-seat.json"
    before = json.loads(agent_path.read_text())
    before["auth_check"] = {"state": "ready", "harness": "cursor"}
    before["limit"] = {"until": "later"}
    agent_path.write_text(json.dumps(before))
    history_before = (board / "messages.jsonl").read_text()

    again = run(
        tool, repo, board, home, "join", "stable-seat", "--roles", "backend",
        "--harness", "cursor",
    )
    assert again.returncode == 0, again.stderr + again.stdout
    after = json.loads(agent_path.read_text())
    assert after["auth_check"] == before["auth_check"]
    assert after.get("runner_context") == before.get("runner_context")
    assert after["limit"] == before["limit"]
    assert "keep this history" in (board / "messages.jsonl").read_text()
    assert history_before in (board / "messages.jsonl").read_text()


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_transfer_refuses_a_seat_holding_a_ticket(tool, tmp_path):
    repo, board, home = make_board(tmp_path)
    joined = run(
        tool, repo, board, home, "join", "working-seat", "--roles", "backend",
        "--harness", "cursor",
    )
    assert joined.returncode == 0, joined.stderr + joined.stdout
    created = run(
        tool, repo, board, home, "create", "held work", "--role", "backend",
        agent="planner",
    )
    assert created.returncode == 0, created.stderr + created.stdout
    claimed = run(tool, repo, board, home, "next", agent="working-seat")
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    refused = run(
        tool, repo, board, home, "join", "working-seat", "--harness", "codex",
        "--transfer",
    )
    assert refused.returncode != 0
    assert "holds a ticket" in refused.stderr + refused.stdout
    workforce = json.loads((board / "workforce.json").read_text())
    assert workforce["working-seat"]["harness"] == "cursor"


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_provider_transfer_clears_native_endpoint_for_both_copies(tool, tmp_path):
    repo, board, home = make_board(tmp_path)
    joined = run(
        tool, repo, board, home, "join", "endpoint-seat", "--roles", "backend",
        "--harness", "cursor",
    )
    assert joined.returncode == 0, joined.stderr + joined.stdout
    digest = hashlib.sha256(str(board.resolve()).encode()).hexdigest()[:16]
    endpoint = home / "cache" / "sessions" / digest / "endpoint-seat.json"
    endpoint.parent.mkdir(parents=True)
    endpoint.write_text(json.dumps({"provider": "cursor", "token": "old-secret"}))
    transferred = run(
        tool, repo, board, home, "join", "endpoint-seat", "--harness", "codex",
        "--transfer",
    )
    assert transferred.returncode == 0, transferred.stderr + transferred.stdout
    assert not endpoint.exists()


def test_failed_spawn_transfer_keeps_old_identity_state(tmp_path):
    tool = CANDIDATE / "tickets.py"
    repo, board, home = make_board(tmp_path)
    joined = run(
        tool,
        repo,
        board,
        home,
        "join",
        "atman-ceo",
        "--roles",
        "master",
        "--harness",
        "cursor",
    )
    assert joined.returncode == 0, joined.stderr + joined.stdout

    agent_path = board / "agents" / "atman-ceo.json"
    before = json.loads(agent_path.read_text())
    before["auth_check"] = {
        "state": "ready",
        "harness": "cursor",
        "identity_label": "cursor-user",
    }
    before["runner_context"] = {"runner_kind": "cursor", "hostname": "old-host"}
    before["limit"] = {"until": "cursor-reset"}
    agent_path.write_text(json.dumps(before))

    # An impossible executable gives a deterministic failed target-provider
    # preflight. A failed transfer must leave the still-enrolled Cursor seat intact.
    failed = run(
        tool,
        repo,
        board,
        home,
        "spawn",
        "atman-ceo",
        "--harness",
        "codex",
        "--transfer",
        "--worktree",
        str(repo),
    )
    assert failed.returncode != 0, failed.stdout
    after = json.loads(agent_path.read_text())
    workforce = json.loads((board / "workforce.json").read_text())
    assert workforce["atman-ceo"]["harness"] == "cursor"
    assert after.get("auth_check") == before["auth_check"]
    assert after.get("runner_context") == before["runner_context"]
    assert after.get("limit") == before["limit"]


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_failed_join_transfer_restores_identity_bytes(tool, tmp_path):
    repo, board, home = make_board(tmp_path)
    joined = run(
        tool, repo, board, home, "join", "atman-ceo", "--roles", "master",
        "--harness", "cursor",
    )
    assert joined.returncode == 0, joined.stderr + joined.stdout
    agent_path = board / "agents" / "atman-ceo.json"
    before = json.loads(agent_path.read_text())
    before["auth_check"] = {
        "state": "ready",
        "harness": "cursor",
        "identity_label": "cursor-user",
    }
    before["runner_context"] = {"runner_kind": "cursor", "hostname": "old-host"}
    before["limit"] = {"until": "cursor-reset", "provider": "cursor"}
    before["adapter_failure"] = {
        "state": "failed",
        "reason": "aborted: tests failed (1 failed, 333 passed)",
        "provider": "cursor",
    }
    agent_path.write_text(json.dumps(before))
    digest = hashlib.sha256(str(board.resolve()).encode()).hexdigest()[:16]
    endpoint = home / "cache" / "sessions" / digest / "atman-ceo.json"
    endpoint.parent.mkdir(parents=True, exist_ok=True)
    endpoint.write_text(json.dumps({"provider": "cursor", "token": "old-secret"}))
    before_bytes = agent_path.read_bytes()

    failed = run(
        tool, repo, board, home, "join", "atman-ceo", "--harness", "custom",
        "--transfer",
    )
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert agent_path.read_bytes() == before_bytes
    assert json.loads(agent_path.read_text())["auth_check"]["harness"] == "cursor"
    assert json.loads((board / "workforce.json").read_text())["atman-ceo"]["harness"] == "cursor"
    assert endpoint.exists()
    assert json.loads(endpoint.read_text())["token"] == "old-secret"


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_stale_provider_limit_does_not_suppress_ready_codex(tool, tmp_path):
    root = CANDIDATE / "tickets.py"
    repo, board, home = make_board(tmp_path)
    joined = run(
        tool, repo, board, home, "join", "atman-ceo-codex", "--roles", "master",
        "--harness", "codex",
    )
    assert joined.returncode == 0, joined.stderr + joined.stdout
    agent_path = board / "agents" / "atman-ceo-codex.json"
    rec = json.loads(agent_path.read_text())
    rec["auth_check"] = {
        "state": "ready",
        "harness": "codex",
        "identity_label": "codex-user",
    }
    rec["runner_context"] = {"runner_kind": "codex", "hostname": "codex-host"}
    rec["limit"] = {
        "at": "2026-01-01T00:00:00Z",
        "until": "never",
        "note": "aborted: tests failed (1 failed, 333 passed)",
        "provider": "claude",
        "kind": "claude",
    }
    agent_path.write_text(json.dumps(rec))
    msg = run(
        root, repo, board, home, "msg", "directed CEO task",
        "--to", "atman-ceo-codex", agent="cursor",
    )
    assert msg.returncode == 0, msg.stderr + msg.stdout
    pending = run(root, repo, board, home, "pending", "--json",
                  agent="atman-ceo-codex")
    payload = json.loads(pending.stdout)
    assert "limited" not in payload
    assert "limited" not in (payload.get("pending") or {})
    blob = json.dumps(payload)
    assert "directed CEO task" in blob or payload.get("messages_to_me") or payload.get("task_messages")


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_transfer_fences_remote_adapter_lease(tool, tmp_path):
    root = CANDIDATE / "tickets.py"
    repo, board, home = make_board(tmp_path)
    joined = run(
        root,
        repo,
        board,
        home,
        "join",
        "remote-ceo",
        "--roles",
        "master",
        "--harness",
        "remote",
    )
    assert joined.returncode == 0, joined.stderr + joined.stdout
    registered = run(
        root,
        repo,
        board,
        home,
        "remote",
        "register",
        "--agent",
        "remote-ceo",
        "--bridge-id",
        "bridge-one",
        agent="remote-ceo",
    )
    assert registered.returncode == 0, registered.stderr + registered.stdout
    lease = json.loads(registered.stdout)

    transferred = run(
        tool,
        repo,
        board,
        home,
        "join",
        "remote-ceo",
        "--harness",
        "codex",
        "--transfer",
    )
    assert transferred.returncode == 0, transferred.stderr + transferred.stdout
    status = run(
        root,
        repo,
        board,
        home,
        "remote",
        "status",
        "--agent",
        "remote-ceo",
        agent="remote-ceo",
    )
    assert status.returncode == 0, status.stderr + status.stdout
    assert json.loads(status.stdout)["online"] is False

    stale = run(
        root,
        repo,
        board,
        home,
        "remote",
        "heartbeat",
        "--agent",
        "remote-ceo",
        "--lease-id",
        lease["lease_id"],
        "--fence",
        str(lease["fence"]),
        agent="remote-ceo",
    )
    assert stale.returncode != 0


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_concurrent_same_provider_rejoin_is_lossless(tool, tmp_path):
    repo, board, home = make_board(tmp_path)
    initial = run(
        tool,
        repo,
        board,
        home,
        "join",
        "stable-seat",
        "--roles",
        "backend",
        "--harness",
        "cursor",
        "--alias",
        "cos",
    )
    assert initial.returncode == 0, initial.stderr + initial.stdout

    def rejoin(_):
        return run(
            tool,
            repo,
            board,
            home,
            "join",
            "stable-seat",
            "--roles",
            "backend",
            "--harness",
            "cursor",
            "--alias",
            "cos",
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        attempts = list(pool.map(rejoin, range(48)))
    failures = [a.stderr + a.stdout for a in attempts if a.returncode]
    assert not failures, failures
    assert json.loads((board / "aliases.json").read_text()) == {"cos": "stable-seat"}
    workforce = json.loads((board / "workforce.json").read_text())
    assert workforce["stable-seat"]["harness"] == "cursor"
    assert json.loads((board / "roles.json").read_text())["stable-seat"] == ["backend"]
