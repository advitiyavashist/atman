"""T-687: Team/Connect auth readiness. Never fake connected. No secrets in UI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from auth_v2_contract import (
    auth_readiness_surface,
    merge_auth_check,
    on_enrolled_runner_host,
)
from test_t685_auth_v2_contract import runner_ctx, sandbox_ctx, v2_ready
from ui_server_harness import make_ui_server_fixture

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
ui_server = make_ui_server_fixture("t687-probe")


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


def paused_quota(ctx=None):
    rec = v2_ready(ctx)
    rec["state"] = "quota"
    rec["detail"] = "Usage limit reached"
    rec["identity_label"] = "dev@example.test"
    return merge_auth_check({}, rec, ctx or runner_ctx())


def test_ready_requires_authoritative_enrolled_context():
    host = runner_ctx()
    ready = merge_auth_check({}, v2_ready(host), host)
    surface = auth_readiness_surface(ready, enrolled_ctx=host, local_ctx=host,
                                     harness="cursor", agent_id="seat")
    assert surface["ready"] is True
    assert surface["label"] == "Ready"
    assert surface["queue"]["ran"] is False
    foreign = runner_ctx(hostname="other-host", runner_id="rnr_other")
    fake = auth_readiness_surface(merge_auth_check({}, v2_ready(foreign), foreign),
                                  enrolled_ctx=host, local_ctx=host,
                                  harness="cursor", agent_id="seat")
    assert fake["ready"] is False
    assert fake["label"] != "Ready"


def test_paused_auth_is_not_quota_or_offline():
    host = runner_ctx()
    rec = paused_quota(host)
    rec["pause"]["paused"] = True
    surface = auth_readiness_surface(rec, enrolled_ctx=host, local_ctx=host,
                                     harness="cursor", agent_id="seat")
    assert surface["paused"] is True
    assert surface["state"] == "quota"
    assert surface["recovery"]["kind"] == "quota"
    assert "not a login failure" in surface["recovery"]["copy"]
    assert surface["label"] == "Usage quota"


def test_sandbox_never_executes_host_reconnect():
    host = runner_ctx()
    assert on_enrolled_runner_host(host, host) is True
    assert on_enrolled_runner_host(host, sandbox_ctx()) is False
    assert on_enrolled_runner_host(sandbox_ctx(), sandbox_ctx()) is False
    surface = auth_readiness_surface(
        v2_ready(host), enrolled_ctx=host, local_ctx=sandbox_ctx(),
        harness="cursor", agent_id="seat")
    assert surface["recovery"]["execute_here"] is False
    assert surface["recovery"]["on_enrolled_host"] is False


def test_resume_copy_does_not_claim_queued_work_ran():
    host = runner_ctx()
    rec = merge_auth_check({}, v2_ready(host), host)
    rec["pause"] = {"paused": False, "retain_queue": True, "retry_model": False,
                    "operator_path": "ready"}
    surface = auth_readiness_surface(
        rec, enrolled_ctx=host, local_ctx=host, resume_at="2026-09-13T00:00:00Z",
        harness="cursor", agent_id="seat")
    assert surface["queue"]["ran"] is False
    assert "not proof a model turn already ran" in surface["queue"]["copy"]


def test_html_has_recheck_and_no_secret_fields():
    html = TOOL.read_text()
    start = html.index('UI_HTML = r"""')
    ui = html[start:html.index('"""', start + 14)]
    assert "authReadiness" in ui
    assert "/auth-reconnect" in ui
    assert "Recheck auth" in ui
    assert "paused-auth" in ui
    assert 'type="password"' not in ui
    assert "provider secrets" in ui.lower() or "no secret fields" in ui


