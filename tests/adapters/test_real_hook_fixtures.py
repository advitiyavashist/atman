"""Parse the real, captured Claude Code hook payloads in fixtures/.

Unlike test_claude_adapter.py (which asserts behavior against hand-written
payloads), this module only proves the adapter can digest what Claude Code
actually sends on the wire. See fixtures/README.md for provenance and the
Notification gap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ticket_board.adapters.claude import AdapterConfig, Enrollment, parse_claude_hook_event
from ticket_board.adapters.claude.adapter import ALLOWED_CLAUDE_EVENTS, CONTRACT_KIND_BY_CLAUDE_EVENT

FIXTURES = Path(__file__).resolve().parent / "fixtures"

FIXTURE_FILES = sorted(p for p in FIXTURES.glob("*.json"))


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
    return AdapterConfig(project_id="prj_demo0001", server_url="http://127.0.0.1:4319")


def test_fixture_directory_is_not_empty():
    assert FIXTURE_FILES, "expected captured fixtures in tests/adapters/fixtures/"


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_fixture_has_fields_the_adapter_reads(path: Path):
    payload = json.loads(path.read_text())

    assert isinstance(payload.get("hook_event_name"), str), path.name
    assert payload["hook_event_name"] in ALLOWED_CLAUDE_EVENTS, path.name

    assert isinstance(payload.get("session_id"), str) and payload["session_id"], path.name

    cwd = payload.get("cwd")
    assert cwd is None or isinstance(cwd, str), path.name

    # Ground truth from a real Claude Code session: no schema/occurred_at field
    # is on the wire at all. Document any future drift loudly rather than
    # silently accepting a new shape.
    assert "hook_schema_version" not in payload, (
        path.name,
        "real payload now carries hook_schema_version -- update fixtures/README.md",
    )
    assert "occurred_at" not in payload and "timestamp" not in payload, path.name

    if payload["hook_event_name"] in ("PreToolUse", "PostToolUse"):
        assert isinstance(payload.get("tool_name"), str) and payload["tool_name"], path.name


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_adapter_parses_every_real_fixture(path: Path, enrollment: Enrollment, config: AdapterConfig):
    payload = json.loads(path.read_text())
    event_name = payload["hook_event_name"]

    event = parse_claude_hook_event(payload, enrollment, config)

    assert event["kind"] == CONTRACT_KIND_BY_CLAUDE_EVENT[event_name]
    assert event["agent_id"] == enrollment.agent_id
    assert event["session_id"] == payload["session_id"]
    # occurred_at is missing on every real payload; the adapter must still
    # produce a usable timestamp rather than raising or forwarding None.
    assert isinstance(event["occurred_at"], str) and event["occurred_at"]


def test_edit_and_bash_tool_shapes_differ(enrollment: Enrollment, config: AdapterConfig):
    edit_payload = json.loads((FIXTURES / "post_tool_use_edit.json").read_text())
    bash_payload = json.loads((FIXTURES / "post_tool_use_bash.json").read_text())

    assert "structuredPatch" in edit_payload["tool_response"]
    assert "stdout" in bash_payload["tool_response"]
    assert set(edit_payload["tool_response"]) != set(bash_payload["tool_response"])

    edit_event = parse_claude_hook_event(edit_payload, enrollment, config)
    bash_event = parse_claude_hook_event(bash_payload, enrollment, config)
    assert edit_event["note"] == "PostToolUse: Edit"
    assert bash_event["note"] == "PostToolUse: Bash"


def test_subagent_stop_note_and_no_notification_fixture_exists(enrollment: Enrollment, config: AdapterConfig):
    payload = json.loads((FIXTURES / "subagent_stop.json").read_text())
    event = parse_claude_hook_event(payload, enrollment, config)
    assert event["note"] == "Subagent turn finished"

    # Documented gap (fixtures/README.md): Notification could not be triggered
    # from a scripted, non-interactive capture session.
    assert not (FIXTURES / "notification.json").exists()


def test_no_operator_paths_or_real_identifiers_leaked():
    banned = ("/Users/", "/private/tmp")
    for path in FIXTURE_FILES:
        text = path.read_text()
        for needle in banned:
            assert needle not in text, (path.name, needle)
