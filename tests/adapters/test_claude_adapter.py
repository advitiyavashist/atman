from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import ticket_board.adapters.claude.adapter as adapter_module
from ticket_board.adapters.claude import (
    AdapterConfig,
    BoardClient,
    ClaudeHookError,
    Enrollment,
    SpoolFull,
    build_hook_envelope,
    deliver_hook_event,
    diagnose,
    install_hooks,
    load_enrollment,
    parse_claude_hook_event,
    save_enrollment,
    uninstall_hooks,
)
from ticket_board.adapters.claude.adapter import revoke_session_lease


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "claude_hooks"


@pytest.fixture
def enrollment() -> Enrollment:
    return Enrollment(
        project_id="prj_demo0001",
        server_url="http://127.0.0.1:4319",
        agent_id="agt_backend01",
        agent_name="backend-1",
        session_id="ses_a1b2c3d4",
        token="agent-token-value",
        created_at="2026-09-06T14:30:00Z",
        lease_version=2,
    )


@pytest.fixture
def config() -> AdapterConfig:
    return AdapterConfig(project_id="prj_demo0001", server_url="http://127.0.0.1:4319", retries=0, spool_cap=2)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_session_start_maps_to_contract_envelope_without_actor(enrollment, config):
    event = parse_claude_hook_event(fixture("session_start.json"), enrollment, config)
    envelope = build_hook_envelope(event, request_id="0f1e2d3c-4b5a-4968-8776-655443322110")

    assert envelope == {
        "request_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
        "event": {
            "event_id": "hev_764079c6615f3a4717c41693",
            "agent_id": "agt_backend01",
            "session_id": "ses_a1b2c3d4",
            "kind": "session_start",
            "occurred_at": "2026-09-06T14:32:00Z",
            "cwd": "/tmp/ticket-board-scratch/project-a",
            "note": None,
        },
    }
    assert "actor" not in envelope


def test_raw_prompt_and_tool_inputs_are_not_forwarded(enrollment, config):
    prompt_event = parse_claude_hook_event(fixture("prompt_with_secret.json"), enrollment, config)
    tool_event = parse_claude_hook_event(fixture("pre_tool_with_secret.json"), enrollment, config)

    prompt_envelope = json.dumps(build_hook_envelope(prompt_event), sort_keys=True)
    tool_envelope = json.dumps(build_hook_envelope(tool_event), sort_keys=True)

    assert "sk-live_" not in prompt_envelope
    assert "Please debug this failing request" not in prompt_envelope
    assert "Bearer aaaabbbbccccdddd" not in tool_envelope
    assert "tool_input" not in tool_envelope
    assert tool_event["kind"] == "probe"
    assert tool_event["note"] == "PreToolUse: Bash"


def test_unknown_schema_unknown_event_and_oversized_payload_are_rejected(enrollment, config):
    payload = fixture("session_start.json")

    with pytest.raises(ClaudeHookError, match="schema"):
        parse_claude_hook_event({**payload, "hook_schema_version": "v999"}, enrollment, config)
    with pytest.raises(ClaudeHookError, match="unsupported"):
        parse_claude_hook_event({**payload, "hook_event_name": "UnknownEvent"}, enrollment, config)
    with pytest.raises(ClaudeHookError, match="larger"):
        parse_claude_hook_event({**payload, "padding": "x" * 100}, enrollment, AdapterConfig("prj_demo0001", "http://127.0.0.1:4319", max_event_bytes=10))


def test_enrollment_is_project_scoped_and_refuses_env_only_identity(tmp_path, enrollment, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("TICKET_BOARD_AGENT_ID", enrollment.agent_id)

    with pytest.raises(ClaudeHookError, match="env-only"):
        load_enrollment(project)

    path = save_enrollment(project, enrollment)
    loaded = load_enrollment(project)

    assert loaded == enrollment
    assert path.stat().st_mode & 0o777 == 0o600


def test_install_is_idempotent_and_preserves_foreign_hooks(tmp_path, enrollment, config):
    project = tmp_path / "project"
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    foreign_hook = {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "echo keep-me"}],
    }
    settings.write_text(json.dumps({"permissions": {"allow": ["Read"]}, "hooks": {"PreToolUse": [foreign_hook]}}))

    install_hooks(project, enrollment, config)
    once = json.loads(settings.read_text())
    install_hooks(project, enrollment, config)
    twice = json.loads(settings.read_text())

    assert once == twice
    assert twice["permissions"] == {"allow": ["Read"]}
    assert twice["hooks"]["PreToolUse"][0] == foreign_hook
    assert any("ticket-board-hook:agt_backend01" in hook["command"] for entry in twice["hooks"]["SessionStart"] for hook in entry["hooks"])

    uninstall_hooks(project, enrollment)
    after_uninstall = json.loads(settings.read_text())
    assert after_uninstall["hooks"] == {"PreToolUse": [foreign_hook]}


def test_install_refuses_live_agent_worktree(enrollment, config):
    with pytest.raises(ClaudeHookError, match="live agent worktree"):
        install_hooks(Path("/Users/kavana/Downloads/steer/.worktrees/some-agent"), enrollment, config)


