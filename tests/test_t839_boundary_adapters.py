"""T-844 regressions: supported wrapper and Claude settings boundaries."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ticket_board.session_boundary import is_board_hook

ROOT = Path(__file__).resolve().parents[1]
SOURCE = [sys.executable, str(ROOT / "tickets.py")]
SESSION_VARS = ("TICKET_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
                "CURSOR_SESSION_ID", "TERM_SESSION_ID", "CURSOR_CONVERSATION_ID")


def checked(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    directory = tmp_path_factory.mktemp("t839-wheel-boundary")
    dist = directory / "dist"
    dist.mkdir()
    checked([sys.executable, "-m", "pip", "wheel", "--no-build-isolation",
             "--no-deps", "--wheel-dir", str(dist), str(ROOT)])
    artifact, = dist.glob("ticket_board-*.whl")
    venv = directory / "venv"
    checked([sys.executable, "-m", "venv", str(venv)])
    python = venv / "bin/python"
    checked([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(artifact)])
    return python, [str(venv / "bin/tickets")]


def case(tmp_path, command):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    board = repo / ".tickets"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
           "TICKETS_DIR": str(board), "TICKETS_CACHE_DIR": str(home / "cache"),
           "PYTHONDONTWRITEBYTECODE": "1"}

    def run(args, sid="setup", agent="setup", seat=None):
        current = dict(env, TICKET_SESSION_ID=sid, TICKET_AGENT=agent)
        if seat:
            current["TICKET_SEAT"] = seat
        return checked(command + args, cwd=str(repo), env=current)

    run(["init"])
    run(["join", "parent-seat", "--roles", "verification", "--alias", "cos"],
        "parent-sid", "parent-seat")
    run(["join", "worker-seat", "--roles", "verification"], "worker-sid", "worker-seat")
    run(["msg", "SECRET_FOR_PARENT", "--to", "parent-seat"], "sender-sid", "sender")
    run(["msg", "ONLY_FOR_WORKER", "--to", "worker-seat"], "sender-sid", "sender")
    return repo, board, env, run


def parent_state(board):
    key = hashlib.sha256(b"parent-sid").hexdigest()[:16]
    return ((board / "agents/parent-seat.json").read_bytes(),
            (board / ".identities" / key).read_bytes(),
            json.loads((board / "workforce.json").read_text())["parent-seat"],
            json.loads((board / "roles.json").read_text())["parent-seat"],
            json.loads((board / "aliases.json").read_text()))


@pytest.mark.parametrize("entry", ["root", "wheel"])
def test_supported_remote_wrapper_replaces_inherited_mailbox_sender_and_session(tmp_path, wheel, entry):
    python, installed_command = wheel
    command = SOURCE if entry == "root" else installed_command
    repo, board, env, run = case(tmp_path, command)
    wrapper = tmp_path / "remote-grok-worker"
    if entry == "root":
        run(["hooks", "remote", "--agent", "worker-seat", "--wrapper", str(wrapper)],
            "parent-sid", "parent-seat", "parent-seat")
    else:
        # Installed CLI's normal-command boundary uses the same shipped renderer
        # as root hooks remote. This does not claim wheel hook-run support.
        code = ("import sys; from ticket_board.session_boundary import remote_wrapper; "
                "print(remote_wrapper(sys.argv[1],sys.argv[2],sys.argv[3]),end='')")
        body = checked([str(python), "-c", code, installed_command[0], str(board), "worker-seat"],
                       cwd=str(repo), env=env).stdout
        wrapper.write_text(body)
        wrapper.chmod(0o700)
    before = parent_state(board)
    hostile = dict(env, TICKET_AGENT="parent-seat", TICKET_SEAT="parent-seat")
    hostile.update({var: "parent-sid" for var in SESSION_VARS})
    inbox = checked([str(wrapper), "inbox", "--keep"], cwd=str(repo), env=hostile)
    assert "ONLY_FOR_WORKER" in inbox.stdout
    assert "SECRET_FOR_PARENT" not in inbox.stdout
    checked([str(wrapper), "msg", "WRAPPER_ATTRIBUTION"], cwd=str(repo), env=hostile)
    records = [json.loads(line) for line in (board / "messages.jsonl").read_text().splitlines()]
    assert next(row for row in reversed(records) if row["text"] == "WRAPPER_ATTRIBUTION")["from"] == "worker-seat"
    checked([str(wrapper), "join", "worker-seat", "--roles", "verification"],
            cwd=str(repo), env=hostile)
    assert parent_state(board) == before
    # A worker join must persist a new owner-bound key, not rewrite parent-sid.
    wrapper_sid = next(line.split("=", 1)[1] for line in wrapper.read_text().splitlines()
                       if line.startswith("export TICKET_SESSION_ID="))
    assert wrapper_sid.startswith("wrapper:worker-seat:")
    key = hashlib.sha256(wrapper_sid.encode()).hexdigest()[:16]
    assert (board / ".identities" / key).read_text().strip() == "worker-seat"


def load_source():
    spec = importlib.util.spec_from_file_location("t839_boundary_source", ROOT / "tickets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def commands(settings, event):
    return [hook["command"] for entry in settings.get("hooks", {}).get(event, [])
            for hook in entry.get("hooks", []) if "command" in hook]


@pytest.mark.parametrize("preexisting", [False, True], ids=["inherit", "existing-worker-local"])
def test_claude_local_role_hooks_are_removed_without_losing_custom_hooks(tmp_path, preexisting):
    repo, board, env, run = case(tmp_path, SOURCE)
    parent = tmp_path / "claude-parent"
    worker = tmp_path / "claude-worker"
    parent.mkdir()
    worker.mkdir()
    local = parent / ".claude/settings.local.json"
    run(["hooks", "claude", "--agent", "parent-seat", "--settings", str(local)],
        "parent-sid", "parent-seat")
    settings = json.loads(local.read_text())
    settings["permissions"]["allow"].append("Bash(my-custom-check:*)")
    # A custom command in the same entry must survive individual filtering.
    settings["hooks"]["UserPromptSubmit"][0]["hooks"].append(
        {"type": "command", "command": "printf custom-hook"})
    settings["hooks"]["PreToolUse"] = [{"hooks": [{"type": "command", "command": "printf other-hook"}]}]
    local.write_text(json.dumps(settings))
    original = local.read_bytes()
    worker_local = worker / ".claude/settings.local.json"
    if preexisting:
        worker_local.parent.mkdir()
        shutil.copy2(local, worker_local)
    module = load_source()
    module._inherit_settings(str(parent), str(worker))
    before = parent_state(board)
    assert module._pin_spawned_worker_hooks(str(board), "worker-seat", str(worker), "claude")
    assert local.read_bytes() == original
    clean = json.loads(worker_local.read_text())
    assert "printf custom-hook" in commands(clean, "UserPromptSubmit")
    assert commands(clean, "PreToolUse") == ["printf other-hook"]
    assert clean["permissions"] == settings["permissions"]
    assert not any(is_board_hook(command) for event in clean.get("hooks", {}) for command in commands(clean, event))
    pinned = json.loads((worker / ".claude/settings.json").read_text())
    hostile = dict(env, TICKET_AGENT="parent-seat", TICKET_SEAT="parent-seat")
    hostile.update({var: "parent-sid" for var in SESSION_VARS})
    for command in commands(pinned, "UserPromptSubmit"):
        result = checked(command, shell=True, cwd=str(worker), env=hostile,
                         input=json.dumps({"session_id": "parent-sid"}))
        assert "SECRET_FOR_PARENT" not in result.stdout
        assert "ONLY_FOR_WORKER" in result.stdout
    assert parent_state(board) == before


def test_installed_sanitizer_preserves_nonboard_commands_in_mixed_entries(tmp_path, wheel):
    python, _ = wheel
    original = {"permissions": {"allow": ["Bash(custom:*)"]}, "hooks": {
        "UserPromptSubmit": [{"matcher": "*", "hooks": [
            {"type": "command", "command": "env TICKET_AGENT=parent /release/tickets.py hook-run --agent parent --event inbox"},
            {"type": "command", "command": "env TICKET_AGENT=parent /release/tickets.py inbox"},
            {"type": "command", "command": "/release/tickets.py custom-check"}]}]}}
    code = ("import sys,json; from ticket_board.session_boundary import without_board_hooks; "
            "print(json.dumps(without_board_hooks(json.load(sys.stdin))[0]))")
    result = checked([str(python), "-c", code], cwd=str(tmp_path), input=json.dumps(original),
                     env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    clean = json.loads(result.stdout)
    assert clean["permissions"] == original["permissions"]
    assert commands(clean, "UserPromptSubmit") == ["/release/tickets.py custom-check"]
