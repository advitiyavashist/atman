"""T-529 / E-013: role markdown injects into the watch/spawn prompt.

Board-canonical store (pm-atman-role-context-v1 / T-530):
  .tickets/briefs/_shared.md
  .tickets/briefs/roles/<role>.md

Inject does not read repo-root roles/ or $TICKETS_ROLES_DIR. Not a
shared-memory product. Local pytest only — no Modal, no GitHub Actions.
"""

import importlib.util
import json
import os
from pathlib import Path

from test_byoa import board, run, stub_harness  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _tickets():
    spec = importlib.util.spec_from_file_location("tickets_t529", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_board_roles(board, shared="shared-memory baseline\n", **roles):
    """Write the T-529/T-530 canonical store under .tickets/briefs/."""
    briefs = Path(board) / "briefs"
    briefs.mkdir(parents=True, exist_ok=True)
    if shared is not None:
        (briefs / "_shared.md").write_text(shared)
    if roles:
        (briefs / "roles").mkdir(parents=True, exist_ok=True)
        for name, text in roles.items():
            (briefs / "roles" / (name + ".md")).write_text(text)


def test_prompt_injects_shared_and_backend_role(board):
    """Know role (backend) → inject _shared.md + backend.md from the board store."""
    write_board_roles(board, shared="shared-memory baseline\n",
                      backend="# backend\n\nLane: backend\n")
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    r = run(board, "prompt", "--agent", "alice")
    assert r.returncode == 0, r.stderr
    assert "Role context" in r.stdout
    assert "shared-memory" in r.stdout
    assert "Lane: backend" in r.stdout
    assert "tickets next" in r.stdout
    assert str(board / "briefs" / "_shared.md") in r.stdout
    assert str(board / "briefs" / "roles" / "backend.md") in r.stdout


def test_prompt_missing_role_file_is_not_fatal(board):
    write_board_roles(board, shared="shared-memory baseline\n")
    assert run(board, "join", "bob", "--roles", "nosuchlane").returncode == 0
    r = run(board, "prompt", "--agent", "bob")
    assert r.returncode == 0, r.stderr
    assert "shared-memory" in r.stdout
    assert "Role context" in r.stdout
    assert "nosuchlane" not in r.stdout


def test_repo_root_roles_are_not_read(board):
    """PATH LOCK: inject must not use repo-root roles/."""
    decoy = board.parent / "roles"
    decoy.mkdir()
    (decoy / "_shared.md").write_text("T529-ROOT-SHARED\n")
    (decoy / "backend.md").write_text("T529-ROOT-BACKEND\n")
    write_board_roles(board, shared="T529-BOARD-SHARED\n",
                      backend="T529-BOARD-BACKEND\n")
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    r = run(board, "prompt", "--agent", "alice")
    assert r.returncode == 0, r.stderr
    assert "T529-BOARD-SHARED" in r.stdout
    assert "T529-BOARD-BACKEND" in r.stdout
    assert "T529-ROOT-SHARED" not in r.stdout
    assert "T529-ROOT-BACKEND" not in r.stdout


def test_repo_root_roles_alone_are_not_injected(board):
    """An empty board store must not fall back to repo-root roles/."""
    decoy = board.parent / "roles"
    decoy.mkdir()
    (decoy / "_shared.md").write_text("T529-ROOT-SHARED\n")
    (decoy / "backend.md").write_text("Lane: backend\n")
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    r = run(board, "prompt", "--agent", "alice")
    assert r.returncode == 0, r.stderr
    assert "T529-ROOT-SHARED" not in r.stdout
    assert "Lane: backend" not in r.stdout
    assert "Role context" not in r.stdout


def test_tickets_roles_dir_is_not_the_store(board, tmp_path):
    pack = tmp_path / "byo-roles"
    pack.mkdir()
    (pack / "_shared.md").write_text("T529-BYO-SHARED\n")
    (pack / "backend.md").write_text("T529-BYO-BACKEND\n")
    write_board_roles(board, shared="T529-BOARD-SHARED\n",
                      backend="T529-BOARD-BACKEND\n")
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    r = run(board, "prompt", "--agent", "alice", env={"TICKETS_ROLES_DIR": str(pack)})
    assert r.returncode == 0, r.stderr
    assert "T529-BOARD-SHARED" in r.stdout
    assert "T529-BOARD-BACKEND" in r.stdout
    assert "T529-BYO-SHARED" not in r.stdout
    assert "T529-BYO-BACKEND" not in r.stdout


def test_board_brief_files_are_the_same_inject_source(board):
    """T-530 writes .tickets/briefs/; watch/spawn inject must read that store."""
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    write_board_roles(board, shared="T529-BOARD-SHARED\n",
                      backend="T529-BOARD-BACKEND\n")
    r = run(board, "prompt", "--agent", "alice")
    assert r.returncode == 0, r.stderr
    assert "T529-BOARD-SHARED" in r.stdout
    assert "T529-BOARD-BACKEND" in r.stdout


def test_unsafe_role_name_is_not_read(board, tmp_path):
    tk = _tickets()
    secret = tmp_path / "secret.md"
    secret.write_text("T529-LEAK\n")
    assert tk._read_role_file("../secret", str(board)) == (None, "")
    assert tk._read_role_file("..", str(board)) == (None, "")
    assert tk._read_role_file("backend/../_shared", str(board)) == (None, "")
    assert tk.role_brief_path(str(board), "../secret") is None
    assert tk.shared_brief_path(str(board)) == str((board / "briefs" / "_shared.md").resolve())


def test_watch_prompt_file_includes_role_context(board, tmp_path):
    """watch writes {prompt_file} from prompt_text — the spawn path uses the same."""
    write_board_roles(board, shared="shared-memory baseline\n",
                      backend="# backend\n\nLane: backend\n")
    script, record = stub_harness(tmp_path)
    assert run(board, "create", "Wire inject", "--role", "backend").returncode == 0
    assert run(board, "join", "alice", "--roles", "backend", "--harness",
               "custom:%s {prompt_file} {cwd} {agent}" % script).returncode == 0
    r = run(board, "watch", "--agent", "alice", "--exec",
            "%s {prompt_file} {cwd} {agent}" % script,
            "--cwd", str(board.parent), "--every", "5", "--max-runs", "1",
            "--run-timeout", "1")
    assert r.returncode == 0, r.stderr + r.stdout
    got = json.loads(record.read_text())
    prompt = got["prompt"]
    assert "alice" in prompt and "tickets next" in prompt
    assert "Role context" in prompt
    assert "shared-memory" in prompt
    assert "Lane: backend" in prompt
    assert not os.path.exists(got["argv"][0])


def test_render_prompt_file_is_the_watch_spawn_inject_path(board):
    write_board_roles(board, shared="shared-memory baseline\n",
                      backend="# backend\n\nLane: backend\n")
    tk = _tickets()
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    path, cleanup = tk._render_prompt_file(str(board), "alice")
    try:
        text = Path(path).read_text()
    finally:
        cleanup()
    assert "Role context" in text
    assert "Lane: backend" in text
    assert "shared-memory" in text
    assert str(board / "briefs" / "roles" / "backend.md") in text
