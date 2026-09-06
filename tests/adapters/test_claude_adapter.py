from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ticket_board.adapters.claude.adapter as adapter_module
import ticket_board.adapters.claude.hook as hook_module
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


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def init_git_repo(repo: Path) -> Path:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True)
    run_git(repo, "config", "user.email", "test@example.invalid")
    run_git(repo, "config", "user.name", "Ticket Board Test")
    (repo / "README.md").write_text("fixture repo\n")
    run_git(repo, "add", "README.md")
    run_git(repo, "commit", "-m", "initial")
    return repo


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


def test_install_refuses_configured_live_agent_worktree_without_host_paths(tmp_path, enrollment, config, monkeypatch):
    worktree_root = tmp_path / "portable-layout" / ".worktrees"
    agent_tree = worktree_root / "some-agent"
    agent_tree.mkdir(parents=True)
    monkeypatch.setenv("TICKET_BOARD_FORBIDDEN_ROOTS", str(worktree_root))

    with pytest.raises(ClaudeHookError, match="live agent worktree"):
        install_hooks(agent_tree, enrollment, config)


def test_install_refuses_user_level_claude_config(tmp_path, enrollment, config, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "ignored-home"))
    monkeypatch.setattr(adapter_module, "_real_home_dir", lambda: fake_home.resolve(strict=False))

    with pytest.raises(ClaudeHookError, match="user-level Claude settings"):
        install_hooks(fake_home, enrollment, config)
    with pytest.raises(ClaudeHookError, match="user-level Claude config"):
        install_hooks(fake_home / ".claude" / "projects" / "scratch", enrollment, config)


