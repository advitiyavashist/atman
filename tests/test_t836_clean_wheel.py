"""T-836: a real wheel install must expose ticket_board and the tickets console."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode != 0,
    reason="pip is required for the clean-wheel acceptance",
)
def test_pip_wheel_installs_named_package_and_tickets_console(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    built = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-w", str(dist)],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert built.returncode == 0, built.stdout + built.stderr
    wheels = sorted(dist.glob("ticket_board-*.whl"))
    unknown = list(dist.glob("UNKNOWN-*.whl"))
    assert not unknown, "setuptools fell back to UNKNOWN: %s" % unknown
    assert wheels, "expected ticket_board-*.whl, got %s\n%s" % (
        list(dist.iterdir()), built.stdout + built.stderr)

    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    pip = venv / "bin" / "pip"
    tickets = venv / "bin" / "tickets"
    installed = subprocess.run(
        [str(pip), "install", str(wheels[0]), "--no-deps", "--no-cache-dir"],
        capture_output=True, text=True,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert tickets.is_file(), "tickets console script missing from isolated venv"

    help_run = subprocess.run(
        [str(tickets), "--help"], capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "TICKETS_DIR"},
        cwd=str(tmp_path),
    )
    assert help_run.returncode == 0, help_run.stderr
    assert "join" in help_run.stdout
    probe = subprocess.run(
        [str(venv / "bin" / "python"), "-c",
         "import ticket_board.agent_checkin as m; print(m.checkin.__module__)"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert probe.returncode == 0, probe.stderr
    assert "ticket_board.agent_checkin" in probe.stdout
