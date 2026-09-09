"""Identity-pinned hook install, execution, upgrade and rollback."""

import json
import os
import subprocess
from pathlib import Path

from test_wakeup import board, run  # noqa: F401


def _commands(settings):
    return {
        event: [hook["command"] for entry in entries for hook in entry.get("hooks", [])
                if "hook-run" in hook.get("command", "") or "codex-hook" in hook.get("command", "")]
        for event, entries in settings["hooks"].items()
    }


def _shell(command, cwd, stdin="", **overrides):
    env = dict(os.environ, TICKET_AGENT="wrong-ambient", TICKETS_DIR=str(Path(cwd) / "wrong-board"))
    env.update(overrides)
    return subprocess.run(command, shell=True, cwd=str(cwd), env=env, input=stdin,
                          text=True, capture_output=True, timeout=30)


def test_claude_hooks_pin_identity_and_board_from_arbitrary_cwd(board, tmp_path):
    settings = tmp_path / "claude-settings.json"
    run(board, "join", "claude-a", "--roles", "docs")
    installed = run(board, "hooks", "claude", "--agent", "claude-a", "--settings", str(settings))
    assert installed.returncode == 0, installed.stderr
    commands = _commands(json.loads(settings.read_text()))
    assert set(commands) >= {"SessionStart", "UserPromptSubmit", "Stop"}
    assert all("--agent claude-a" in command and "TICKET_AGENT=claude-a" in command
               and str(board) in command for group in commands.values() for command in group)

    elsewhere = tmp_path / "unrelated" / "deep"
    elsewhere.mkdir(parents=True)
    started = _shell(commands["SessionStart"][0], elsewhere,
                     json.dumps({"hook_event_name": "SessionStart"}))
    assert started.returncode == 0, started.stderr
    assert "Ticket board" in started.stdout and str(board) in started.stdout

    stopped = _shell(commands["Stop"][0], elsewhere,
                     json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}))
    decision = json.loads(stopped.stdout)
    assert decision["decision"] == "block" and "claude-a" in decision["reason"]
    assert json.loads((board / "agents" / "claude-a.json").read_text()).get("stop_blocks")
    assert not (board / "agents" / "wrong-ambient.json").exists()
    identities = json.loads((board / "coordination" / "state.json").read_text())["agents"]
    assert "claude-a" in identities and "wrong-ambient" not in identities


def test_codex_and_cursor_hooks_ignore_wrong_ambient_identity(board, tmp_path):
    run(board, "join", "codex-a", "--roles", "docs")
    codex_file = tmp_path / "codex-hooks.json"
    installed = run(board, "hooks", "codex", "--agent", "codex-a",
                    "--worktree", str(board.parent), "--hooks-file", str(codex_file))
    assert installed.returncode == 0, installed.stderr
    codex_cmd = _commands(json.loads(codex_file.read_text()))["SessionStart"][0]
    nested = board.parent / "nested"
    nested.mkdir()
    result = _shell(codex_cmd, tmp_path,
                    json.dumps({"hook_event_name": "SessionStart", "cwd": str(nested)}))
    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Agent: codex-a" in context and "wrong-ambient" not in context

    run(board, "join", "cursor-a", "--roles", "docs")
    installed = run(board, "hooks", "cursor", "--agent", "cursor-a",
                    "--worktree", str(board.parent))
    assert installed.returncode == 0, installed.stderr
    cursor_cfg = json.loads((board.parent / ".cursor" / "hooks.json").read_text())
    cursor_cmd = cursor_cfg["hooks"]["sessionStart"][0]["command"]
    assert "--agent cursor-a" in cursor_cmd and cursor_cmd.startswith(str(board.parent))
    result = _shell(cursor_cmd, tmp_path,
                    json.dumps({"hook_event_name": "SessionStart", "workspace_roots": [str(board.parent)]}))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "wrong-ambient" not in json.dumps(payload)
    identities = json.loads((board / "coordination" / "state.json").read_text())["agents"]
    assert {"codex-a", "cursor-a"} <= set(identities) and "wrong-ambient" not in identities


