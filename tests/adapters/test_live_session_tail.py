"""T-181: the live-session tail of the Claude hook adapter.

Every test here exists because of something a REAL Claude Code session did that
the fixture-driven half (T-201) could not have seen.  Where a test encodes a
defect that was actually observed, the docstring says what was observed, so a
later reader can tell a regression guard from a preference.

Provenance of the payloads: `tests/adapters/fixtures/` are the real, captured
Claude Code 2.1.263 payloads from T-214 -- not hand-written.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from ticket_board.adapters.claude import (
    AdapterConfig,
    ClaudeHookError,
    Enrollment,
    OperatorCredentials,
    append_receipt,
    diagnose_live,
    disconnect_project,
    hook_runtime,
    install_hooks,
    load_receipts,
    parse_claude_hook_event,
    preflight_hook_command,
    receipts_path,
    revoke_session_lease,
    save_enrollment,
    uninstall_hooks,
)
from ticket_board.adapters.claude.adapter import RECEIPT_CAP, current_agent_version

REAL = Path(__file__).resolve().parent / "fixtures"


def real(name: str) -> dict:
    return json.loads((REAL / name).read_text())


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
    return AdapterConfig(project_id="prj_demo0001", server_url="http://127.0.0.1:4319", retries=0)


@pytest.fixture
def project(tmp_path: Path, enrollment: Enrollment) -> Path:
    project_dir = tmp_path / "scratch-project"
    project_dir.mkdir()
    save_enrollment(project_dir, enrollment)
    return project_dir


# ---------------------------------------------------------------------------
# D2: event identity.  Five REAL tool calls collapsed into one event_id.
# ---------------------------------------------------------------------------

PRE_TOOL_FIXTURES = [
    "pre_tool_use_write.json",
    "pre_tool_use_read.json",
    "pre_tool_use_edit.json",
    "pre_tool_use_bash.json",
    "pre_tool_use_task.json",
]


def test_real_tool_calls_in_the_same_second_get_distinct_event_ids(enrollment, config):
    """Observed: all five real PreToolUse payloads hashed to ONE event_id.

    Real payloads carry no timestamp, so `occurred_at` falls back to `utc_now()`
    at one-second resolution.  The server dedupes on `event_id` -- correctly --
    so four of these five real tool calls were silently discarded as retries.
    """
    ids = {
        parse_claude_hook_event(real(name), enrollment, config)["event_id"]
        for name in PRE_TOOL_FIXTURES
    }
    assert len(ids) == len(PRE_TOOL_FIXTURES)


def test_every_real_fixture_gets_a_distinct_event_id(enrollment, config):
    payloads = sorted(REAL.glob("*.json"))
    ids = {parse_claude_hook_event(json.loads(p.read_text()), enrollment, config)["event_id"]
           for p in payloads}
    assert len(ids) == len(payloads)


def test_pre_and_post_of_one_tool_call_are_different_events(enrollment, config):
    """`tool_use_id` is shared across Pre/Post, so the kind must still separate them."""
    pre = parse_claude_hook_event(real("pre_tool_use_bash.json"), enrollment, config)
    post = parse_claude_hook_event(real("post_tool_use_bash.json"), enrollment, config)
    assert real("pre_tool_use_bash.json")["tool_use_id"] == real("post_tool_use_bash.json")["tool_use_id"]
    assert pre["event_id"] != post["event_id"]


def test_a_byte_identical_retry_keeps_the_same_event_id(enrollment, config):
    """Dedupe is required behaviour; the fix must not break it.

    The adapter retries a delivery it cannot distinguish from a timeout, and the
    board must record exactly one audit row for that.
    """
    payload = real("pre_tool_use_bash.json")
    first = parse_claude_hook_event(payload, enrollment, config)["event_id"]
    second = parse_claude_hook_event(json.loads(json.dumps(payload)), enrollment, config)["event_id"]
    assert first == second


def test_event_id_never_digests_prompt_or_tool_content(enrollment, config):
    """Identity comes from correlation ids only -- never from what was said or done."""
    payload = real("post_tool_use_bash.json")
    mutated = json.loads(json.dumps(payload))
    mutated["tool_response"] = {"stdout": "completely different output", "stderr": "x"}
    mutated["tool_input"] = {"command": "echo something-else-entirely"}
    assert (
        parse_claude_hook_event(payload, enrollment, config)["event_id"]
        == parse_claude_hook_event(mutated, enrollment, config)["event_id"]
    )


# ---------------------------------------------------------------------------
# D1: the installed hook command has to actually run.
# ---------------------------------------------------------------------------


def test_hook_command_does_not_depend_on_a_python_on_path(project, enrollment, config):
    """Observed: the command was `python -m ...` and this machine has no `python`.

    Every hook died in the shell at 127 before any adapter code ran, while the
    doctor still reported `config_installed: true`.
    """
    settings_path = install_hooks(project, enrollment, config)
    command = json.loads(settings_path.read_text())["hooks"]["SessionStart"][0]["hooks"][0]["command"]

    assert not command.startswith("python "), command
    executable, package_root = hook_runtime()
    assert shlex.quote(executable) in command
    assert os.path.isabs(executable) and os.path.exists(executable)
    # A hook runs in Claude Code's environment, not the installing shell's.
    assert shlex.quote(package_root) in command


def test_installed_hook_command_is_executable(project, enrollment, config):
    install_hooks(project, enrollment, config)
    ok, detail = preflight_hook_command(project, enrollment, config)
    assert ok, detail


def test_preflight_flag_survives_the_trailing_owner_comment(project, enrollment, config):
    """Observed: the owner marker is a trailing `#...` shell comment.

    `command + " --preflight"` put the flag INSIDE that comment, so the probe
    silently ran the delivery path instead and reported a missing
    `hook_event_name` -- a false negative that looked like a broken interpreter.
    """
    install_hooks(project, enrollment, config)
    settings = json.loads((project / ".claude" / "settings.json").read_text())
    command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "#" in command, "this test is meaningless if the marker stops being a comment"

    from ticket_board.adapters.claude.adapter import _installed_hook_command, OWNER_MARKER

    installed = _installed_hook_command(project, enrollment.agent_id)
    head, marker, _ = installed.partition("#" + OWNER_MARKER)
    assert marker, "owner marker not found where preflight expects it"
    assert "--preflight" not in head


def test_preflight_fails_when_the_interpreter_is_missing(project, enrollment, config, monkeypatch):
    """The guard must actually bite, not just pass on a healthy machine."""
    monkeypatch.setattr(
        "ticket_board.adapters.claude.adapter.hook_runtime",
        lambda: ("/nonexistent/bin/python-that-is-not-here", str(Path.cwd())),
    )
    install_hooks(project, enrollment, config)
    ok, detail = preflight_hook_command(project, enrollment, config)
    assert ok is False
    assert "preflight marker" in detail or "could not be executed" in detail


# ---------------------------------------------------------------------------
# D3: a doctor an operator can actually run.
# ---------------------------------------------------------------------------


def test_doctor_separates_installed_from_runnable(project, enrollment, config, monkeypatch):
    """`config_installed` and `hook_executable` are different facts.

    This is the pair that D1 hid: a hook naming a missing interpreter is
    installed and will never run, and no other signal can tell them apart.
    """
    monkeypatch.setattr(
        "ticket_board.adapters.claude.adapter.hook_runtime",
        lambda: ("/nonexistent/bin/python-that-is-not-here", str(Path.cwd())),
    )
    install_hooks(project, enrollment, config)
    report = diagnose_live(project, enrollment, config)

    assert report.config_installed is True
    assert report.hook_executable is False
    assert any("cannot run" in line for line in report.remediation)


def test_doctor_says_the_hook_has_never_been_invoked(project, enrollment, config):
    install_hooks(project, enrollment, config)
    report = diagnose_live(project, enrollment, config)

    assert report.hook_executable is True
    assert report.hook_executed is False
    assert report.session_adopted is False
    assert any("never invoked" in line for line in report.remediation)


def test_doctor_distinguishes_delivery_from_adoption(project, enrollment, config):
    """A probe proves reachability. Only a real session event proves adoption."""
    append_receipt(project, {
        "ts": "2026-09-06T22:00:00Z", "hook_event_name": "PreToolUse", "kind": "probe",
        "event_id": "hev_probe", "session_id": "sess-1", "delivered": True,
        "status_code": 200, "context_lines": 1,
    })
    install_hooks(project, enrollment, config)
    delivered_only = diagnose_live(project, enrollment, config)
    assert delivered_only.hook_executed is True
    assert delivered_only.server_received is True
    assert delivered_only.session_adopted is False

    append_receipt(project, {
        "ts": "2026-09-06T22:00:05Z", "hook_event_name": "Stop", "kind": "stop",
        "event_id": "hev_stop", "session_id": "sess-1", "delivered": True,
        "status_code": 200, "context_lines": 2,
    })
    adopted = diagnose_live(project, enrollment, config)
    assert adopted.session_adopted is True
    assert adopted.remediation == []


def test_doctor_reports_spooled_events_when_the_board_is_unreachable(project, enrollment, config):
    append_receipt(project, {
        "ts": "2026-09-06T22:00:00Z", "hook_event_name": "SessionStart", "kind": "session_start",
        "event_id": "hev_1", "session_id": "sess-1", "delivered": False, "spooled": True,
        "error": "connection refused",
    })
    install_hooks(project, enrollment, config)
    report = diagnose_live(project, enrollment, config)

    assert report.hook_executed is True
    assert report.server_received is False
    assert any("no event reached the board (1 spooled" in line for line in report.remediation)


# ---------------------------------------------------------------------------
# Receipts: bounded, private, and never a channel for content.
# ---------------------------------------------------------------------------


def test_receipts_refuse_sensitive_fields(project):
    with pytest.raises(ClaudeHookError, match="sensitive field"):
        append_receipt(project, {"ts": "2026-09-06T22:00:00Z", "prompt": "my secret plan"})


def test_receipts_drop_unknown_fields_rather_than_storing_them(project):
    """An unrecognised but harmless field is dropped by the whitelist."""
    append_receipt(project, {
        "ts": "2026-09-06T22:00:00Z", "event_id": "hev_1", "session_id": "s",
        "something_new": "should not be stored",
    })
    stored = load_receipts(project)[0]
    assert "something_new" not in stored
    assert stored["event_id"] == "hev_1"


@pytest.mark.parametrize("field", ["prompt", "tool_input", "tool_response", "transcript_path"])
def test_receipts_refuse_a_sensitive_field_even_when_it_would_be_dropped(project, field):
    """Fail closed, do not silently drop.

    The whitelist alone would make this safe, but it would also hide the caller
    bug that put a prompt in a receipt. The adapter's privacy bound refuses
    rather than redacts; receipts hold the same line.
    """
    with pytest.raises(ClaudeHookError, match="sensitive field"):
        append_receipt(project, {"ts": "t", "event_id": "hev_1", field: None})
    assert load_receipts(project) == []


def test_receipts_redact_secrets_in_error_text(project):
    append_receipt(project, {
        "ts": "2026-09-06T22:00:00Z", "event_id": "hev_1", "session_id": "s",
        "error": "board rejected Bearer abcdefgh12345678 from the adapter",
    })
    stored = load_receipts(project)[0]
    assert "abcdefgh12345678" not in stored["error"]
    assert "<redacted>" in stored["error"]


def test_receipt_file_is_bounded_and_private(project):
    for index in range(RECEIPT_CAP + 25):
        append_receipt(project, {
            "ts": "2026-09-06T22:00:00Z", "event_id": "hev_%d" % index, "session_id": "s",
        })
    receipts = load_receipts(project)
    assert len(receipts) == RECEIPT_CAP
    assert receipts[-1]["event_id"] == "hev_%d" % (RECEIPT_CAP + 24)
    assert oct(receipts_path(project).stat().st_mode & 0o777) == "0o600"


def test_a_torn_receipt_line_does_not_break_the_doctor(project, enrollment, config):
    append_receipt(project, {"ts": "t", "event_id": "hev_1", "session_id": "s"})
    with receipts_path(project).open("a") as handle:
        handle.write('{"event_id": "hev_2", "sessio\n')  # truncated write
    assert len(load_receipts(project)) == 1
    diagnose_live(project, enrollment, config, run_preflight=False)  # must not raise


# ---------------------------------------------------------------------------
# Two sessions.
# ---------------------------------------------------------------------------


def test_two_claude_sessions_in_one_project_stay_distinct(enrollment, config):
    """One enrollment, two Claude sessions: identity is durable, events are not merged.

    The agent identity comes from the on-disk enrollment and is the same for
    both; the session id comes from the wire and must not be.
    """
    first = real("session_start.json")
    second = json.loads(json.dumps(first))
    second["session_id"] = "99999999-9999-9999-9999-999999999999"

    a = parse_claude_hook_event(first, enrollment, config)
    b = parse_claude_hook_event(second, enrollment, config)

    assert a["agent_id"] == b["agent_id"] == enrollment.agent_id
    assert a["session_id"] != b["session_id"]
    assert a["event_id"] != b["event_id"]


def test_two_sessions_are_attributed_separately_in_receipts(project, enrollment, config):
    for session in ("session-aaa", "session-bbb"):
        append_receipt(project, {
            "ts": "2026-09-06T22:00:00Z", "hook_event_name": "Stop", "kind": "stop",
            "event_id": "hev_" + session, "session_id": session,
            "delivered": True, "status_code": 200, "context_lines": 1,
        })
    sessions = {r["session_id"] for r in load_receipts(project)}
    assert sessions == {"session-aaa", "session-bbb"}

    install_hooks(project, enrollment, config)
    assert diagnose_live(project, enrollment, config).session_adopted is True


def test_a_second_session_does_not_duplicate_the_installed_hook(project, enrollment, config):
    install_hooks(project, enrollment, config)
    install_hooks(project, enrollment, config)
    settings = json.loads((project / ".claude" / "settings.json").read_text())
    assert len(settings["hooks"]["SessionStart"]) == 1


# ---------------------------------------------------------------------------
# D4: uninstall must leave an operator's file exactly as it found it.
# ---------------------------------------------------------------------------


FOREIGN_SETTINGS = {
    "model": "opus",
    "hooks": {
        "SessionStart": [
            {"matcher": "", "hooks": [{"type": "command", "command": "echo somebody-elses-hook"}]}
        ]
    },
    "unrelated": {"keep": "me"},
}


def test_uninstall_leaves_a_foreign_settings_file_byte_identical(project, enrollment, config):
    """Observed: `sort_keys=True` reordered the operator's whole file.

    The foreign hook survived semantically, but "we touched nothing of yours"
    could not be shown with a diff -- and a diff is this ticket's evidence.
    """
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(FOREIGN_SETTINGS, indent=2) + "\n")
    before = settings_path.read_text()

    install_hooks(project, enrollment, config)
    assert settings_path.read_text() != before, "install should have changed something"

    uninstall_hooks(project, enrollment)
    assert settings_path.read_text() == before


def test_uninstall_does_not_create_a_settings_file_that_was_not_there(project, enrollment):
    settings_path = project / ".claude" / "settings.json"
    assert not settings_path.exists()
    uninstall_hooks(project, enrollment)
    assert not settings_path.exists()


def test_install_then_uninstall_leaves_no_empty_hooks_key(project, enrollment, config):
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({"model": "opus"}, indent=2) + "\n")
    before = settings_path.read_text()

    install_hooks(project, enrollment, config)
    uninstall_hooks(project, enrollment)
    assert settings_path.read_text() == before
    assert "hooks" not in json.loads(settings_path.read_text())


# ---------------------------------------------------------------------------
# D5/D6: revoke is an operator action, against a current version.
# ---------------------------------------------------------------------------


class RecordingClient:
    def __init__(self, agent_version: int = 7):
        self.base_url = "http://127.0.0.1:4319"
        self.project_id = "prj_demo0001"
        self.deletes = []
        self.gets = []
        self.agent_version = agent_version

    def get_json(self, path, *, token=None, operator=None):
        self.gets.append((path, token, operator))
        return {"items": [{"id": "agt_backend01", "version": self.agent_version}],
                "stream": {}}

    def delete_json(self, path, body, *, token=None, operator=None, expected_status=(200,)):
        self.deletes.append((path, body, token, operator))
        return {"id": "agt_backend01", "state": "revoked"}


def test_revoke_uses_operator_credentials_not_the_agent_token(enrollment):
    """Observed: the agent token produced 403 `agent_token_insufficient` every time.

    The frozen contract says revokeSessionLease is "Operator or master only".
    """
    client = RecordingClient()
    operator = OperatorCredentials("sess-token", "csrf-token", "http://127.0.0.1:4319")
    revoke_session_lease(client, enrollment, note="recovery", operator=operator)

    path, body, token, sent_operator = client.deletes[-1]
    assert path == "/agents/agt_backend01/session-lease"
    assert token is None, "the agent token must not be used on an operator-only route"
    assert sent_operator is operator


def test_revoke_has_no_agent_token_fallback(enrollment):
    client = RecordingClient()
    with pytest.raises(TypeError):
        revoke_session_lease(client, enrollment, note="recovery")


def test_revoke_still_requires_an_explicit_note(enrollment):
    client = RecordingClient()
    operator = OperatorCredentials("sess-token", "csrf-token", "http://127.0.0.1:4319")
    with pytest.raises(ClaudeHookError, match="explicit note"):
        revoke_session_lease(client, enrollment, note="   ", operator=operator)
    assert client.deletes == []


def test_revoke_reads_the_agents_current_version_not_the_stale_lease_version(enrollment):
    """Observed: 409 `ticket_version_conflict` expected_version=1 actual_version=7.

    `Enrollment.lease_version` is the SESSION LEASE's version, captured once at
    enrollment; this route compares the AGENT's version, which increments on
    every hook event recorded. It was wrong in both quantity and freshness.
    """
    client = RecordingClient(agent_version=7)
    operator = OperatorCredentials("sess-token", "csrf-token", "http://127.0.0.1:4319")
    revoke_session_lease(client, enrollment, note="recovery", operator=operator)

    _, body, _, _ = client.deletes[-1]
    assert body["expected_version"] == 7
    assert body["expected_version"] != enrollment.lease_version


def test_an_explicit_expected_version_skips_the_extra_read(enrollment):
    client = RecordingClient()
    operator = OperatorCredentials("sess-token", "csrf-token", "http://127.0.0.1:4319")
    revoke_session_lease(client, enrollment, note="recovery", operator=operator, expected_version=3)
    assert client.gets == []
    assert client.deletes[-1][1]["expected_version"] == 3


def test_current_agent_version_reads_the_contract_items_key(enrollment):
    """AgentListResponse names the array `items`. Reading `agents` finds nothing."""
    client = RecordingClient(agent_version=4)
    assert current_agent_version(client, enrollment) == 4


def test_current_agent_version_refuses_to_guess_for_a_missing_agent(enrollment):
    client = RecordingClient()
    client.get_json = lambda path, token=None, operator=None: {"items": [], "stream": {}}
    with pytest.raises(ClaudeHookError, match="not present in this project"):
        current_agent_version(client, enrollment)


# ---------------------------------------------------------------------------
# Agent-side teardown.
# ---------------------------------------------------------------------------


def test_disconnect_destroys_the_local_credential(project, enrollment, config):
    install_hooks(project, enrollment, config)
    append_receipt(project, {"ts": "t", "event_id": "hev_1", "session_id": "s"})

    result = disconnect_project(project, enrollment)

    assert not (project / ".ticket-board" / "enrollment.json").exists()
    assert not receipts_path(project).exists()
    assert len(result["removed"]) == 2
    from ticket_board.adapters.claude.adapter import _project_has_owned_hooks
    assert _project_has_owned_hooks(project, enrollment.agent_id) is False


def test_disconnect_reports_undelivered_spooled_events_instead_of_deleting_them(project, enrollment, config):
    """Spooled events are undelivered work; whether to drop them is not ours to decide."""
    spool = project / ".ticket-board" / "spool"
    spool.mkdir(parents=True, exist_ok=True)
    (spool / "hev_1.json").write_text("{}")

    result = disconnect_project(project, enrollment)

    assert len(result["spooled_events_left"]) == 1
    assert (spool / "hev_1.json").exists()


# ---------------------------------------------------------------------------
# The operator CLI. Exit codes are the contract docs/connect-claude.md publishes.
# ---------------------------------------------------------------------------


def test_cli_doctor_exit_code_distinguishes_healthy_from_needs_attention(project, enrollment, config, capsys):
    from ticket_board.adapters.claude import connect

    install_hooks(project, enrollment, config)
    # Installed and runnable, but Claude Code has never invoked it.
    assert connect.main(["--project-dir", str(project), "doctor"]) == connect.EXIT_UNHEALTHY

    append_receipt(project, {
        "ts": "2026-09-06T22:00:00Z", "hook_event_name": "Stop", "kind": "stop",
        "event_id": "hev_1", "session_id": "sess-1", "delivered": True,
        "status_code": 200, "context_lines": 1,
    })
    assert connect.main(["--project-dir", str(project), "doctor"]) == connect.EXIT_OK


def test_cli_doctor_json_is_machine_readable(project, enrollment, config, capsys):
    from ticket_board.adapters.claude import connect

    install_hooks(project, enrollment, config)
    connect.main(["--project-dir", str(project), "doctor", "--json", "--no-preflight"])
    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"config", "delivery", "adoption", "remediation"}
    assert report["delivery"]["hook_executed"] is False


def test_cli_reports_a_missing_enrollment_instead_of_guessing(tmp_path, capsys):
    from ticket_board.adapters.claude import connect

    empty = tmp_path / "not-enrolled"
    empty.mkdir()
    assert connect.main(["--project-dir", str(empty), "doctor"]) == connect.EXIT_ERROR
    assert "enrollment" in capsys.readouterr().err


def test_cli_refuses_to_enroll_user_level_claude_config(tmp_path, capsys, monkeypatch):
    """The CLI must not become a way around the guard."""
    from ticket_board.adapters.claude import connect

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr("ticket_board.adapters.claude.adapter._real_home_dir", lambda: home)

    code = connect.main([
        "--project-dir", str(home / ".claude"), "enroll",
        "--server-url", "http://127.0.0.1:4319", "--project-id", "prj_demo0001",
        "--code", "enrollment-code",
    ])
    assert code == connect.EXIT_ERROR
    assert "user-level" in capsys.readouterr().err


def test_enrollment_guard_runs_before_the_code_is_spent(tmp_path, monkeypatch):
    """A refused project dir must not cost the operator a single-use code.

    `save_enrollment` guards the same path, but it runs after the exchange, so
    the guard alone was not enough: the code was already burned by then.
    """
    from ticket_board.adapters.claude import exchange_enrollment

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr("ticket_board.adapters.claude.adapter._real_home_dir", lambda: home)

    class ExplodingClient:
        base_url = "http://127.0.0.1:4319"

        def post_json(self, *args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("the board was contacted before the path guard ran")

    with pytest.raises(ClaudeHookError, match="user-level"):
        exchange_enrollment(ExplodingClient(), home / ".claude",
                            code="enrollment-code", session_id="ses_1",
                            runtime_version="2.1.263")
