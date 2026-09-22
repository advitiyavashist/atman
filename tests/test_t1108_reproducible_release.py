"""T-1108: release tarballs are byte-reproducible; Homebrew pins the
published GitHub asset hash, not the local build.
"""
import gzip
import hashlib
import importlib.util
import io
import struct
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Served v0.3.0 asset digest (formula pin). That asset was a local build from
# the old non-reproducible builder, not a GitHub rewrite of uploaded bytes.
PUBLISHED_V030_SHA256 = "c125cb6f91f32e6168cd242c04efd1d09586f2b091451ff5821b747b6943cf33"
PUBLISHED_V030_URL = (
    "https://github.com/advitiyavashist/atman/releases/download/v0.3.0/atman-0.3.0.tar.gz"
)


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


def test_two_builds_of_the_same_ref_are_byte_identical(source, tmp_path):
    repo, sha = source
    first = builder.build(repo, sha, tmp_path / "dist-a")
    time.sleep(1.1)
    second = builder.build(repo, sha, tmp_path / "dist-b")
    left = Path(first["tarball"]).read_bytes()
    right = Path(second["tarball"]).read_bytes()
    assert left == right
    assert first["sha256"] == second["sha256"]
    assert hashlib.sha256(left).hexdigest() == first["sha256"]


def test_gzip_wrapper_has_fixed_mtime_and_empty_name(source, tmp_path):
    repo, sha = source
    result = builder.build(repo, sha, tmp_path / "dist")
    raw = Path(result["tarball"]).read_bytes()
    assert raw[:2] == b"\x1f\x8b"
    flags = raw[3]
    mtime = struct.unpack_from("<I", raw, 4)[0]
    assert mtime == 0
    assert flags & 0x08 == 0  # FNAME unset: empty original-name field


def test_tar_members_are_sorted_and_metadata_is_normalised(source, tmp_path):
    repo, sha = source
    result = builder.build(repo, sha, tmp_path / "dist")
    raw = Path(result["tarball"]).read_bytes()
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(raw)), mode="r:") as tar:
        infos = [info for info in tar.getmembers() if info.name]
    names = [info.name for info in infos]
    assert names == sorted(names)
    assert names[0].startswith("atman-")
    for info in infos:
        assert info.uid == 0
        assert info.gid == 0
        assert info.uname == ""
        assert info.gname == ""
        assert info.mtime == 0
        mode = info.mode & 0o777
        if info.isdir():
            assert mode == 0o755
        elif info.name.count("/") == 1 and info.name.endswith("/tickets.py"):
            assert mode == 0o555
        else:
            assert mode == 0o444


def test_workflow_hashes_the_downloaded_published_asset():
    text = (ROOT / ".github/workflows/release-homebrew.yml").read_text()
    assert "gh release download" in text
    assert "Hash the published GitHub Release asset" in text
    assert "Fail if published asset differs from the local build" in text
    assert "steps.build.outputs.sha256" in text
    assert "steps.published.outputs.sha256" in text
    assert "BUILD_SHA" in text and "PUBLISHED_SHA" in text
    assert "exit 1" in text
    _, _, after = text.partition("name: Bump the tap formula")
    assert after
    assert "steps.published.outputs.sha256" in after
    # Formula bump must pin the published hash, never the pre-upload build output alone.
    assert "steps.build.outputs.sha256" not in after


def test_runbook_forbids_hashing_the_local_tarball_without_compare():
    readme = (ROOT / "packaging/homebrew/README.md").read_text()
    assert "Download the published asset" in readme
    assert "FAIL if it differs" in readme
    assert "LOCAL_SHA" in readme and "PUBLISHED_SHA" in readme
    assert "not GitHub serving" in readme and "different bytes" in readme
    assert "hand-copied" in readme
    assert "GitHub Actions on this repository is currently blocked on billing" not in readme
    builder = (ROOT / "scripts/build_release_tarball.py").read_text()
    assert "old non-reproducible builder" in builder
    assert "gzip wrapper can differ from this file" not in builder
    assert "asset GitHub served" not in (ROOT / ".github/workflows/release-homebrew.yml").read_text()


def test_in_repo_formula_matches_published_v030_tap():
    formula = (ROOT / "packaging/homebrew/atman.rb").read_text()
    assert PUBLISHED_V030_URL in formula
    assert 'sha256 "%s"' % PUBLISHED_V030_SHA256 in formula
    assert "REPLACE_BEFORE_RELEASE" not in formula
    assert "v0.2.0" not in formula


def test_readme_status_table_cites_synced_formula():
    """#270 leftover: README cited packaging/homebrew/atman.rb while that
    copy still pointed at v0.2.0 / a placeholder sha, and the table header
    stayed 'Status on 2026-09-15' after the Homebrew row changed.
    """
    readme = (ROOT / "README.md").read_text()
    formula = (ROOT / "packaging/homebrew/atman.rb").read_text()
    assert "| Claim | Status on 2026-09-19 |" in readme
    assert "Status on 2026-09-15" not in readme
    assert "`packaging/homebrew/atman.rb`" in readme
    assert "`v0.3.0`" in readme
    assert PUBLISHED_V030_URL in formula
    assert 'sha256 "%s"' % PUBLISHED_V030_SHA256 in formula
    assert "REPLACE_BEFORE_RELEASE" not in formula
    assert "v0.2.0" not in formula
