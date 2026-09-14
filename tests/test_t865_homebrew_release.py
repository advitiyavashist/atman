"""T-865: the Homebrew release tarball must be the same verified-release
bundle install_live.py already exports and smoke-tests, and it must actually
pass the acceptance checks the atman.rb formula's `test do` block runs
(atm --version reporting a verified release, atm join on an isolated board,
atm ui answering /board.json) -- proven here without needing `brew` itself,
so CI can run it anywhere.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tarfile
import time
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = _load("install_live", ROOT / "scripts/install_live.py")
builder = _load("build_release_tarball", ROOT / "scripts/build_release_tarball.py")


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


def test_tarball_sha256_matches_the_reported_value(source, tmp_path):
    repo, sha = source
    result = builder.build(repo, sha, tmp_path / "dist")
    tarball = Path(result["tarball"])
    assert tarball.is_file()
    assert hashlib.sha256(tarball.read_bytes()).hexdigest() == result["sha256"]
    assert result["commit"] == sha


def test_tarball_manifest_hashes_every_shipped_file(source, tmp_path):
    repo, sha = source
    result = builder.build(repo, sha, tmp_path / "dist")
    extract = tmp_path / "extract"
    extract.mkdir()
    with tarfile.open(result["tarball"]) as tar:
        tar.extractall(extract)  # noqa: S202 - trusted fixture output, not user input
    top = next(extract.iterdir())
    manifest = json.loads((top / "release.json").read_text())
    assert manifest["commit"] == sha
    for name, recorded in manifest["files"].items():
        data = (top / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == recorded["sha256"]
        assert len(data) == recorded["size"]


def _extracted_tickets_py(source, tmp_path):
    repo, sha = source
    result = builder.build(repo, sha, tmp_path / "dist")
    extract = tmp_path / "extract"
    extract.mkdir()
    with tarfile.open(result["tarball"]) as tar:
        tar.extractall(extract)  # noqa: S202
    top = next(extract.iterdir())
    return top / "tickets.py", sha


def test_extracted_tarball_reports_a_verified_release(source, tmp_path):
    """Exactly what the formula's test do block asserts on `atm --version`."""
    tickets_py, sha = _extracted_tickets_py(source, tmp_path)
    out = subprocess.check_output([str(tickets_py), "--version"], text=True,
                                  env=dict(installer.clean_env(), HOME=str(tmp_path)))
    assert out.strip() == "tickets commit %s (verified release)" % sha


def test_extracted_tarball_joins_an_isolated_board(source, tmp_path):
    """Exactly what the formula's test do block asserts on `atm join`."""
    tickets_py, _ = _extracted_tickets_py(source, tmp_path)
    board = tmp_path / "board"
    env = dict(installer.clean_env(), HOME=str(tmp_path), TICKETS_DIR=str(board),
               TICKET_AGENT="brew-test")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    out = subprocess.check_output(
        [str(tickets_py), "join", "brew-test-seat", "--roles", "backend"],
        text=True, cwd=str(cwd), env=env)
    assert "joined as brew-test-seat" in out
    assert board.is_dir()


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_extracted_tarball_ui_answers_board_json(source, tmp_path):
    """Exactly what the formula's test do block asserts on `atm ui` ("app health")."""
    tickets_py, _ = _extracted_tickets_py(source, tmp_path)
    board = tmp_path / "board"
    env = dict(installer.clean_env(), HOME=str(tmp_path), TICKETS_DIR=str(board),
               TICKET_AGENT="brew-test")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    subprocess.check_output([str(tickets_py), "join", "brew-test-seat", "--roles", "backend"],
                            cwd=str(cwd), env=env)

    port = _free_port()
    proc = subprocess.Popen([str(tickets_py), "ui", "--port", str(port), "--host", "127.0.0.1"],
                            cwd=str(cwd), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        body = None
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/board.json" % port, timeout=1) as resp:
                    assert resp.status == 200
                    body = json.loads(resp.read())
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(0.25)
        assert body is not None, "atm ui never answered /board.json"
        assert "counts" in body
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_formula_points_at_a_github_release_tarball_and_pins_a_sha256():
    formula = (ROOT / "packaging/homebrew/atman.rb").read_text()
    assert 'url "https://github.com/advitiyavashist/atman/releases/download/' in formula
    assert "sha256 " in formula
    assert 'bin/name' in formula or '"atm"' in formula
    assert "tickets" in formula
