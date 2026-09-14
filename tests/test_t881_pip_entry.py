"""T-881: pip `tickets` script is the full runtime (ui, quickstart, --version)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_script_is_full_entry():
    body = (ROOT / "pyproject.toml").read_text()
    assert 'tickets = "ticket_board.entry:main"' in body
    assert "ticket_board.cli:main" not in body.split("[project.scripts]", 1)[1].split("[", 1)[0]


def test_entry_resolves_repo_tickets_py():
    from ticket_board.entry import tickets_py_path
    path = tickets_py_path()
    assert path is not None
    assert path.resolve() == (ROOT / "tickets.py").resolve()


def test_entry_version_ui_quickstart_are_known(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir()
    env.pop("TICKETS_DIR", None)

    def run_argv(argv):
        code = (
            "import sys; from ticket_board.entry import main; "
            "sys.argv = %r; main()" % (argv,)
        )
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ROOT), env=env, capture_output=True, text=True)

    r = run_argv(["tickets", "--version"])
    assert r.returncode == 0, r.stderr + r.stdout
    assert r.stdout.strip()

    r = run_argv(["tickets", "ui", "-h"])
    assert r.returncode == 0, r.stderr + r.stdout
    assert "8765" in r.stdout or "command board" in r.stdout.lower() or "ui" in r.stdout.lower()

    r = run_argv(["tickets", "quickstart", "-h"])
    assert r.returncode == 0, r.stderr + r.stdout
    assert "quickstart" in r.stdout.lower() or "sample" in r.stdout.lower() or "agent" in r.stdout.lower()
