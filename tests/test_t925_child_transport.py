"""Fresh workers get their own provider endpoint, even across boards."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from unittest import mock

import pytest

from test_t839_boundary_adapters import wheel  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
# Explicit canaries describe the boundary independently of the sanitizer list.
PARENT = {
    "TICKET_SESSION_ID": "parent-session", "CLAUDE_CODE_SESSION_ID": "parent-session",
    "CODEX_SESSION_ID": "parent-session", "CODEX_THREAD_ID": "parent-thread",
    "CURSOR_SESSION_ID": "parent-session", "CURSOR_CONVERSATION_ID": "parent-session",
    "TERM_SESSION_ID": "parent-terminal", "TICKETS_SESSION_LEASE": "parent-lease",
    "CLAUDE_CODE_MESSAGING_SOCKET": "/synthetic-parent.sock",
    "CLAUDE_CODE_MESSAGING_TOKEN": "synthetic-parent-token",
    "CODEX_APP_SERVER_CONTROL_SOCK": "/synthetic-codex.sock",
    "CODEX_APP_SERVER_SOCKET": "/synthetic-codex-other.sock",
    "CURSOR_ACP_CONTROL_SOCK": "/synthetic-cursor.sock",
    "CURSOR_PERSIST_SESSION": "parent-tmux", "TMUX": "parent-tmux", "TMUX_PANE": "%99",
    "TICKET_SESSION_PID": "999999", "CLAUDE_PID": "999999",
    "CODEX_SESSION_PID": "999999", "CURSOR_SESSION_PID": "999999",
}


def load(name):
    spec = importlib.util.spec_from_file_location("t925_" + name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean_env(tmp_path):
    return {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"),
            "TICKETS_CACHE_DIR": str(tmp_path / "cache"),
            "CODEX_HOME": str(tmp_path / "codex-config"),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
            "PYTEST_CURRENT_TEST": "t925::child (call)"}


@pytest.mark.parametrize("same_board", [True, False])
def test_fresh_child_cannot_adopt_parent_endpoint_but_can_register_its_own(tmp_path, same_board):
    tickets, adapters = load("tickets"), load("session_adapters")
    parent_board = str(tmp_path / "parent-board")
    child_board = parent_board if same_board else str(tmp_path / "child-board")
    # T-1114: ambient socket registration requires announcing this board.
    parent_canaries = dict(PARENT, ATMAN_SESSION_TRANSPORT_BOARD=parent_board)
    env = dict(clean_env(tmp_path), **parent_canaries,
               TICKET_AGENT="parent", TICKET_SEAT="parent")
    with mock.patch.dict(os.environ, env, clear=True):
        # Registration stores a synthetic endpoint; it never connects to a socket.
        parent = adapters.register_persistent(parent_board, "parent", "claude", "now")
        assert parent["ok"], parent
        before = adapters.read_endpoint(parent_board, "parent")
        child = tickets._supervisor_launch_env(child_board, "child")
        assert dict(os.environ) == env
    assert child["TICKET_SEAT"] == child["TICKET_AGENT"] == "child"
    assert child["TICKET_SESSION_ID"].startswith("launch:child:")
    assert not (set(parent_canaries) - {"TICKET_SESSION_ID"}) & child.keys()
    assert child["CODEX_HOME"] == env["CODEX_HOME"]
    assert child["TICKETS_CACHE_DIR"] == env["TICKETS_CACHE_DIR"]
    with mock.patch.dict(os.environ, child, clear=True):
        rejected = adapters.register_persistent(child_board, "child", "claude", "now")
        assert not rejected["ok"]
        assert adapters.read_endpoint(child_board, "child") is None
        assert adapters.read_endpoint(parent_board, "parent") == before
        # A child harness may supply its own endpoint after launch; direct
        # registration/attach must continue to read that explicit environment.
        os.environ.update(CLAUDE_CODE_MESSAGING_SOCKET=str(tmp_path / "child.sock"),
                          CLAUDE_CODE_MESSAGING_TOKEN="synthetic-child-token",
                          TICKET_SESSION_PID=str(os.getpid()),
                          ATMAN_SESSION_TRANSPORT_BOARD=child_board)
        registered = adapters.register_persistent(child_board, "child", "claude", "now")
        assert registered["ok"], registered
        assert registered["record"]["socket"] == str(tmp_path / "child.sock")
        assert registered["record"]["pid"] == os.getpid()
        assert adapters.read_endpoint(parent_board, "parent") == before


@pytest.mark.parametrize("entry", ["source", "wheel"])
def test_generated_wrapper_strips_parent_transport_in_real_child(tmp_path, wheel, entry):
    from ticket_board.session_boundary import remote_wrapper

    python, _ = wheel
    inspector = tmp_path / "inspect-env"
    inspector.write_text("#!" + str(python) + "\nimport json,os\nprint(json.dumps(dict(os.environ)))\n")
    inspector.chmod(0o700)
    parent_canaries = dict(PARENT, ATMAN_SESSION_TRANSPORT_BOARD=str(tmp_path / "board"))
    env = dict(clean_env(tmp_path), **parent_canaries,
               TICKET_AGENT="parent", TICKET_SEAT="parent")
    args = [str(inspector), str(tmp_path / "board"), "child"]
    if entry == "source":
        body = remote_wrapper(*args)
    else:
        code = ("import sys; from ticket_board.session_boundary import remote_wrapper; "
                "print(remote_wrapper(*sys.argv[1:]),end='')")
        result = subprocess.run([str(python), "-c", code, *args], env=env,
                                cwd=tmp_path, capture_output=True, text=True, check=True)
        body = result.stdout
    wrapper = tmp_path / "worker-wrapper"
    wrapper.write_text(body)
    wrapper.chmod(0o700)
    result = subprocess.run([str(wrapper), "inspect"], env=env, cwd=tmp_path,
                            capture_output=True, text=True, check=True)
    child = json.loads(result.stdout)
    assert child["TICKET_AGENT"] == child["TICKET_SEAT"] == "child"
    assert child["TICKET_SESSION_ID"].startswith("wrapper:child:")
    assert not (set(parent_canaries) - {"TICKET_SESSION_ID"}) & child.keys()
    assert child["CODEX_HOME"] == env["CODEX_HOME"]