def test_board_json_auth_surface_never_fake_ready(board, ui_server, tmp_path):
    env = fake_cli(tmp_path, "agent", "Not logged in")
    assert run(board, "join", "cursor-seat", "--roles", "docs",
               "--harness", "cursor", env=env).returncode == 0
    assert run(board, "harness", "auth", "cursor-seat", env=env).returncode == 1
    rec = agent_record(board, "cursor-seat")
    rec["runner_context"] = runner_ctx(hostname="other-host", username="other",
                                       runner_id="rnr_foreign")
    (board / "agents" / "cursor-seat.json").write_text(json.dumps(rec, indent=2))
    d = ui_server.get()
    seat = next(a for a in d["agents"] if a["name"] == "cursor-seat")
    surface = seat["auth_surface"]
    assert surface["ready"] is False
    assert surface["state"] in ("login_required", "") or surface["label"] != "Ready"
    assert surface["recovery"]["execute_here"] is False
    assert "agent login" in (surface["recovery"]["cmd"] or surface["login_cmd"] or "agent login")
    page = ui_server.get("/", raw=True).decode()
    assert 'data-testid="auth-cursor-seat"' in page or "authReadiness" in page
    assert 'type="password"' not in page


def test_reconnect_refuses_secrets_and_off_host_execute(board, ui_server, tmp_path):
    env = fake_cli(tmp_path, "agent", "Logged in as dev@example.test")
    assert run(board, "join", "cursor-seat", "--roles", "docs",
               "--harness", "cursor", env=env).returncode == 0
    rec = agent_record(board, "cursor-seat")
    rec["runner_context"] = runner_ctx(hostname="other-host", username="other",
                                       runner_id="rnr_foreign")
    rec["auth_check"] = paused_quota(runner_ctx(hostname="other-host", username="other",
                                               runner_id="rnr_foreign"))
    (board / "agents" / "cursor-seat.json").write_text(json.dumps(rec, indent=2))
    status, out = ui_server.post("/auth-reconnect", {"agent": "cursor-seat", "password": "nope"})
    assert status == 400
    assert out["ok"] is False
    assert out.get("executed") is False
    status, out = ui_server.post("/auth-reconnect", {"agent": "cursor-seat", "login": True})
    assert status == 400
    assert "login" in out["error"]
    status, out = ui_server.post("/auth-reconnect", {"agent": "cursor-seat"})
    assert status == 200
    assert out["ok"] is False
    assert out["executed"] is False
    assert out["ran"] is False
    assert "enrolled" in (out.get("error") or "").lower() or out.get("instructions")
    after = agent_record(board, "cursor-seat")
    assert after["auth_check"]["state"] == "quota"


def test_reconnect_probe_on_enrolled_host(board, tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("tickets_t687", str(TOOL))
    tk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tk)
    env = fake_cli(tmp_path, "agent", "Logged in as dev@example.test")
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("TICKETS_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("ATMAN_RUNNER_KIND", raising=False)
    monkeypatch.delenv("ATMAN_SANDBOX", raising=False)
    monkeypatch.delenv("CURSOR_SANDBOX", raising=False)
    assert run(board, "join", "cursor-seat", "--roles", "docs",
               "--harness", "cursor", "--lifecycle", "persistent", env=env).returncode == 0
    quota_env = fake_cli(tmp_path, "agent", "Usage limit reached; try again later")
    monkeypatch.setenv("PATH", quota_env["PATH"])
    assert run(board, "harness", "auth", "cursor-seat", env=quota_env).returncode == 1
    assert agent_record(board, "cursor-seat")["auth_check"]["state"] == "quota"
    env = fake_cli(tmp_path, "agent", "Logged in as dev@example.test")
    monkeypatch.setenv("PATH", env["PATH"])
    status, out = tk._ui_auth_reconnect(str(board), {"agent": "cursor-seat"})
    assert status == 200
    assert out["ok"] is True
    assert out["executed"] is True
    assert out["ran"] is False
    assert out["auth_surface"]["ready"] is True
    assert out["auth_surface"]["identity_label"]
    stored = agent_record(board, "cursor-seat")["auth_check"]
    assert stored["state"] == "ready"
    assert stored.get("pause", {}).get("paused") is not True
