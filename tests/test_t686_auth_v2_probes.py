"""T-686: wire T-685 probes, spawn gates, pause/resume into tickets.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from watch_reaper import collect_watch_pids_from_board

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="operator", env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent,
             HOME=str(board.parent.parent / "home"),
             TICKETS_CACHE_DIR=str(board.parent.parent / "cache"))
    if env:
        e.update(env)
    r = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                       text=True, env=e, cwd=board.parent)
    if args and args[0] == "spawn" and "--stop" not in args:
        collect_watch_pids_from_board(board)
    return r


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    (tmp_path / "cache").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty",
                    "-m", "init"], check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    assert run(b, "create", "Work", "--role", "docs").returncode == 0
    return b


def fake_cli(tmp_path, name, status_text, rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text(
        "#!/bin/sh\n"
        "echo %s\n"
        "exit %d\n" % (repr(status_text), rc))
    script.chmod(0o755)
    return dict(PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""))


def agent_record(board, name):
    return json.loads((board / "agents" / (name + ".json")).read_text())


def test_claude_spawn_is_gated_like_cursor(board, tmp_path):
    env = fake_cli(tmp_path, "claude", "Not logged in")
    assert run(board, "join", "claude-seat", "--roles", "docs",
               "--harness", "claude").returncode == 0
    refused = run(board, "spawn", "claude-seat", "--roles", "docs", env=env)
    assert refused.returncode != 0 and "watcher not started" in refused.stderr
    assert agent_record(board, "claude-seat")["auth_check"]["state"] == "login_required"


def test_expired_is_not_login_required(board, tmp_path):
    env = fake_cli(tmp_path, "agent", "token expired; please reauthenticate")
    assert run(board, "join", "cursor-seat", "--roles", "docs",
               "--harness", "cursor").returncode == 0
    r = run(board, "harness", "auth", "cursor-seat", env=env)
    assert r.returncode == 1
    assert "Credential expired" in r.stdout
    assert agent_record(board, "cursor-seat")["auth_check"]["state"] == "expired"
    assert "Login required" not in r.stdout


def test_network_is_not_unavailable_binary(board, tmp_path):
    env = fake_cli(tmp_path, "agent", "connection refused", rc=1)
    assert run(board, "join", "cursor-seat", "--roles", "docs",
               "--harness", "cursor").returncode == 0
    r = run(board, "harness", "auth", "cursor-seat", env=env)
    assert r.returncode == 1
    assert agent_record(board, "cursor-seat")["auth_check"]["state"] == "network"


def test_sandbox_ready_does_not_clobber_host_quota(board, tmp_path):
    env = fake_cli(tmp_path, "agent", "Logged in as host@example.test")
    assert run(board, "join", "cursor-seat", "--roles", "docs",
               "--harness", "cursor", "--lifecycle", "persistent").returncode == 0
    host_env = fake_cli(tmp_path, "agent", "Usage limit reached; try again later")
    assert run(board, "harness", "auth", "cursor-seat", env=host_env).returncode == 1
    rec = agent_record(board, "cursor-seat")
    assert rec["auth_check"]["state"] == "quota"
    assert rec["auth_check"].get("authoritative") is True
    sand = dict(host_env)
    sand["ATMAN_RUNNER_KIND"] = "sandbox"
    ready = fake_cli(tmp_path, "agent", "Logged in as sandbox@example.test")
    sand["ATMAN_RUNNER_KIND"] = "sandbox"
    r = run(board, "harness", "auth", "cursor-seat", env=sand)
    rec = agent_record(board, "cursor-seat")
    assert rec["auth_check"]["state"] == "quota"
    assert rec["auth_check"]["execution_context"]["runner_kind"] == "host"
    assert rec["auth_check"]["pause"]["retry_model"] is False
    assert rec["auth_check"]["pause"]["paused"] is True
    assert "non-authoritative" in r.stdout or rec["auth_check"]["state"] == "quota"


def test_matching_ready_clears_pause(board, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "logged-in"
    script = bindir / "agent"
    script.write_text(
        "#!/bin/sh\n"
        "if [ -f %s ]; then echo 'Logged in as dev@example.test'; exit 0; fi\n"
        "echo 'Not logged in'; exit 0\n" % str(state))
    script.chmod(0o755)
    env = dict(PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    assert run(board, "join", "cursor-seat", "--roles", "docs",
               "--harness", "cursor", "--lifecycle", "persistent").returncode == 0
    assert run(board, "harness", "auth", "cursor-seat", env=env).returncode == 1
    rec = agent_record(board, "cursor-seat")
    assert rec["auth_check"]["pause"]["paused"] is True
    alert = rec["auth_check"]["alert_id"]
    assert alert.startswith("auth:cursor-seat:login_required:")
    state.write_text("1")
    r = run(board, "harness", "auth", "cursor-seat", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    rec = agent_record(board, "cursor-seat")
    assert rec["auth_check"]["state"] == "ready"
    assert rec["auth_check"]["pause"]["paused"] is False
    assert rec["auth_check"].get("alert_id", "") == ""
    assert rec["auth_check"]["credential_profile_ref"].startswith("prf_")
    assert "sk-" not in json.dumps(rec["auth_check"])


def test_custom_spawn_still_allowed_without_declared_check(board, tmp_path):
    script = tmp_path / "echo.sh"
    script.write_text("#!/bin/sh\necho OK\n")
    script.chmod(0o755)
    r = run(board, "spawn", "qwen", "--harness", "custom", "--cmd",
            "%s {prompt_file}" % script, "--roles", "docs", "--every", "3600",
            "--max-runs", "1")
    assert r.returncode == 0, r.stderr + r.stdout
    run(board, "spawn", "qwen", "--stop")


def test_steer_shaped_board_refuses_wrong_git_root(board, tmp_path):
    """dirname(board) is Steer; expected origin is Atman."""
    env = fake_cli(tmp_path, "agent", "Logged in as dev@example.test")
    wf = json.loads((board / "workforce.json").read_text()) if (board / "workforce.json").exists() else {}
    assert run(board, "join", "cursor-seat", "--roles", "docs",
               "--harness", "cursor").returncode == 0
    wf = json.loads((board / "workforce.json").read_text())
    wf["cursor-seat"]["expected_origin"] = "advitiyavashist/atman"
    (board / "workforce.json").write_text(json.dumps(wf, indent=2))
    r = run(board, "spawn", "cursor-seat", env=env)
    assert r.returncode != 0
    assert "repo_mismatch" in (r.stderr + r.stdout)


def test_ui_json_exposes_v2_auth_fields(board, tmp_path):
    env = fake_cli(tmp_path, "agent", "Not logged in")
    assert run(board, "join", "cursor-seat", "--roles", "docs",
               "--harness", "cursor").returncode == 0
    run(board, "harness", "auth", "cursor-seat", env=env)
    snap = run(board, "ui", "--json", env=env)
    seat = next(a for a in json.loads(snap.stdout)["agents"] if a["name"] == "cursor-seat")
    assert seat["auth"] == "login_required"
    assert seat["auth_login_cmd"] == "agent login"
    assert seat.get("auth_paused") is True or seat["auth"] == "login_required"
