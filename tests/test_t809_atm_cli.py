"""T-809: atm is the primary CLI; tickets is the same implementation."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
PKG = ROOT / "src" / "ticket_board" / "cli.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(script, board, *args, argv0=None):
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="t809",
               HOME=str(board.parent / "home"))
    env.pop("TICKETS_STOP_HOOK", None)
    (board.parent / "home").mkdir(exist_ok=True)
    cmd0 = argv0 or sys.executable
    if argv0:
        return subprocess.run([str(argv0), *args], capture_output=True, text=True,
                              env=env, cwd=str(board.parent))
    return subprocess.run([sys.executable, str(script), *args], capture_output=True,
                          text=True, env=env, cwd=str(board.parent))


def test_cli_prog_follows_invocation_name():
    root = _load(TOOL, "t809_root")
    pkg = _load(PKG, "t809_pkg")
    for mod in (root, pkg):
        assert mod.PRIMARY_CLI_NAME == "atm"
        assert mod.COMPAT_CLI_NAME == "tickets"
        assert mod.cli_prog(["/usr/bin/atm"]) == "atm"
        assert mod.cli_prog(["/usr/bin/tickets"]) == "tickets"
        assert mod.cli_prog(["/opt/tickets.py"]) == "tickets"
        assert mod.cli_prog(["/opt/atm.py"]) == "atm"


def test_pyproject_scripts_share_one_entry():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'atm = "ticket_board.cli:main"' in text
    assert 'tickets = "ticket_board.cli:main"' in text


def test_help_prog_matches_invoked_name(tmp_path):
    atm = tmp_path / "atm"
    tickets = tmp_path / "tickets"
    atm.symlink_to(TOOL)
    tickets.symlink_to(TOOL)
    r_atm = subprocess.run([str(atm), "--help"], capture_output=True, text=True)
    r_tic = subprocess.run([str(tickets), "--help"], capture_output=True, text=True)
    assert r_atm.returncode == 0 and r_tic.returncode == 0
    assert "usage: atm" in r_atm.stdout
    assert "usage: tickets" in r_tic.stdout
    for out in (r_atm.stdout, r_tic.stdout):
        assert "join" in out and "connect" in out


def test_atm_and_tickets_share_one_board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    board = repo / ".tickets"
    atm = tmp_path / "bin" / "atm"
    tickets = tmp_path / "bin" / "tickets"
    atm.parent.mkdir()
    atm.symlink_to(TOOL)
    tickets.symlink_to(TOOL)
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="t809",
               HOME=str(tmp_path / "home"))
    env.pop("TICKETS_STOP_HOOK", None)
    (tmp_path / "home").mkdir()
    created = subprocess.run(
        [str(atm), "create", "shared-board-proof", "--role", "backend"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert created.returncode == 0, created.stderr
    shown = subprocess.run(
        [str(tickets), "show", "T-001"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert shown.returncode == 0, shown.stderr
    assert "shared-board-proof" in shown.stdout
    listed = subprocess.run(
        [str(atm), "list"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert "T-001" in listed.stdout


def test_old_hooks_still_install_tickets_commands(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    env = dict(os.environ, TICKET_AGENT="cu", HOME=str(tmp_path / "home"),
               TICKETS_DIR=str(repo / ".tickets"))
    env.pop("TICKETS_STOP_HOOK", None)
    (tmp_path / "home").mkdir()
    created = subprocess.run(
        [sys.executable, str(TOOL), "create", "hook-board"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert created.returncode == 0, created.stderr
    r = subprocess.run(
        [sys.executable, str(TOOL), "hooks", "cursor", "--agent", "cu"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert r.returncode == 0, r.stderr
    hook = repo / ".cursor" / "hooks" / "tickets-board.py"
    assert hook.is_file()
    cfg = (repo / ".cursor" / "hooks.json").read_text(encoding="utf-8")
    assert "tickets-board.py" in cfg
    claude = tmp_path / "settings.json"
    r2 = subprocess.run(
        [sys.executable, str(TOOL), "hooks", "claude", "--agent", "doc",
         "--settings", str(claude)],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert r2.returncode == 0, r2.stderr
    body = claude.read_text(encoding="utf-8")
    assert "hook-run --agent doc" in body
    assert "Bash(tickets:*)" in body


def test_install_sh_links_both_names():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'ln -sf "$HERE/tickets.py" "$BIN/atm"' in text
    assert 'ln -sf "$HERE/tickets.py" "$BIN/tickets"' in text
    assert "atman.sh" not in text
    assert "https://atman.sh" not in text


def test_worker_prompt_names_atm_primary_and_tickets_alias():
    root = _load(TOOL, "t809_prompt")
    assert "`atm ...`" in root.WORKER_PROMPT
    assert "compatibility alias" in root.WORKER_PROMPT
    assert "tickets next" in root.WORKER_PROMPT


def test_live_installer_writes_atm_alias(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "install_live_t809", ROOT / "scripts/install_live.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    live = tmp_path / "tools" / "tickets.py"
    aliases = installer.path_cli_aliases(live)
    names = {p.name for p in aliases}
    assert names == {"tickets.py", "atm.py"}