def test_cursor_upgrade_replaces_legacy_message_board_hook(board):
    cursor = board.parent / ".cursor"
    cursor.mkdir()
    legacy = {"command": "./hooks/check-message-board.py", "timeout": 15}
    foreign = {"command": "./hooks/keep-foreign.py", "timeout": 9}
    (cursor / "hooks.json").write_text(json.dumps({
        "version": 1,
        "hooks": {event: [legacy, foreign] for event in
                  ("sessionStart", "beforeSubmitPrompt", "stop")},
    }))

    installed = run(board, "hooks", "cursor", "--agent", "cursor-a",
                    "--worktree", str(board.parent))
    assert installed.returncode == 0, installed.stderr
    hooks = json.loads((cursor / "hooks.json").read_text())["hooks"]
    for entries in hooks.values():
        commands = [entry["command"] for entry in entries]
        assert "./hooks/check-message-board.py" not in commands
        assert "./hooks/keep-foreign.py" in commands
        assert sum("tickets-board.py --agent cursor-a" in command
                   for command in commands) == 1


def test_remote_grok_cos_wrapper_pins_normal_commands_and_task_wake(board, tmp_path):
    run(board, "join", "grok-worker", "--roles", "review")
    run(board, "master", "take", agent="boss")
    run(board, "master", "cos", "grok-worker", agent="boss")
    wrapper = tmp_path / "tickets-grok-worker"
    installed = run(board, "hooks", "remote", "--agent", "grok-worker",
                    "--prompt-kind", "cos", "--wrapper", str(wrapper))
    assert installed.returncode == 0, installed.stderr
    manifest = json.loads(Path(str(wrapper) + ".hooks.json").read_text())
    assert manifest["agent"] == "grok-worker"
    assert set(manifest["commands"]) == {"identity", "SessionStart", "inbox", "Stop", "taskWake"}
    assert all("--agent grok-worker" in command for command in manifest["commands"].values())

    elsewhere = tmp_path / "away"
    elsewhere.mkdir()
    env = dict(os.environ, TICKET_AGENT="cto", TICKETS_DIR=str(tmp_path / "wrong"))
    identity = subprocess.run([str(wrapper), "identity"], cwd=elsewhere, env=env,
                              text=True, capture_output=True, timeout=30)
    assert identity.returncode == 0, identity.stderr
    assert json.loads(identity.stdout)["agent_id"] == "grok-worker"
    wake = subprocess.run([str(wrapper)], cwd=elsewhere, env=env,
                          text=True, capture_output=True, timeout=30)
    assert wake.returncode == 0, wake.stderr
    assert "CHIEF OF STAFF" in wake.stdout and "grok-worker" in wake.stdout
    posted = subprocess.run([str(wrapper), "msg", "remote cos online", "--re", "T-001"],
                            cwd=elsewhere, env=env, text=True, capture_output=True, timeout=30)
    assert posted.returncode == 0, posted.stderr
    assert json.loads((board / "messages.jsonl").read_text().splitlines()[-1])["from"] == "grok-worker"


def test_hook_upgrade_and_exact_rollback_preserve_previous_config(board, tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "keep-me"}]}]}}))
    run(board, "hooks", "claude", "--agent", "alpha", "--settings", str(settings))
    alpha = settings.read_bytes()
    run(board, "hooks", "claude", "--agent", "beta", "--settings", str(settings))
    assert b"--agent beta" in settings.read_bytes() and b"keep-me" in settings.read_bytes()
    restored = run(board, "hooks", "claude", "--agent", "beta", "--settings", str(settings), "--rollback")
    assert restored.returncode == 0, restored.stderr
    assert settings.read_bytes() == alpha

    # A post-install edit invalidates the receipt rather than being erased.
    run(board, "hooks", "claude", "--agent", "beta", "--settings", str(settings))
    settings.write_bytes(settings.read_bytes() + b"\n")
    refused = run(board, "hooks", "claude", "--agent", "beta", "--settings", str(settings), "--rollback")
    assert refused.returncode != 0 and "changed after" in refused.stderr


def test_fresh_remote_install_rolls_back_to_missing_and_rejects_unsafe_agent(board, tmp_path):
    wrapper = tmp_path / "remote"
    bad = run(board, "hooks", "remote", "--agent", "bad;touch-pwned", "--wrapper", str(wrapper))
    assert bad.returncode != 0 and not wrapper.exists()
    run(board, "hooks", "remote", "--agent", "safe", "--wrapper", str(wrapper))
    assert wrapper.exists() and Path(str(wrapper) + ".hooks.json").exists()
    rolled = run(board, "hooks", "remote", "--agent", "safe", "--wrapper", str(wrapper), "--rollback")
    assert rolled.returncode == 0, rolled.stderr
    assert not wrapper.exists() and not Path(str(wrapper) + ".hooks.json").exists()
