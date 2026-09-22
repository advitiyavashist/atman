#!/usr/bin/env python3
"""Pin packaging/homebrew/atman.rb to one built tarball (T-866).

Rewrites the formula's `url` and `sha256` lines from a build_release_tarball.py
`--json` manifest so the SAME formula that will ship from the tap can be
`brew install --formula`-ed against a tarball served from anywhere -- a
GitHub Release asset, or a loopback http.server in CI. Nothing else in the
formula is touched, which is the point: CI proves the real formula, not a
test double.

Usage:
  python3 scripts/pin_homebrew_formula.py --formula packaging/homebrew/atman.rb \\
      --release-json dist/release.json --url-base http://127.0.0.1:8766/ --out /tmp/atman.rb
"""
import argparse
import json
import os
import re
import sys

URL_RE = re.compile(r'^(\s*url\s+)"[^"]*"', re.M)
SHA_RE = re.compile(r'^(\s*sha256\s+)"[^"]*"', re.M)


def pin(formula_text, tarball_url, sha256):
    """Return the formula with url/sha256 replaced; raise if either is missing."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
        raise ValueError("sha256 must be 64 hex chars, got %r" % (sha256,))
    text, n_url = URL_RE.subn(lambda m: '%s"%s"' % (m.group(1), tarball_url), formula_text, count=1)
    text, n_sha = SHA_RE.subn(lambda m: '%s"%s"' % (m.group(1), sha256), text, count=1)
    if n_url != 1 or n_sha != 1:
        raise ValueError("formula has no url/sha256 line to pin (url=%d sha256=%d)" % (n_url, n_sha))
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--formula", required=True, help="source formula (left untouched)")
    ap.add_argument("--release-json", required=True, help="build_release_tarball.py --json output")
    ap.add_argument("--url-base", required=True, help="prefix the tarball basename is appended to")
    ap.add_argument("--out", required=True, help="where to write the pinned formula (dir is created)")
    a = ap.parse_args(argv)
    with open(a.release_json) as f:
        release = json.load(f)
    base = a.url_base if a.url_base.endswith("/") else a.url_base + "/"
    url = base + os.path.basename(release["tarball"])
    with open(a.formula) as f:
        pinned = pin(f.read(), url, release["sha256"])
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        f.write(pinned)
    print("pinned %s -> %s\n  url    %s\n  sha256 %s" % (a.formula, a.out, url, release["sha256"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
