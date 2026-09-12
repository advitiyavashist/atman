"""T-828: transfer must work in a real wheel, without checkout imports."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def checked(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    directory = tmp_path_factory.mktemp("t828-wheel")
    wheels = directory / "wheels"
    wheels.mkdir()
    checked([sys.executable, "-m", "pip", "wheel", "--no-deps",
             "--no-build-isolation", "--wheel-dir", str(wheels), str(ROOT)])
    wheel, = wheels.glob("ticket_board-*.whl")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    assert "ticket_board/identity_transfer.py" in names
    assert "tickets.py" not in names
    environment = directory / "venv"
    checked([sys.executable, "-m", "venv", str(environment)])
    python = environment / "bin/python"
    checked([str(python), "-m", "pip", "install", "--no-index",
             "--no-deps", str(wheel)])
    return python, environment / "bin/tickets"


def make_case(tmp_path, installed):
    python, command = installed
    repo = tmp_path / "repo"
    repo.mkdir()
    checked(["git", "init", "-q", str(repo)])
    checked(["git", "-C", str(repo), "-c", "user.name=review", "-c",
             "user.email=review@example.test", "commit", "-q", "--allow-empty",
             "-m", "init"])
    board = repo / ".tickets"
    board.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    cache = home / "cache"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": str(home), "TICKETS_DIR": str(board),
           "TICKETS_CACHE_DIR": str(cache), "PYTHONDONTWRITEBYTECODE": "1"}
    result = checked([str(python), "-c",
                      "import ticket_board.cli as c; print(c.__file__); "
                      "assert 'tickets' not in __import__('sys').modules"],
                     cwd=str(repo), env=env)
    assert str(ROOT) not in result.stdout
    checked([str(command), "join", "seat", "--harness", "remote",
             "--roles", "master", "--alias", "ceo"], cwd=str(repo), env=env)
    agent = board / "agents/seat.json"
    record = json.loads(agent.read_text())
    record.update({
        "auth_check": {"state": "ready", "harness": "remote"},
        "runner_context": {"runner_kind": "remote", "hostname": "old-host"},
        "limit": {"at": "2026-01-01T00:00:00Z", "until": "never"},
        "adapter_failure": {"state": "failed", "provider": "remote"},
    })
    agent.write_text(json.dumps(record, separators=(",", ":")))
    remote = board / "adapters/seat.json"
    remote.parent.mkdir()
    remote.write_text(json.dumps({
        "schema": 1, "agent": "seat", "fence": 7,
        "lease": {"id": "old-bearer", "bridge_id": "bridge-one", "fence": 7,
                  "expires_epoch": time.time() + 300},
        "claim": {"id": "old-claim", "fence": 7},
    }, separators=(",", ":")))
    digest = hashlib.sha256(str(board).encode("utf-8")).hexdigest()[:16]
    endpoint = cache / "sessions" / digest / "seat.json"
    endpoint.parent.mkdir(parents=True)
    # Non-canonical formatting catches semantic-JSON restoration losing bytes.
    endpoint.write_bytes(b'{ "token":"old-secret", "provider" : "remote" }\n')
    endpoint.chmod(0o600)
    paths = [agent, remote, endpoint, board / "workforce.json",
             board / "roles.json", board / "aliases.json", board / "messages.jsonl"]
    return python, command, repo, board, env, paths


def snapshot(paths):
    return {str(path): (path.read_bytes(), path.stat().st_mode & 0o777)
            for path in paths}


def test_installed_invalid_transfer_preserves_identity_bytes(tmp_path, installed):
    _, command, repo, _, env, paths = make_case(tmp_path, installed)
    before = snapshot(paths)
    result = subprocess.run([str(command), "join", "seat", "--harness", "custom",
                             "--transfer"], capture_output=True, text=True,
                            cwd=str(repo), env=env)
    assert result.returncode != 0
    assert "custom needs" in result.stdout + result.stderr
    assert snapshot(paths) == before


def test_installed_committed_transfer_fences_old_transports(tmp_path, installed):
    _, command, repo, board, env, paths = make_case(tmp_path, installed)
    history = (board / "messages.jsonl").read_bytes()
    checked([str(command), "join", "seat", "--harness", "codex", "--transfer"],
            cwd=str(repo), env=env)
    record = json.loads((board / "agents/seat.json").read_text())
    for key in ("auth_check", "runner_context", "limit", "adapter_failure"):
        assert key not in record
    assert record["ticket"] == ""
    assert not paths[2].exists()
    remote = json.loads((board / "adapters/seat.json").read_text())
    assert remote["fence"] == 8
    assert remote["lease"] == {}
    assert "claim" not in remote
    assert remote["revoked_at"]
    assert json.loads((board / "workforce.json").read_text())["seat"]["harness"] == "codex"
    assert history in (board / "messages.jsonl").read_bytes()


def test_installed_late_failure_restores_raw_endpoint_without_success_audit(tmp_path, installed):
    python, _, repo, _, env, paths = make_case(tmp_path, installed)
    before = snapshot(paths)
    # Function definitions need their own statement line in Python 3.9.
    code = "import argparse\nimport ticket_board.cli as c\n" + (
        "def fail(*args):\n    raise RuntimeError('injected late join failure')\n"
        "c._cmd_join_apply_locked = fail\n"
        "a = argparse.Namespace(name='seat', tool='', harness='codex', "
        "alias='', transfer=True)\n"
        "c.cmd_join(a, c.board_dir())\n"
    )
    result = subprocess.run([str(python), "-c", code], capture_output=True,
                            text=True, cwd=str(repo), env=env)
    assert result.returncode != 0
    assert "injected late join failure" in result.stderr
    assert snapshot(paths) == before
