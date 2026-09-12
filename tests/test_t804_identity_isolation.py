"""T-804: reused seat names must not leak provider/session/auth state.

Role aliases (ceo, cos) stay stable. Runnable identities are unique per
provider. Reusing a name across providers is refused unless `--transfer` audits
the handover and strips identity-bound state. Message history remains readable.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]
TOOL = ROOT / "tickets.py"


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "fake-home"),
        "PYTEST_CURRENT_TEST": "tests/test_t804_identity_isolation.py",
    }
    for passthrough in ("TMPDIR", "TMP", "TEMP"):
        if os.environ.get(passthrough):
            e[passthrough] = os.environ[passthrough]
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run(tool, cwd, *args, env=None, tmp_path=None, agent=""):
    env = env or clean_env(tmp_path)
    if agent:
        env = dict(env, TICKET_AGENT=agent)
    return subprocess.run(
        [sys.executable, str(tool), *args],
        capture_output=True, text=True, cwd=str(cwd), env=env)


def make_board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    board = repo / ".tickets"
    board.mkdir()
    return repo, board


def agent_rec(board, name):
    path = board / "agents" / (name + ".json")
    return json.loads(path.read_text()) if path.is_file() else {}


def plant_auth(board, name, provider, state="login_required"):
    rec = agent_rec(board, name)
    rec["auth_check"] = {
        "state": state,
        "harness": provider,
        "login_cmd": "agent login" if provider == "cursor" else "codex login",
        "detail": "%s leftover" % provider,
    }
    rec["limit"] = {"at": "now", "until": "never", "kind": provider}
    rec["runner_context"] = {"agent_id": name, "runner_kind": provider, "hostname": "old-host"}
    path = board / "agents" / (name + ".json")
    path.write_text(json.dumps(rec, indent=2))


def plant_endpoint(board, cache_dir, name, provider):
    sys.path.insert(0, str(ROOT))
    os.environ["TICKETS_CACHE_DIR"] = cache_dir
    import session_adapters as sa
    sa.write_endpoint(str(board), name, {
        "seat": name, "provider": provider, "mode": "native",
        "socket": "/tmp/t804.sock", "token": "secret", "pid": os.getpid(),
        "at": "now", "lease_id": "lease-%s" % provider, "fence": 1,
        "heartbeat_epoch": time.time(),
    })
    return sa


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_atman_ceo_cursor_to_codex_reuse_is_refused(tool, tmp_path):
    repo, board = make_board(tmp_path)
    env = clean_env(tmp_path, TICKETS_DIR=str(board))
    first = run(tool, repo, "join", "atman-ceo", "--roles", "master",
                "--harness", "cursor", "--alias", "ceo", env=env, tmp_path=tmp_path)
    assert first.returncode == 0, first.stderr + first.stdout
    plant_auth(board, "atman-ceo", "cursor")
    reuse = run(tool, repo, "join", "atman-ceo", "--roles", "master",
                "--harness", "codex", env=env, tmp_path=tmp_path)
    assert reuse.returncode != 0, reuse.stdout + reuse.stderr
    out = reuse.stdout + reuse.stderr
    assert "--transfer" in out
    assert "cursor" in out
    rec = agent_rec(board, "atman-ceo")
    assert rec["auth_check"]["harness"] == "cursor"
    assert rec["auth_check"]["login_cmd"] == "agent login"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_unique_codex_and_cursor_seats_keep_distinct_auth(tool, tmp_path):
    repo, board = make_board(tmp_path)
    env = clean_env(tmp_path, TICKETS_DIR=str(board))
    assert run(tool, repo, "join", "atman-ceo", "--roles", "master",
               "--harness", "cursor", "--alias", "ceo",
               env=env, tmp_path=tmp_path).returncode == 0
    plant_auth(board, "atman-ceo", "cursor")
    unique = run(tool, repo, "join", "atman-ceo-codex", "--roles", "master",
                 "--harness", "codex", "--alias", "ceo", "--transfer",
                 env=env, tmp_path=tmp_path)
    assert unique.returncode == 0, unique.stderr + unique.stdout
    plant_auth(board, "atman-ceo-codex", "codex", state="ready")
    cursor = agent_rec(board, "atman-ceo")
    codex = agent_rec(board, "atman-ceo-codex")
    assert cursor["auth_check"]["harness"] == "cursor"
    assert cursor["auth_check"]["login_cmd"] == "agent login"
    assert codex["auth_check"]["harness"] == "codex"
    assert codex["auth_check"]["login_cmd"] == "codex login"
    aliases = json.loads((board / "aliases.json").read_text())
    assert aliases["ceo"] == "atman-ceo-codex"


def test_transfer_strips_identity_state_and_keeps_history(tmp_path):
    repo, board = make_board(tmp_path)
    cache = str(tmp_path / "cache")
    env = clean_env(tmp_path, TICKETS_DIR=str(board), TICKETS_CACHE_DIR=cache)
    r = run(TOOL, repo, "join", "atman-ceo", "--roles", "master",
             "--harness", "cursor", "--alias", "ceo", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    msg = run(TOOL, repo, "msg", "historical CEO note from cursor",
              agent="atman-ceo", env=env, tmp_path=tmp_path)
    assert msg.returncode == 0, msg.stderr
    plant_auth(board, "atman-ceo", "cursor")
    sa = plant_endpoint(board, cache, "atman-ceo", "cursor")
    assert sa.read_endpoint(str(board), "atman-ceo")["provider"] == "cursor"

    xfer = run(TOOL, repo, "join", "atman-ceo", "--harness", "codex", "--transfer",
               env=env, tmp_path=tmp_path)
    assert xfer.returncode == 0, xfer.stderr + xfer.stdout
    assert "transferred" in xfer.stdout
    rec = agent_rec(board, "atman-ceo")
    assert "auth_check" not in rec
    assert "limit" not in rec
    assert rec.get("ticket", "") == ""
    assert sa.read_endpoint(str(board), "atman-ceo") is None
    history = run(TOOL, repo, "inbox", "--all", agent="atman-ceo",
                  env=env, tmp_path=tmp_path)
    assert "historical CEO note from cursor" in history.stdout
    wf = json.loads((board / "workforce.json").read_text())
    assert wf["atman-ceo"]["harness"] == "codex"
    assert wf["atman-ceo"]["agent_id"] == "atman-ceo"


def test_retire_historical_alias_keeps_message_history(tmp_path):
    repo, board = make_board(tmp_path)
    env = clean_env(tmp_path, TICKETS_DIR=str(board))
    assert run(TOOL, repo, "join", "atman-ceo", "--roles", "master",
               "--harness", "cursor", "--alias", "ceo",
               env=env, tmp_path=tmp_path).returncode == 0
    assert run(TOOL, repo, "msg", "cursor-era CEO mail", agent="atman-ceo",
               env=env, tmp_path=tmp_path).returncode == 0
    assert run(TOOL, repo, "join", "atman-ceo-codex", "--roles", "master",
               "--harness", "codex", "--alias", "ceo", "--transfer",
               env=env, tmp_path=tmp_path).returncode == 0
    retired = run(TOOL, repo, "retire", "atman-ceo", env=env, tmp_path=tmp_path,
                  agent="atman-ceo-codex")
    assert retired.returncode == 0, retired.stderr
    assert "atman-ceo" not in json.loads((board / "workforce.json").read_text())
    aliases = json.loads((board / "aliases.json").read_text())
    assert aliases.get("ceo") == "atman-ceo-codex"
    history = run(TOOL, repo, "inbox", "--all", agent="atman-ceo-codex",
                  env=env, tmp_path=tmp_path)
    assert "cursor-era CEO mail" in history.stdout


def test_spawn_does_not_probe_auth_on_refused_reuse(tmp_path):
    repo, board = make_board(tmp_path)
    env = clean_env(tmp_path, TICKETS_DIR=str(board))
    assert run(TOOL, repo, "join", "atman-ceo", "--roles", "master",
               "--harness", "cursor", env=env, tmp_path=tmp_path).returncode == 0
    plant_auth(board, "atman-ceo", "cursor")
    spawned = run(TOOL, repo, "spawn", "atman-ceo", "--harness", "codex",
                  env=env, tmp_path=tmp_path)
    assert spawned.returncode != 0
    out = spawned.stdout + spawned.stderr
    assert "--transfer" in out
    assert "agent login" not in out
    rec = agent_rec(board, "atman-ceo")
    assert rec["auth_check"]["harness"] == "cursor"
    assert rec["auth_check"]["login_cmd"] == "agent login"


def test_join_help_documents_transfer(tmp_path):
    repo, board = make_board(tmp_path)
    env = clean_env(tmp_path, TICKETS_DIR=str(board))
    help_out = run(TOOL, repo, "join", "--help", env=env, tmp_path=tmp_path)
    assert help_out.returncode == 0
    text = help_out.stdout + help_out.stderr
    assert "--transfer" in text
    assert "--alias" in text
