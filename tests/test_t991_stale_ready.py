"""T-991: same-seat unavailable re-probe clears stale Ready when the CLI dies."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from auth_v2_contract import merge_auth_check, seat_fence_matches
from test_t685_auth_v2_contract import runner_ctx, sandbox_ctx, v2_ready

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="operator", env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent,
             HOME=str(board.parent.parent / "home"),
             TICKETS_CACHE_DIR=str(board.parent.parent / "cache"))
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, env=e, cwd=board.parent)


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


def isolated_path(bindir):
    """Keep git, drop any host `agent` so missing-binary is deterministic."""
    return str(bindir) + os.pathsep + "/usr/bin:/bin:/usr/sbin"


def fake_cli(tmp_path, name, status_text, rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text(
        "#!/bin/sh\n"
        "echo %s\n"
        "exit %d\n" % (repr(status_text), rc))
    script.chmod(0o755)
    return dict(PATH=isolated_path(bindir))


def agent_record(board, name):
    return json.loads((board / "agents" / (name + ".json")).read_text())


def unavailable_probe(ctx, at="2026-09-15T00:00:01Z"):
    rec = v2_ready(ctx)
    rec["state"] = "unavailable"
    rec["detail"] = "agent is not installed"
    rec["identity_label"] = ""
    rec["identity"] = ""
    rec["at"] = at
    rec["exit"] = 127
    return rec


def test_seat_fence_ignores_binary_and_runner_id_only():
    host = runner_ctx()
    drifted = runner_ctx(binary="agent", runner_id="rnr_missing_bin")
    assert seat_fence_matches(host, drifted) is True
    assert seat_fence_matches(host, runner_ctx(argv0="atm")) is False
    assert seat_fence_matches(host, runner_ctx(username="other")) is False
    assert seat_fence_matches(
        host, runner_ctx(agent_id="other-seat", ticket_agent="other-seat")) is False
    assert seat_fence_matches(host, sandbox_ctx()) is False


def test_ready_to_missing_binary_replaces_stale_ready():
    host = runner_ctx()
    stored = merge_auth_check({}, v2_ready(host), host)
    assert stored["state"] == "ready" and stored["authoritative"] is True
    missing = runner_ctx(binary="agent", runner_id="rnr_missing_bin")
    incoming = unavailable_probe(missing, at="2026-09-15T00:53:00Z")
    # tickets.py _store_auth_check passes the stored context as runner_ctx.
    merged = merge_auth_check(stored, incoming, host)
    assert merged["state"] == "unavailable"
    assert merged["authoritative"] is True
    assert merged["at"] == "2026-09-15T00:53:00Z"
    assert merged["at"] > stored["at"]
    assert merged["detail"] == "agent is not installed"
    assert merged["pause"]["operator_path"] == "unavailable"
    assert merged["pause"]["paused"] is True
    assert merged["execution_context"]["binary"] == "agent"


def test_first_connect_unavailable_is_fail_closed_not_ready():
    missing = runner_ctx(binary="agent", runner_id="rnr_never_installed")
    incoming = unavailable_probe(missing)
    merged = merge_auth_check({}, incoming, missing)
    assert merged["state"] == "unavailable"
    assert merged["authoritative"] is True
    assert merged.get("identity_label") == ""


def test_unauthenticated_probe_cannot_set_ready_on_empty_blob():
    host = runner_ctx()
    incoming = v2_ready(host)
    incoming["state"] = "login_required"
    incoming["detail"] = "Not logged in"
    incoming["identity_label"] = ""
    incoming["authoritative"] = False
    merged = merge_auth_check({}, incoming, host)
    assert merged.get("state") != "ready"
    assert merged.get("authoritative") is not True


def test_wrong_seat_unavailable_cannot_overwrite_ready():
    host = runner_ctx()
    stored = merge_auth_check({}, v2_ready(host), host)
    other = runner_ctx(
        binary="agent", runner_id="rnr_other_seat",
        agent_id="other-seat", ticket_agent="other-seat")
    merged = merge_auth_check(stored, unavailable_probe(other), host)
    assert merged["state"] == "ready"
    assert merged["detail"] == "Logged in"
    assert merged["execution_context"]["agent_id"] == "atman-auth-v2"


def test_sandbox_unavailable_cannot_overwrite_host_ready():
    host = runner_ctx()
    stored = merge_auth_check({}, v2_ready(host), host)
    sand = sandbox_ctx()
    sand["binary"] = "agent"
    merged = merge_auth_check(stored, unavailable_probe(sand), host)
    assert merged["state"] == "ready"
    merged_as_sand = merge_auth_check(stored, unavailable_probe(sand), sand)
    assert merged_as_sand["state"] == "ready"


def test_board_move_repo_root_change_does_not_clear_ready():
    host = runner_ctx()
    stored = merge_auth_check({}, v2_ready(host), host)
    moved = runner_ctx(
        binary="agent", runner_id="rnr_moved",
        repo_root="/tmp/moved-worktree")
    merged = merge_auth_check(stored, unavailable_probe(moved), host)
    assert merged["state"] == "ready"
    assert merged["execution_context"]["repo_root"] == host["repo_root"]


def test_packaged_argv0_cannot_overwrite_root_ready():
    root = runner_ctx(argv0="tickets", binary="/repo/tickets.py")
    stored = merge_auth_check({}, v2_ready(root), root)
    packaged = runner_ctx(argv0="atm", binary="atm", runner_id="rnr_packaged")
    merged = merge_auth_check(stored, unavailable_probe(packaged), root)
    assert merged["state"] == "ready"
    assert merged["execution_context"]["argv0"] == "tickets"


def test_same_seat_ready_with_binary_drift_does_not_replace():
    host = runner_ctx()
    stored = merge_auth_check({}, v2_ready(host), host)
    drifted = runner_ctx(binary="/other/opt/agent", runner_id="rnr_other_bin")
    incoming = v2_ready(drifted)
    incoming["detail"] = "Logged in (other binary)"
    incoming["at"] = "2026-09-15T00:59:00Z"
    merged = merge_auth_check(stored, incoming, host)
    assert merged["state"] == "ready"
    assert merged["detail"] == "Logged in"
    assert merged["execution_context"]["binary"] == host["binary"]


def test_harness_ready_then_removed_binary_becomes_unavailable(board, tmp_path):
    env = fake_cli(tmp_path, "agent", "Logged in as dev@example.test")
    assert run(board, "join", "cursor-seat", "--roles", "docs",
               "--harness", "cursor", "--lifecycle", "persistent",
               env=env).returncode == 0
    r = run(board, "harness", "auth", "cursor-seat", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    rec = agent_record(board, "cursor-seat")["auth_check"]
    assert rec["state"] == "ready"
    assert rec.get("authoritative") is True
    ready_at = rec["at"]
    resolved = rec["execution_context"]["binary"]
    assert os.path.isabs(resolved) and os.path.isfile(resolved)
    time.sleep(1.1)
    Path(resolved).unlink()
    r = run(board, "harness", "auth", "cursor-seat", env=env)
    rec = agent_record(board, "cursor-seat")["auth_check"]
    assert rec["state"] == "unavailable", r.stdout + r.stderr
    assert rec["authoritative"] is True
    assert rec["at"] > ready_at
    assert rec["pause"]["operator_path"] == "unavailable"
    assert rec["pause"]["paused"] is True
    assert rec.get("detail")


def test_harness_first_connect_missing_binary_is_not_ready(board, tmp_path):
    bindir = tmp_path / "empty-bin"
    bindir.mkdir()
    env = dict(PATH=isolated_path(bindir))
    assert run(board, "join", "fresh-seat", "--roles", "docs",
               "--harness", "cursor", env=env).returncode == 0
    r = run(board, "harness", "auth", "fresh-seat", env=env)
    rec = agent_record(board, "fresh-seat")["auth_check"]
    assert rec["state"] == "unavailable", r.stdout + r.stderr
    assert rec["state"] != "ready"
    assert rec.get("identity_label") in ("", None)
