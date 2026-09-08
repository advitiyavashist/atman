"""Pinned release delivery, isolated from the operator's board and home."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess

import pytest

import test_wakeup
from test_wakeup import board  # noqa: F401
from test_merge_repo_identity import test_cross_repo_pin_is_not_closed_and_same_repo_pin_still_closes as cross_repo_case

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_live", ROOT / "scripts/install_live.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    for name in installer.FILES:
        shutil.copy2(ROOT / name, repo / name)
    env = dict(installer.clean_env(), GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="t@t")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], env=env).decode().strip()
    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "fixture release")
    return repo, git("rev-parse", "HEAD")


def test_stage_activate_provenance_and_immutable_source(source, tmp_path):
    repo, sha = source
    live = tmp_path / "tools/tickets.py"
    release = installer.install(repo, sha, live)
    assert not live.exists()
    # Dirty source bytes must not leak into the pinned snapshot.
    (repo / "tickets.py").write_text("raise RuntimeError('dirty checkout')")
    installer.install(repo, sha, live, activate=True)
    assert os.access(live, os.X_OK)
    output = subprocess.check_output([str(live), "--version"], text=True)
    assert output.strip() == "tickets commit %s (verified release)" % sha
    assert sha in subprocess.check_output([str(live), "--help"], text=True)
    assert str(release / "tickets.py") in live.read_text()
    target = release / "board_backup.py"
    target.chmod(0o644)
    target.write_text("# modified installed dependency")
    assert "DRIFTED" in subprocess.check_output([str(live), "--version"], text=True)
    with pytest.raises(RuntimeError, match="drifted"):
        installer.install(repo, sha, live)


def test_unknown_live_bytes_refused_and_preserved_then_explicitly_adopted(source, tmp_path):
    repo, sha = source
    live = tmp_path / "tools/tickets.py"
    live.parent.mkdir()
    original = b"#!/usr/bin/env python3\nprint('live-only safety patch')\n"
    live.write_bytes(original)
    live.chmod(0o751)
    with pytest.raises(RuntimeError, match="unacknowledged"):
        installer.install(repo, sha, live, activate=True)
    assert live.read_bytes() == original
    with pytest.raises(RuntimeError, match="unacknowledged"):
        installer.install(repo, sha, live, activate=True, expected="wrong")
    installer.install(repo, sha, live, activate=True, expected=hashlib.sha256(original).hexdigest())
    backup = live.parent / "tickets-releases" / ("previous-" + hashlib.sha256(original).hexdigest())
    assert live.stat().st_mode & 0o777 == 0o751
    assert backup.read_bytes() == original
    assert backup.stat().st_mode & 0o777 == 0o751


def test_failed_post_activation_smoke_restores_previous_launcher(source, tmp_path, monkeypatch):
    repo, sha = source
    live = tmp_path / "tools/tickets.py"
    live.parent.mkdir()
    original = b"#!/usr/bin/env python3\nprint('old')\n"
    live.write_bytes(original)
    live.chmod(0o755)
    smoke = installer.smoke
    def fail_only_live(path, pin):
        if path == live:
            raise RuntimeError("activation smoke failed")
        smoke(path, pin)
    monkeypatch.setattr(installer, "smoke", fail_only_live)
    with pytest.raises(RuntimeError, match="activation smoke failed"):
        installer.install(repo, sha, live, activate=True, expected=hashlib.sha256(original).hexdigest())
    assert live.read_bytes() == original
    assert os.access(live, os.X_OK)


def test_installed_cli_cross_repo_guard_and_rotation(source, tmp_path, board, monkeypatch):
    repo, sha = source
    live = tmp_path / "tools/tickets.py"
    installer.install(repo, sha, live, activate=True)
    monkeypatch.setattr(test_wakeup, "TOOL", live)
    # Real CLI, separate repositories sharing an actual fetched commit.
    cross_repo_case(board)
    for i in range(15):
        result = test_wakeup.run(board, "msg", "rotation-%02d-" % i + "x" * 100,
                                 agent="sender", env={"TICKETS_MESSAGES_MAX_BYTES": "800"})
        assert result.returncode == 0, result.stderr
    assert list(board.glob("messages.20*.jsonl"))
    history = test_wakeup.run(board, "inbox", "--all", "--limit", "1000", "--keep")
    assert history.returncode == 0, history.stderr
    for i in range(15):
        assert "rotation-%02d-" % i in history.stdout