def test_install_refuses_user_level_claude_config(tmp_path, enrollment, config, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    with pytest.raises(ClaudeHookError, match="user-level Claude settings"):
        install_hooks(fake_home, enrollment, config)
    with pytest.raises(ClaudeHookError, match="user-level Claude config"):
        install_hooks(fake_home / ".claude" / "projects" / "scratch", enrollment, config)


def test_install_refuses_symlink_to_user_home(tmp_path, enrollment, config, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    project_link = tmp_path / "project-link"
    project_link.symlink_to(fake_home, target_is_directory=True)
    monkeypatch.setenv("HOME", str(fake_home))

    with pytest.raises(ClaudeHookError, match="user-level Claude settings"):
        install_hooks(project_link, enrollment, config)


class FailingClient(BoardClient):
    def __init__(self):
        pass

    def post_json(self, path, body, *, token, expected_status=(200,)):
        raise RuntimeError("offline")


class RecordingClient(BoardClient):
    def __init__(self):
        self.calls = []
        self.base_url = "http://127.0.0.1:4319"
        self.project_id = "prj_demo0001"

    def post_json(self, path, body, *, token, expected_status=(200,)):
        self.calls.append((path, body, token))
        return {"accepted": True, "deduplicated": False, "context": {"lines": ["ok"]}}

    def delete_json(self, path, body, *, token, expected_status=(200,)):
        self.calls.append((path, body, token))
        return {"agent": {"id": "agt_backend01"}, "session": None}


def test_delivery_posts_hook_events_and_offline_spools_with_cap(tmp_path, enrollment, config):
    event = parse_claude_hook_event(fixture("session_start.json"), enrollment, config)
    envelope = build_hook_envelope(event)

    client = RecordingClient()
    result = deliver_hook_event(client, enrollment, envelope, tmp_path / "spool", config=config)
    assert result.delivered is True
    assert client.calls[0] == ("/hook-events", envelope, "agent-token-value")

    offline = FailingClient()
    spool = tmp_path / "spool"
    first = deliver_hook_event(offline, enrollment, {**envelope, "event": {**event, "event_id": "hev_0000000000000001"}}, spool, config=config)
    second = deliver_hook_event(offline, enrollment, {**envelope, "event": {**event, "event_id": "hev_0000000000000002"}}, spool, config=config)
    assert first.spooled and second.spooled
    assert len(list(spool.glob("*.json"))) == 2

    with pytest.raises(SpoolFull, match="cap"):
        deliver_hook_event(offline, enrollment, {**envelope, "event": {**event, "event_id": "hev_0000000000000003"}}, spool, config=config)


def test_doctor_reports_delivery_and_adoption_separately(tmp_path, enrollment, config):
    project = tmp_path / "project"
    project.mkdir()
    install_hooks(project, enrollment, config)

    probe_only = diagnose(
        project,
        enrollment,
        [fixture("notification_probe.json")],
        [{"accepted": True, "deduplicated": False, "context": {"lines": ["ok"]}}],
    ).as_dict()
    assert probe_only["config"] == {"installed": True}
    assert probe_only["delivery"] == {"server_received": True, "response_delivered": True}
    assert probe_only["adoption"] == {"session_adopted": False}
    assert "synthetic probe" in probe_only["remediation"][0]

    adopted = diagnose(project, enrollment, [fixture("session_start.json")], [])
    assert adopted.session_adopted is True
    assert adopted.server_received is False


def test_two_project_dirs_keep_sessions_distinct(tmp_path, enrollment, config):
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    other = Enrollment(**{**enrollment.__dict__, "agent_id": "agt_backend02", "session_id": "ses_other0001", "token": "other-token"})

    save_enrollment(project_a, enrollment)
    save_enrollment(project_b, other)

    assert load_enrollment(project_a).session_id == "ses_a1b2c3d4"
    assert load_enrollment(project_b).session_id == "ses_other0001"


def test_revoke_requires_note_and_uses_session_lease_route(enrollment):
    client = RecordingClient()
    with pytest.raises(ClaudeHookError, match="explicit note"):
        revoke_session_lease(client, enrollment, note="")

    revoke_session_lease(client, enrollment, note="operator requested recovery", expected_version=7)
    path, body, token = client.calls[-1]
    assert path == "/agents/agt_backend01/session-lease"
    assert body["expected_version"] == 7
    assert body["note"] == "operator requested recovery"
    assert token == "agent-token-value"


def test_board_client_uses_contract_project_header(monkeypatch):
    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"accepted": true}'

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["project"] = req.get_header("X-project-id")
        seen["authorization"] = req.get_header("Authorization")
        return Response()

    monkeypatch.setattr(adapter_module.request, "urlopen", fake_urlopen)
    client = BoardClient("http://127.0.0.1:4319", "prj_demo0001")
    assert client.post_json("/hook-events", {"request_id": "r"}, token="tok") == {"accepted": True}

    assert seen == {
        "url": "http://127.0.0.1:4319/hook-events",
        "project": "prj_demo0001",
        "authorization": "Bearer tok",
    }
