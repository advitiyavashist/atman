"""T-1380: pip install wires atm/tickets to the same CLI as brew and install.sh."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode != 0,
    reason="pip is required for the clean-venv acceptance",
)
def test_pip_install_atm_version_and_help_match_full_cli(tmp_path):
    """Repro from the ticket: venv + pip install . must accept --version and
    expose the full command surface (steer, hooks, ...), not ticket_board.cli."""
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    pip = venv / "bin" / "pip"
    atm = venv / "bin" / "atm"
    tickets = venv / "bin" / "tickets"
    installed = subprocess.run(
        [str(pip), "install", str(ROOT), "--no-cache-dir"],
        capture_output=True, text=True,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert atm.is_file() and tickets.is_file()
    assert atm.read_bytes() == tickets.read_bytes()

    # Console scripts must load tickets:main, not ticket_board.cli:main.
    for script in (atm, tickets):
        body = script.read_text(encoding="utf-8")
        assert "tickets:main" in body or "from tickets import main" in body
        assert "ticket_board.cli" not in body

    env = {k: v for k, v in os.environ.items() if k != "TICKETS_DIR"}
    version = subprocess.run(
        [str(atm), "--version"], capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert version.returncode == 0, version.stderr
    assert "0.3.0" in version.stdout
    assert "unrecognized arguments" not in (version.stdout + version.stderr).lower()

    help_run = subprocess.run(
        [str(atm), "--help"], capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert help_run.returncode == 0, help_run.stderr
    out = help_run.stdout
    assert "usage: atm" in out
    for cmd in ("steer", "hooks", "spawn", "knowledge", "self"):
        assert cmd in out, "pip atm --help missing %s (still the old cli.py?)" % cmd

    # python -m ticket_board must not resurrect the old surface either.
    mod = subprocess.run(
        [str(venv / "bin" / "python"), "-m", "ticket_board", "--version"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert mod.returncode == 0, mod.stderr
    assert "0.3.0" in mod.stdout
