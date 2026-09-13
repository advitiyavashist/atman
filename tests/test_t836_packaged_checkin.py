"""T-836: packaged checkin must start without repo-root tickets.py."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

from test_t492_checkin_dedup import SIX_FIELDS, _seed, _survived  # noqa: E402
from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def test_cli_imports_without_root_tickets_on_path(board, monkeypatch):
    src = str(ROOT / "src")
    monkeypatch.delitem(sys.modules, "tickets", raising=False)
    monkeypatch.delitem(sys.modules, "ticket_board.cli", raising=False)
    monkeypatch.delitem(sys.modules, "ticket_board.agent_checkin", raising=False)
    monkeypatch.syspath_prepend(src)
    # Hide the checkout tickets.py so this matches a wheel install.
    monkeypatch.chdir(board)
    cli = importlib.import_module("ticket_board.cli")
    _, path = _seed(board)
    cli.checkin(str(board), "bob")
    _, got = _survived(path)
    assert got == SIX_FIELDS, got


def test_tickets_help_from_src_path_without_root_module(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env.pop("TICKETS_DIR", None)
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import tickets"
         % str(ROOT / "src")],
        cwd=str(tmp_path),
        capture_output=True, text=True, env=env,
    )
    assert r.returncode != 0
    r = subprocess.run(
        [sys.executable, "-c",
         "import ticket_board.cli as c; print(c.checkin.__module__)"],
        cwd=str(tmp_path),
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ticket_board.agent_checkin" in r.stdout
