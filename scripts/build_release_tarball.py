#!/usr/bin/env python3
"""Build a Homebrew-ready release tarball for a pinned, reviewed commit.

Packages exactly the files scripts/install_live.py exports (root tickets.py
and its siblings, plus the src/ticket_board/ package tree) so the tarball a
brew formula downloads is the same immutable-release bundle install_live.py
already proves with `smoke()`, not a second definition of "what ships".

The archive is byte-reproducible: tar members are added in sorted order with
uid/gid/uname/gname/mtime zeroed and modes pinned, and the gzip wrapper uses
mtime 0 and an empty original-name field (T-1108). Two builds of the same
ref produce the same sha256.

The printed sha256 is of the *local* file. Homebrew must pin the sha256 of
the asset GitHub actually serves after upload -- that gzip wrapper can differ
from this file. See packaging/homebrew/README.md.

Usage:
  python3 scripts/build_release_tarball.py --ref <sha-or-tag> [--outdir dist]

Prints the tarball path, its local sha256, and the exported commit sha (what
`atm --version` must report once installed).
"""
import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_live import digest, export_paths, git  # noqa: E402

# Pinned archive metadata so the same commit always yields the same bytes.
_GZIP_MTIME = 0
_GZIP_COMPRESSLEVEL = 9
_DIR_MODE = 0o755
_FILE_MODE = 0o444
_TICKETS_MODE = 0o555


def _release_version(repo, sha):
    """pyproject.toml [project] version at sha, falling back to the short sha."""
    import re
    try:
        text = git(repo, "show", sha + ":pyproject.toml").decode()
        match = re.search(r'^version = "([^"]+)"', text, re.M)
        if match:
            return match.group(1)
    except Exception:
        pass
    return sha[:12]


def _stage_members(stage, top):
    """(path, arcname) pairs: the top directory, then every descendant, sorted."""
    members = [(stage, top)]
    for path in stage.rglob("*"):
        members.append((path, "%s/%s" % (top, path.relative_to(stage).as_posix())))
    members.sort(key=lambda item: item[1])
    return members


def _normalized_tarinfo(tar, path, arcname, stage):
    info = tar.gettarinfo(name=str(path), arcname=arcname)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.devmajor = 0
    info.devminor = 0
    if path.is_dir():
        info.mode = _DIR_MODE
        info.type = tarfile.DIRTYPE
        info.size = 0
    else:
        info.mode = _TICKETS_MODE if path.name == "tickets.py" and path.parent == stage else _FILE_MODE
        info.size = path.stat().st_size
    return info


def write_reproducible_tarball(tarball, stage, top):
    """Write stage/ as top/ into tarball with fixed tar + gzip headers."""
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for path, arcname in _stage_members(stage, top):
            info = _normalized_tarinfo(tar, path, arcname, stage)
            if path.is_dir():
                tar.addfile(info)
            else:
                with path.open("rb") as fh:
                    tar.addfile(info, fh)
    with tarball.open("wb") as fh:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=fh,
            mtime=_GZIP_MTIME,
            compresslevel=_GZIP_COMPRESSLEVEL,
        ) as gz:
            gz.write(tar_buf.getvalue())


def build(repo, ref, outdir):
    repo = Path(repo)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    sha = git(repo, "rev-parse", "--verify", ref + "^{commit}").decode().strip()
    version = _release_version(repo, sha)
    paths = export_paths(repo, sha)
    payload = {name: git(repo, "show", sha + ":" + name) for name in paths}
    manifest = {"commit": sha, "files": {name: {"sha256": digest(data), "size": len(data)}
                                          for name, data in payload.items()}}

    top = "atman-%s" % version
    tarball = outdir / ("%s.tar.gz" % top)
    with tempfile.TemporaryDirectory(prefix="atman-release-") as scratch:
        stage = Path(scratch) / top
        for name, data in payload.items():
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            dest.chmod(_TICKETS_MODE if name == "tickets.py" else _FILE_MODE)
        manifest_path = stage / "release.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
        manifest_path.chmod(_FILE_MODE)
        write_reproducible_tarball(tarball, stage, top)

    tarball_sha256 = hashlib.sha256(tarball.read_bytes()).hexdigest()
    return {"commit": sha, "version": version, "tarball": str(tarball),
            "sha256": tarball_sha256, "files": sorted(payload)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", required=True, help="reviewed commit or tag to export")
    parser.add_argument("--outdir", type=Path, default=Path("dist"))
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args()
    result = build(args.repo.resolve(), args.ref, args.outdir)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("commit:  %s" % result["commit"])
        print("version: %s" % result["version"])
        print("tarball: %s" % result["tarball"])
        print("sha256:  %s  (local file; pin Homebrew to the downloaded published asset)" % result["sha256"])


if __name__ == "__main__":
    main()
