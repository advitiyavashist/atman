"""T-623: immutable live-release must ship src/ticket_board/ so turns/cost
metrics work from an arbitrary cwd with no PYTHONPATH.

The pre-T-623 installer exported only tickets.py + two helper modules. Every
root-CLI command that imports ticket_board then raised ModuleNotFoundError
outside a checkout (or any env without site-packages). This file locks the
exact staged-release shape and the adversarial isolation the installer smoke
uses: python -S, PYTHONNOUSERSITE=1, cwd != release dir, TICKETS_DIR isolated.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_live", ROOT / "scripts/install_live.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    installer.seed_fixture_repo(repo, ROOT)
    env = dict(installer.clean_env(), GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="t@t")

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], env=env).decode().strip()

    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "fixture release")
    return repo, git("rev-parse", "HEAD")


def _seed_board(board):
    board.mkdir()
    for name, content in (("tickets.json", "[]"), ("sprints.json", "[]"),
                          ("roles.json", "{}"), ("agents.json", "[]")):
        (board / name).write_text(content)


def _broken_release(tmp_path):
    """Pre-T-623 export: root files only, no src/ticket_board/."""
    release = tmp_path / "broken-release"
    release.mkdir()
    for name in installer.FILES:
        shutil.copy2(ROOT / name, release / name)
    manifest = {"commit": "deadbeef", "files": {}}
    for name in installer.FILES:
        data = (release / name).read_bytes()
        manifest["files"][name] = {"sha256": installer.digest(data), "size": len(data)}
    (release / "release.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    return release


def _run_release(release, board_dir, cwd, *args):
    env = dict(installer.clean_env(), TICKETS_DIR=str(board_dir), HOME=str(board_dir.parent),
               TICKET_AGENT="", PYTHONNOUSERSITE="1")
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-S", str(release / "tickets.py"), *args],
        capture_output=True, text=True, env=env, cwd=str(cwd))


def test_pre_t623_release_shape_fails_ticket_board_imports(tmp_path):
    """Documents the failure this ticket fixes: no package beside tickets.py."""
    release = _broken_release(tmp_path)
    board = tmp_path / "board"
    _seed_board(board)
    arbitrary = tmp_path / "elsewhere"
    arbitrary.mkdir()
    for cmd in (("turns", "--json"), ("route",)):
        r = _run_release(release, board, arbitrary, *cmd)
        assert r.returncode != 0, cmd
        assert "ModuleNotFoundError" in r.stderr, cmd
    # ui --json falls back to an empty turns snapshot when ticket_board is missing.
    ui = _run_release(release, board, arbitrary, "ui", "--json")
    assert ui.returncode == 0, ui.stderr
    snap = json.loads(ui.stdout)
    assert snap["turns"]["aggregates"]["n"] == 0
    assert _run_release(release, board, arbitrary, "turns", "--json").returncode != 0


def test_install_live_staged_release_metrics_from_arbitrary_cwd(source, tmp_path):
    repo, sha = source
    live = tmp_path / "tools/tickets.py"
    release = installer.install(repo, sha, live, activate=False)
    assert (release / "src" / "ticket_board" / "turns.py").is_file()
    board = tmp_path / "board"
    _seed_board(board)
    arbitrary = tmp_path / "elsewhere"
    arbitrary.mkdir()
    env = dict(installer.clean_env(), TICKETS_DIR=str(board), HOME=str(tmp_path),
               TICKET_AGENT="", PYTHONNOUSERSITE="1")
    env.pop("PYTHONPATH", None)
    py = [sys.executable, "-S", str(release / "tickets.py")]

    def run(*args):
        r = subprocess.run([*py, *args], cwd=str(arbitrary), env=env,
                             capture_output=True, text=True)
        assert r.returncode == 0, (args, r.stderr)
        assert "ModuleNotFoundError" not in r.stderr
        return r.stdout

    assert run("--version").strip() == "tickets commit %s (verified release)" % sha
    turns = json.loads(run("turns", "--json"))
    assert turns["v"] == 1
    util = json.loads(run("util", "--json"))
    assert "agents" in util
    run("route")
    snap = json.loads(run("ui", "--json"))
    assert snap["turns"]["v"] == 1
    assert snap["turns"] == turns


def test_release_manifest_lists_every_exported_byte(source, tmp_path):
    repo, sha = source
    live = tmp_path / "tools/tickets.py"
    release = installer.install(repo, sha, live, activate=False)
    manifest = json.loads((release / "release.json").read_text())
    expected = set(installer.export_paths(repo, sha))
    assert set(manifest["files"]) == expected
    installed = {
        str(path.relative_to(release))
        for path in release.rglob("*")
        if path.is_file() and path.name != "release.json"
    }
    assert installed == expected
    for rel in sorted(expected):
        path = release / rel
        assert path.is_file(), rel
        recorded = manifest["files"][rel]
        data = path.read_bytes()
        assert recorded["size"] == len(data)
        assert recorded["sha256"] == installer.digest(data)


def test_tampered_package_file_is_drifted(source, tmp_path):
    repo, sha = source
    live = tmp_path / "tools/tickets.py"
    installer.install(repo, sha, live, activate=True)
    release = live.parent / "tickets-releases" / sha
    target = release / "src/ticket_board/prices.json"
    target.chmod(0o644)
    original = target.read_text()
    tampered = original.replace('"v": 1', '"v": 9', 1)
    assert len(tampered) == len(original)
    target.write_text(tampered)

    tickets_spec = importlib.util.spec_from_file_location(
        "tickets_release_%s" % release.name, str(release / "tickets.py"))
    module = importlib.util.module_from_spec(tickets_spec)
    tickets_spec.loader.exec_module(module)
    assert "DRIFTED" in module.release_status()
    assert "src/ticket_board/prices.json" in module.release_status()


def test_unmanifested_package_file_is_drifted(source, tmp_path):
    repo, sha = source
    live = tmp_path / "tools/tickets.py"
    release = installer.install(repo, sha, live, activate=False)
    extra = release / "src/ticket_board/injected.py"
    extra.write_text("raise RuntimeError('unmanifested code executed')\n")

    tickets_spec = importlib.util.spec_from_file_location(
        "tickets_release_%s" % release.name, str(release / "tickets.py"))
    module = importlib.util.module_from_spec(tickets_spec)
    tickets_spec.loader.exec_module(module)
    assert "DRIFTED" in module.release_status()
    assert "src/ticket_board/injected.py" in module.release_status()
