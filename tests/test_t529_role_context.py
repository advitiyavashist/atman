"""T-529 / E-013: role markdown injects into the watch/spawn prompt.

PM path lock: only `.tickets/briefs/_shared.md` and
`.tickets/briefs/roles/<role>.md` (same store as T-530). Repo-root `roles/`
is not a source. Local pytest only — no Modal, no GitHub Actions.
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


def _write_briefs(board, shared="T529-SHARED shared-memory", roles=None):
    briefs = board / "briefs"
    (briefs / "roles").mkdir(parents=True, exist_ok=True)
    (briefs / "_shared.md").write_text(shared + "\n")
    for name, text in (roles or {}).items():
        (briefs / "roles" / (name + ".md")).write_text(text + "\n")


def test_prompt_injects_shared_and_backend_role(board):
    """Know role (backend) → inject board briefs/_shared.md + briefs/roles/backend.md."""
    _write_briefs(board, roles={"backend": "Lane: backend"})
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    r = run(board, "prompt", "--agent", "alice")
    assert r.returncode == 0, r.stderr
    assert "Role context" in r.stdout
    assert str(board / "briefs" / "_shared.md") in r.stdout
    assert str(board / "briefs" / "roles" / "backend.md") in r.stdout
    assert "T529-SHARED" in r.stdout
    assert "Lane: backend" in r.stdout
    assert "tickets next" in r.stdout


def test_prompt_missing_role_file_is_not_fatal(board):
    _write_briefs(board)
    assert run(board, "join", "bob", "--roles", "nosuchlane").returncode == 0
    r = run(board, "prompt", "--agent", "bob")
    assert r.returncode == 0, r.stderr
    assert "T529-SHARED" in r.stdout
    assert "Role context" in r.stdout
    assert "nosuchlane" not in r.stdout


def test_board_brief_update_lands_on_next_prompt(board):
    """Update: edit .tickets/briefs/roles/<role>.md; next prompt picks it up."""
    _write_briefs(board, roles={"backend": "Lane: backend"})
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    before = run(board, "prompt", "--agent", "alice").stdout
    assert "Lane: backend" in before
    assert "T529-UPDATED-BACKEND" not in before
    (board / "briefs" / "roles" / "backend.md").write_text("T529-UPDATED-BACKEND\n")
    after = run(board, "prompt", "--agent", "alice").stdout
    assert "T529-UPDATED-BACKEND" in after
    assert "T529-SHARED" in after


def test_repo_root_and_project_roles_are_not_read(board):
    """PM lock: repo-root / project roles/ must not inject."""
    _write_briefs(board, shared="T529-BOARD-SHARED", roles={"backend": "T529-BOARD-BACKEND"})
    decoy = board.parent / "roles"
    decoy.mkdir()
    (decoy / "_shared.md").write_text("T529-PROJECT-LEAK\n")
    (decoy / "backend.md").write_text("T529-PROJECT-LEAK\n")
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    r = run(board, "prompt", "--agent", "alice")
    assert r.returncode == 0, r.stderr
    assert "T529-BOARD-SHARED" in r.stdout
    assert "T529-BOARD-BACKEND" in r.stdout
    assert "T529-PROJECT-LEAK" not in r.stdout


def test_tickets_roles_dir_is_not_a_source(board, tmp_path):
    _write_briefs(board, shared="T529-BOARD-SHARED", roles={"backend": "T529-BOARD-BACKEND"})
    pack = tmp_path / "byo-roles"
    pack.mkdir()
    (pack / "_shared.md").write_text("T529-BYO-LEAK\n")
    (pack / "backend.md").write_text("T529-BYO-LEAK\n")
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    r = run(board, "prompt", "--agent", "alice", env={"TICKETS_ROLES_DIR": str(pack)})
    assert r.returncode == 0, r.stderr
    assert "T529-BOARD-SHARED" in r.stdout
    assert "T529-BYO-LEAK" not in r.stdout


def test_unsafe_role_name_is_not_read(board, tmp_path):
    tk = _tickets()
    assert tk.role_brief_path(str(board), "../secret") is None
    assert tk._read_role_file("../secret", str(board)) == (None, "")
    assert tk._read_role_file("..", str(board)) == (None, "")
    assert tk._read_role_file("backend/../_shared", str(board)) == (None, "")
    assert tk.shared_brief_path(str(board)).endswith("briefs/_shared.md")


def test_watch_prompt_file_includes_role_context(board, tmp_path):
    """watch writes {prompt_file} from prompt_text — the spawn path uses the same."""
    _write_briefs(board, roles={"backend": "Lane: backend"})
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
    assert "T529-SHARED" in prompt
    assert "Lane: backend" in prompt
    assert "briefs/_shared.md" in prompt or "briefs/_shared.md".replace("/", os.sep) in prompt
    assert not os.path.exists(got["argv"][0])


def test_render_prompt_file_is_the_watch_spawn_inject_path(board):
    _write_briefs(board, roles={"backend": "Lane: backend"})
    tk = _tickets()
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    path, cleanup = tk._render_prompt_file(str(board), "alice")
    try:
        text = Path(path).read_text()
    finally:
        cleanup()
    assert "Role context" in text
    assert "Lane: backend" in text
    assert "T529-SHARED" in text
    assert tk.shared_brief_path(str(board)) in text
    assert tk.role_brief_path(str(board), "backend") in text
