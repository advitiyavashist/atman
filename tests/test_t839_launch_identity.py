"""T-839 launch gate: unique workers cannot inherit a role hook or session."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(HERE).parent
TICKETS = str(ROOT / "tickets.py")


def _run(board, args, env=None, session=None, agent=None, seat=None, entry="root", cwd=None):
    e = dict(os.environ)
    for var in (
        "TICKET_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "CODEX_SESSION_ID",
        "CURSOR_SESSION_ID",
        "TERM_SESSION_ID",
        "CURSOR_CONVERSATION_ID",
    ):
        e.pop(var, None)
    e.pop("TICKET_AGENT", None)
    e.pop("TICKET_SEAT", None)
    e.pop("TICKETS_DIR", None)
    if session is not None:
        e["TICKET_SESSION_ID"] = session
    if agent is not None:
        e["TICKET_AGENT"] = agent
    if seat is not None:
        e["TICKET_SEAT"] = seat
    e.update(env or {})
    if "TICKETS_DIR" not in e:
        e["TICKETS_DIR"] = os.path.join(board, ".tickets")
    if entry == "pkg":
        e["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
        cmd = [sys.executable, "-m", "ticket_board", *args]
    else:
        cmd = [sys.executable, TICKETS, *args]
    return subprocess.run(cmd, cwd=cwd or board, env=e, capture_output=True, text=True)


def _load_tickets():
    spec = importlib.util.spec_from_file_location("tickets_t839", TICKETS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def board(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    r = _run(str(d), ["init"], session="setup")
    assert r.returncode == 0, r.stderr
    _run(str(d), ["create", "seat probe", "--role", "lead"], session="setup")
    return str(d)


@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_ticket_seat_outranks_forged_session_id_on_inbox(board, entry):
    _run(board, ["join", "cursor", "--roles", "verification"], session="cos-session",
         agent="cursor", entry=entry)
    _run(board, ["msg", "for cursor only", "--to", "cursor"], session="cos-session",
         agent="cursor", entry=entry)
    _run(board, ["msg", "for worker only", "--to", "unique-worker"], session="cos-session",
         agent="cursor", entry=entry)

    stolen = _run(
        board, ["inbox", "--keep", "--limit", "8"],
        session="cos-session", agent="unique-worker", seat="unique-worker",
        entry=entry,
    )
    assert stolen.returncode == 0, stolen.stderr
    assert "for worker only" in stolen.stdout
    assert "for cursor only" not in stolen.stdout


@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_packaged_and_root_record_session_identity(board, entry):
    _run(board, ["join", "planner", "--roles", "lead"], session="s1", entry=entry)
    out = _run(board, ["board", "-q"], session="s1", agent="stale-name", entry=entry).stdout
    assert "you: planner" in out
    assert "UNCONFIRMED" not in out


def test_supervisor_launch_env_strips_inherited_provider_session(board, monkeypatch):
    mod = _load_tickets()
    monkeypatch.setenv("CURSOR_SESSION_ID", "parent-cos-session")
    monkeypatch.setenv("TICKET_SESSION_ID", "parent-cos-session")
    monkeypatch.setenv("TICKET_AGENT", "cursor")
    env = mod._supervisor_launch_env(os.path.join(board, ".tickets"), "unique-worker")
    assert env["TICKET_SEAT"] == "unique-worker"
    assert env["TICKET_AGENT"] == "unique-worker"
    assert env["TICKET_SESSION_ID"].startswith("launch:unique-worker:")
    assert env.get("CURSOR_SESSION_ID") is None
    assert env["TICKET_SESSION_ID"] != "parent-cos-session"


def test_spawned_cursor_hooks_replace_canonical_role_hook(board, tmp_path):
    mod = _load_tickets()
    tickets_dir = os.path.join(board, ".tickets")
    _run(board, ["join", "cursor", "--roles", "verification"], session="cos")
    repo = tmp_path / "repo"
    repo.mkdir()
    canonical = _run(board, ["hooks", "cursor", "--agent", "cursor", "--worktree", str(repo)])
    assert canonical.returncode == 0, canonical.stderr
    hook_src = (repo / ".cursor" / "hooks" / "tickets-board.py").read_text()
    assert "AGENT = 'cursor'" in hook_src

    worker_a = tmp_path / "worker-a"
    worker_b = tmp_path / "worker-b"
    worker_a.mkdir()
    worker_b.mkdir()
    (worker_a / ".cursor").mkdir(parents=True)
    (worker_b / ".cursor").mkdir(parents=True)
    import shutil
    shutil.copytree(repo / ".cursor", worker_a / ".cursor", dirs_exist_ok=True)
    shutil.copytree(repo / ".cursor", worker_b / ".cursor", dirs_exist_ok=True)
    assert "AGENT = 'cursor'" in (worker_a / ".cursor" / "hooks" / "tickets-board.py").read_text()

    roles_before = json.loads((Path(tickets_dir) / "roles.json").read_text())
    assert roles_before.get("cursor") == ["verification"]

    assert mod._pin_spawned_worker_hooks(tickets_dir, "codex-to-cursor-a", str(worker_a), "cursor")
    assert mod._pin_spawned_worker_hooks(tickets_dir, "codex-to-cursor-b", str(worker_b), "cursor")

    assert "AGENT = 'codex-to-cursor-a'" in (worker_a / ".cursor" / "hooks" / "tickets-board.py").read_text()
    assert "AGENT = 'codex-to-cursor-b'" in (worker_b / ".cursor" / "hooks" / "tickets-board.py").read_text()
    assert "AGENT = 'cursor'" in (repo / ".cursor" / "hooks" / "tickets-board.py").read_text()
    roles_after = json.loads((Path(tickets_dir) / "roles.json").read_text())
    assert roles_after.get("cursor") == ["verification"]
    assert "codex-to-cursor-a" not in json.dumps(roles_after.get("cursor"))


def test_checkin_stamps_explicit_worktree_not_process_cwd(board, tmp_path, monkeypatch):
    mod = _load_tickets()
    launcher = tmp_path / "atman-runtime-current"
    target = tmp_path / "cursor-cos-runtime-0912"
    launcher.mkdir()
    target.mkdir()
    tickets_dir = os.path.join(board, ".tickets")
    monkeypatch.chdir(launcher)
    rec = mod.checkin(tickets_dir, "cursor", None, "spawned", cwd=str(target))
    assert os.path.realpath(rec["cwd"]) == os.path.realpath(target)
    assert os.path.realpath(rec["cwd"]) != os.path.realpath(launcher)
    assert launcher.name not in rec["cwd"]


def test_spawn_checkin_uses_worktree_not_launcher_cwd(board, tmp_path):
    launcher = tmp_path / "atman-runtime-current"
    target = tmp_path / "cursor-cos-runtime-0912"
    launcher.mkdir()
    target.mkdir()
    _run(board, ["join", "cursor", "--roles", "verification"], session="cos",
         agent="cursor", cwd=str(launcher))
    spawned = _run(
        board,
        ["spawn", "cursor", "--exec", "true", "--every", "3600", "--max-runs", "1",
         "--worktree", str(target)],
        session="cos", agent="cursor", cwd=str(launcher),
    )
    try:
        rec_path = Path(board) / ".tickets" / "agents" / "cursor.json"
        rec = json.loads(rec_path.read_text())
        assert spawned.returncode == 0, spawned.stdout + spawned.stderr
        assert os.path.realpath(rec["cwd"]) == os.path.realpath(target), spawned.stdout + spawned.stderr
        assert os.path.realpath(rec.get("worktree") or rec["cwd"]) == os.path.realpath(target)
        assert "atman-runtime-current" not in (rec.get("cwd") or "")
        assert "atman-runtime-current" not in (rec.get("worktree") or "")
    finally:
        _run(board, ["spawn", "cursor", "--stop"], session="cos", agent="cursor",
             cwd=str(launcher))
