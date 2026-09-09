"""T-223 residual (T-291): drift is detected on every command, reported on none.

release_version() hashes the pinned release's three files on every CLI
invocation -- but the result was only ever shown via --help/--version (it
was argparse's epilog), so the fleet paid the hashing cost unconditionally
and a tampered release ran ordinary commands (e.g. `tickets board`) with no
warning at all. This fixes both halves: a cheap size check against the
manifest first (release_status(), skips hashing when nothing changed), and
a loud one-line stderr WARNING on ANY command when the release is drifted,
not just --version -- while the command still runs.
"""
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess

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


def _load_release_module(release_dir):
    """Import the installed release's own tickets.py, not the source checkout's --
    release_status() resolves paths off its own __file__, so it must be loaded
    from inside the release directory to find that release's release.json."""
    tickets_spec = importlib.util.spec_from_file_location(
        "tickets_release_%s" % release_dir.name, str(release_dir / "tickets.py"))
    module = importlib.util.module_from_spec(tickets_spec)
    tickets_spec.loader.exec_module(module)
    return module


def test_a_tampered_release_warns_on_an_ordinary_command_and_still_succeeds(source, tmp_path):
    repo, sha = source
    live = tmp_path / "tools/tickets.py"
    installer.install(repo, sha, live, activate=True)
    release = live.parent / "tickets-releases" / sha
    target = release / "board_backup.py"
    target.chmod(0o644)
    target.write_text("# modified installed dependency")

    # The exact reproduction from the ticket: an ordinary command, not --version.
    result = subprocess.run([str(live), "board"], cwd=tmp_path, text=True,
                             capture_output=True,
                             env=dict(installer.clean_env(), HOME=str(tmp_path)))
    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr and "DRIFTED" in result.stderr


def test_an_untampered_ordinary_command_prints_no_warning(source, tmp_path):
    repo, sha = source
    live = tmp_path / "tools/tickets.py"
    installer.install(repo, sha, live, activate=True)

    result = subprocess.run([str(live), "board"], cwd=tmp_path, text=True,
                             capture_output=True,
                             env=dict(installer.clean_env(), HOME=str(tmp_path)))
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_an_untampered_release_hashes_nothing_on_the_hot_path(source, tmp_path, monkeypatch):
    repo, sha = source
    live = tmp_path / "tools/tickets.py"
    installer.install(repo, sha, live, activate=True)
    release = live.parent / "tickets-releases" / sha
    module = _load_release_module(release)

    calls = []
    real_sha256 = hashlib.sha256
    def spy(*a, **kw):
        calls.append(1)
        return real_sha256(*a, **kw)
    monkeypatch.setattr(hashlib, "sha256", spy)

    status = module.release_status()
    assert status == "tickets commit %s (verified release)" % sha
    assert calls == [], "cheap size check against the manifest should skip hashing entirely"


def test_an_old_format_manifest_without_size_still_detects_drift(source, tmp_path):
    """Backward compat: a release installed before this fix wrote release.json
    with a bare hash string per file, not {"sha256", "size"}. That must still
    be caught -- by falling back to hashing every file, as it always did."""
    import json
    repo, sha = source
    live = tmp_path / "tools/tickets.py"
    installer.install(repo, sha, live, activate=True)
    release = live.parent / "tickets-releases" / sha

    manifest_path = release / "release.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"] = {name: entry["sha256"] for name, entry in manifest["files"].items()}
    manifest_path.chmod(0o644)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    module = _load_release_module(release)
    assert module.release_status() == "tickets commit %s (verified release)" % sha

    target = release / "board_backup.py"
    target.chmod(0o644)
    target.write_text("# modified installed dependency")
    assert "DRIFTED" in module.release_status()
