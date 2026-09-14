#!/usr/bin/env python3
"""Build a Homebrew-ready release tarball for a pinned, reviewed commit.

Packages exactly the files scripts/install_live.py exports (root tickets.py
and its siblings, plus the src/ticket_board/ package tree) so the tarball a
brew formula downloads is the same immutable-release bundle install_live.py
already proves with `smoke()`, not a second definition of "what ships".

Usage:
  python3 scripts/build_release_tarball.py --ref <sha-or-tag> [--outdir dist]

Prints the tarball path, its sha256 (the value a Homebrew formula's `sha256`
field pins), and the exported commit sha (what `atm --version` must report
once installed).
"""
import argparse
import hashlib
import json
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_live import digest, export_paths, git  # noqa: E402


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
            dest.chmod(0o555 if name == "tickets.py" else 0o444)
        manifest_path = stage / "release.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
        manifest_path.chmod(0o444)

        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(stage, arcname=top)

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
        print("sha256:  %s" % result["sha256"])


if __name__ == "__main__":
    main()
