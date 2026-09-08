"""T-544: pytest tests/runners tests/server together must collect.

On bare main the combined invocation fails collection: tests/runners files
did `from conftest import in_process_opener` and tests/server/conftest.py
shadows the bare `conftest` name. Mutation: restore that import in a planted
file collected with tests/server → ImportError quoting in_process_opener.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env():
    e = dict(os.environ)
    e["PYTHONPATH"] = "src:."
    e.pop("PYTEST_CURRENT_TEST", None)
    return e


def test_combined_runners_and_server_collect():
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/runners", "tests/server",
         "--collect-only", "-q"],
        cwd=str(ROOT), env=_env(), capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ImportError" not in r.stdout + r.stderr
    assert "in_process_opener" not in (r.stdout + r.stderr) or "error" not in (
        r.stdout + r.stderr
    ).lower()


def test_restored_shadowing_import_fails_collection(tmp_path):
    planted = tmp_path / "test_t544_planted_shadow.py"
    planted.write_text(
        "from conftest import in_process_opener\n"
        "def test_placeholder():\n"
        "    assert in_process_opener is not None\n"
    )
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/server", str(planted),
         "--collect-only", "-q"],
        cwd=str(ROOT), env=_env(), capture_output=True, text=True,
    )
    blob = r.stdout + r.stderr
    assert r.returncode != 0, blob
    assert "ImportError" in blob
    assert "in_process_opener" in blob
