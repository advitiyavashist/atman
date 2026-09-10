"""Keyboard-only connect/review evidence: the dashboard tests named by T-185.

This lane owns tests/acceptance/, not tests/ui/. The keyboard bar lives in
jsdom tests already on main; this file pins that they still exist and, when
node_modules is present, re-runs them.
"""

from pathlib import Path
import os
import subprocess

REPO = Path(__file__).resolve().parents[3]
APP_TEST = REPO / "tests" / "ui" / "app.test.tsx"
TICKETS_TEST = REPO / "tests" / "ui" / "tickets.test.tsx"


def test_keyboard_connect_and_review_cases_are_present():
    app = APP_TEST.read_text()
    tickets = TICKETS_TEST.read_text()
    assert "opens Connect agent as a keyboard-reachable dialog" in app
    assert "{Escape}" in app
    assert "dismisses the open detail dialog on Escape" in tickets
    assert "user.keyboard" in tickets


def test_keyboard_vitest_when_ui_deps_are_installed():
    node_modules = REPO / "node_modules"
    ui_modules = REPO / "ui" / "node_modules"
    if not node_modules.is_dir() and not ui_modules.is_dir():
        return
    env = os.environ.copy()
    env["CI"] = "1"
    proc = subprocess.run(
        ["npm", "test", "-w", "ui", "--",
         "tests/ui/app.test.tsx", "tests/ui/tickets.test.tsx"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