def test_install_refuses_symlink_to_user_home(tmp_path, enrollment, config, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    project_link = tmp_path / "project-link"
    project_link.symlink_to(fake_home, target_is_directory=True)
    monkeypatch.setenv("HOME", str(tmp_path / "ignored-home"))
    monkeypatch.setattr(adapter_module, "_real_home_dir", lambda: fake_home.resolve(strict=False))

    with pytest.raises(ClaudeHookError, match="user-level Claude settings"):
        install_hooks(project_link, enrollment, config)


@pytest.fixture
def board_layout(tmp_path, monkeypatch):
    """A miniature of this host's board layout with no /Users/kavana anywhere in it.

    Two protected checkouts (the equivalents of steer and tickets), an in-tree
    agent worktree, an out-of-tree agent worktree registered against the same
    repository, and an ordinary user project that has nothing to do with either.
    """
    root = tmp_path / "portable-layout"
    steer = init_git_repo(root / "boards" / "steer")
    tickets = init_git_repo(root / "boards" / "tickets")

    in_tree = steer / ".worktrees" / "agent-one"
    run_git(steer, "worktree", "add", "-b", "agent-one", str(in_tree), "HEAD")

    off_tree = root / "somewhere" / "else" / "agent-two"
    off_tree.parent.mkdir(parents=True)
    run_git(steer, "worktree", "add", "-b", "agent-two", str(off_tree), "HEAD")

    user_project = init_git_repo(root / "elsewhere" / "my-app")
    user_worktree = root / "elsewhere" / "my-app-review"
    run_git(user_project, "worktree", "add", "-b", "review", str(user_worktree), "HEAD")

    monkeypatch.setenv(
        adapter_module.FORBIDDEN_ROOTS_ENV, os.pathsep.join([str(steer), str(tickets)])
    )
    return {
        "root": root,
        "steer": steer,
        "tickets": tickets,
        "in_tree": in_tree,
        "off_tree": off_tree,
        "user_project": user_project,
        "user_worktree": user_worktree,
    }


def test_project_dir_guard_refuses_protected_checkouts_on_a_portable_layout(board_layout):
    """Refusals must come from configuration plus git structure, not host paths."""
    refused = [
        ("protected checkout root", board_layout["steer"]),
        ("second protected checkout root", board_layout["tickets"]),
        ("agent worktree inside a protected checkout", board_layout["in_tree"]),
        ("agent worktree registered elsewhere on disk", board_layout["off_tree"]),
        ("subdirectory of a protected checkout", board_layout["steer"] / "docs" / "deep"),
        ("symlink pointing at a protected checkout", board_layout["root"] / "innocent-link"),
    ]
    (board_layout["steer"] / "docs" / "deep").mkdir(parents=True)
    (board_layout["root"] / "innocent-link").symlink_to(board_layout["steer"], target_is_directory=True)

    for label, project_dir in refused:
        with pytest.raises(ClaudeHookError, match="live agent worktree"):
            adapter_module._ensure_safe_project_dir(project_dir)
        assert not (Path(project_dir) / ".claude").exists(), label

    assert "/Users/kavana" not in str(board_layout["root"])


def test_project_dir_guard_survives_case_folding(board_layout):
    """The T-201 bypass class: on a case-insensitive filesystem a differently
    cased spelling of a protected checkout is the same directory.

    Path.resolve() does not case-normalise, so `relative_to` reports a folded
    *subdirectory* as unrelated -- only comparing by filesystem identity catches
    it. The subdirectory and worktree cases are the ones that matter: the folded
    root alone would pass on a direct equality check.
    """
    steer = board_layout["steer"]
    (steer / "docs" / "deep").mkdir(parents=True, exist_ok=True)

    def fold(path):
        return Path(str(path).replace("/boards/", "/BOARDS/"))

    if not fold(steer).exists():
        pytest.skip("filesystem is case-sensitive; a folded spelling is a different directory")

    for project_dir in (steer, steer / "docs" / "deep", board_layout["in_tree"]):
        folded = fold(project_dir)
        assert folded.exists()
        with pytest.raises(ClaudeHookError, match="live agent worktree"):
            adapter_module._ensure_safe_project_dir(folded)


def test_project_dir_guard_allows_ordinary_projects(board_layout, enrollment, config):
    """The guard must not refuse the adapter's own primary use case.

    An ordinary git project root is exactly where Claude Code reads
    `.claude/settings.json` from, so refusing every git root would leave the
    adapter unable to enroll anything real.
    """
    allowed = [
        ("ordinary user project root", board_layout["user_project"]),
        ("subdirectory of an ordinary project", board_layout["user_project"] / "src"),
        ("worktree of an ordinary project", board_layout["user_worktree"]),
        ("plain directory outside any repository", board_layout["root"] / "scratch"),
    ]
    (board_layout["user_project"] / "src").mkdir()
    (board_layout["root"] / "scratch").mkdir()

    for label, project_dir in allowed:
        adapter_module._ensure_safe_project_dir(Path(project_dir))

    install_hooks(board_layout["user_project"], enrollment, config)
    assert (board_layout["user_project"] / ".claude" / "settings.json").exists()


def test_project_dir_guard_still_refuses_when_git_cannot_run(board_layout, monkeypatch):
    """Path containment is the floor: losing git must not open the guard.

    Only the off-tree worktree depends on asking git, so it is the one case that
    legitimately degrades -- and it degrades to a documented gap, not a silent one.
    """
    monkeypatch.setattr(adapter_module, "_git_common_dir", lambda path: None)
    (board_layout["steer"] / "docs" / "deep").mkdir(parents=True, exist_ok=True)

    grounded = [
        board_layout["steer"],
        board_layout["tickets"],
        board_layout["in_tree"],
        board_layout["steer"] / "docs" / "deep",
    ]
    # With git unavailable the containment check is alone, so it must stand up to
    # case folding by itself rather than leaning on repository identity.
    folded = [Path(str(d).replace("/boards/", "/BOARDS/")) for d in grounded]
    if not folded[0].exists():
        folded = []

    for project_dir in grounded + folded:
        with pytest.raises(ClaudeHookError, match="live agent worktree"):
            adapter_module._ensure_safe_project_dir(project_dir)


def test_protected_roots_come_from_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv(adapter_module.FORBIDDEN_ROOTS_ENV, raising=False)
    fake_home = tmp_path / "home"
    monkeypatch.setattr(adapter_module, "_real_home_dir", lambda: fake_home.resolve(strict=False))
    defaults = adapter_module._protected_roots()
    assert defaults == (
        (fake_home / "Downloads" / "steer").resolve(strict=False),
        (fake_home / "Downloads" / "tickets").resolve(strict=False),
    )

    monkeypatch.setenv(adapter_module.FORBIDDEN_ROOTS_ENV, "~/one" + os.pathsep + str(tmp_path / "two"))
    monkeypatch.setenv("HOME", str(fake_home))
    configured = adapter_module._protected_roots()
    assert configured == (
        (fake_home / "one").resolve(strict=False),
        (tmp_path / "two").resolve(strict=False),
    )

    monkeypatch.setenv(adapter_module.FORBIDDEN_ROOTS_ENV, "")
    assert adapter_module._protected_roots() == ()


@pytest.mark.parametrize(
    "checkout", [Path("/Users/kavana/Downloads/steer"), Path("/Users/kavana/Downloads/tickets")]
)
def test_project_dir_guard_refuses_this_hosts_main_checkouts(checkout, monkeypatch):
    """The two checkouts named in the ticket, refused under the shipped defaults.

    Skips only where the checkout is genuinely absent; the portable-layout tests
    above carry the requirement on every other host, so nothing goes untested.
    """
    monkeypatch.delenv(adapter_module.FORBIDDEN_ROOTS_ENV, raising=False)
    if not (checkout / ".git").exists() or adapter_module._real_home_dir() != Path("/Users/kavana"):
        pytest.skip("%s is not this host's board checkout" % checkout)

    with pytest.raises(ClaudeHookError, match="live agent worktree"):
        adapter_module._ensure_safe_project_dir(checkout)


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


def test_hook_command_refuses_server_url_mismatch_before_delivery(tmp_path, enrollment, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    save_enrollment(project, enrollment)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(fixture("session_start.json"))))
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(
        hook_module,
        "deliver_hook_event",
        lambda *args, **kwargs: pytest.fail("server URL mismatch reached delivery"),
    )

    result = hook_module.main(
        [
            "--project-id",
            enrollment.project_id,
            "--server-url",
            "http://attacker.invalid",
            "--agent-id",
            enrollment.agent_id,
            "--project-dir",
            str(project),
        ]
    )

    assert result == 0
    assert "identity does not match" in stderr.getvalue()


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
