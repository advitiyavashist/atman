"""tickets self: which script is live, without touching a board."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def test_self_on_dev_checkout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    bin_dir = home / ".local/bin"
    bin_dir.mkdir(parents=True)
    link = bin_dir / "tickets"
    link.symlink_to(TOOL)
    env = dict(os.environ, HOME=str(home), PATH=str(bin_dir),
               TICKETS_DIR=str(tmp_path / "isolated-board"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    out = subprocess.check_output([sys.executable, str(TOOL), "self"], text=True, env=env)
    assert "script: %s" % TOOL.resolve() in out
    assert "uninstalled checkout" in out
    assert "PATH:" in out


def test_self_on_pinned_release(tmp_path, monkeypatch):
    """Release launcher execs a pinned copy; self reports both."""
    home = tmp_path / "home"
    releases = home / ".claude/tools/tickets-releases/abc123"
    releases.mkdir(parents=True)
    shutil.copy2(TOOL, releases / "tickets.py")
    for name in ("ticket_coordination.py", "board_backup.py"):
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, releases / name)
    manifest = {"commit": "abc123", "files": {}}
    for name in ("tickets.py", "ticket_coordination.py", "board_backup.py"):
        path = releases / name
        if not path.exists():
            continue
        data = path.read_bytes()
        manifest["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    (releases / "release.json").write_text(json.dumps(manifest) + "\n")
    live = home / ".claude/tools/tickets.py"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("#!/usr/bin/env python3\nimport os, sys\n"
                    "os.execv(sys.executable, [sys.executable, %r] + sys.argv[1:])\n"
                    % str(releases / "tickets.py"))
    live.chmod(0o755)
    bin_dir = home / ".local/bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "tickets").symlink_to(live)
    env = dict(os.environ, HOME=str(home), PATH=str(bin_dir),
               TICKETS_DIR=str(tmp_path / "isolated-board"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    out = subprocess.check_output([sys.executable, str(live), "self"], text=True, env=env, timeout=15)
    assert "abc123" in out
    assert "verified release" in out
    assert "release launcher shim" in out
    assert str(releases / "tickets.py") in out


def test_self_needs_no_board(tmp_path, monkeypatch):
    env = dict(os.environ, TICKETS_DIR=str(tmp_path / "isolated-board"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.chdir(tmp_path)
    result = subprocess.run([sys.executable, str(TOOL), "self"], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
