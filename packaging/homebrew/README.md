# Homebrew distribution (T-865)

`Formula/atman.rb` installs `atm` (primary) and `tickets` (compatibility
alias) from a pinned, sha256-verified release tarball — the same bundle
`scripts/install_live.py` already exports and smoke-tests (root `tickets.py`
+ its siblings + `src/ticket_board/`). It does not add a second installer:
`install.sh` stays the documented path for development machines; this
formula is the pinned-release path for a fresh macOS user.

The live formula lives in `advitiyavashist/homebrew-tap` as
`Formula/atman.rb`. `packaging/homebrew/atman.rb` in this repo is a copy and
must stay in sync with that tap. Its `sha256` is the hash of the published
GitHub Release asset after it has been checked equal to the local build
(T-1108).

## Building a release tarball

```sh
python3 scripts/build_release_tarball.py --ref <reviewed-sha-or-tag> --outdir dist
# prints: commit, version, tarball path, local sha256
```

The tarball is byte-reproducible: tar members are sorted, uid/gid/mtime are
zeroed, modes are pinned, and the gzip wrapper uses mtime 0 with an empty
original-name field. Two local builds of the same ref match each other.

The tarball contains a `release.json` manifest hashing every shipped file.
`atm --version` reads that manifest at runtime and reports
`tickets commit <sha> (verified release)` only if every byte still matches —
the same drift check `tickets self` and the release launcher shim rely on.
That is what the formula's `test do` block asserts; it is not a weaker
"binary exists" check.

Do **not** hand-copy a sha256 into the formula without the equality check
below. v0.3.0's published asset was a plain local build from the old
non-reproducible builder (gzip FNAME `atman-0.3.0.tar`, OS=255, mtime about
3s before `createdAt`, tar uid 501/`runner`), and the formula was first
published with a different hand-copied hash. That is not GitHub serving
different bytes than were uploaded.

## Manual release runbook

Cut a release by hand when Actions is unavailable, or follow the same
hash-and-compare rule the workflow uses:

1. Tag the reviewed commit already on `origin/main` (not a pre-merge branch
   tip). `pyproject.toml`'s `version` plus a `vX.Y.Z` git tag is the pinned
   pair.

   ```sh
   git tag vX.Y.Z <reviewed-sha>
   git push origin vX.Y.Z
   ```

2. Build the tarball from that tag and note the printed local sha256:

   ```sh
   python3 scripts/build_release_tarball.py --ref vX.Y.Z --outdir dist
   LOCAL_SHA=$(python3 -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('dist/atman-X.Y.Z.tar.gz').read_bytes()).hexdigest())")
   ```

3. Create the GitHub Release and upload the local tarball as its asset
   (`atman-X.Y.Z.tar.gz`).

   ```sh
   gh release create vX.Y.Z dist/atman-X.Y.Z.tar.gz \
     --repo advitiyavashist/atman \
     --title vX.Y.Z \
     --notes-file <release-notes>
   ```

4. **Download the published asset, hash it, and FAIL if it differs from
   the local build.** Do not hash only `dist/` and paste that into the
   formula; do not skip the compare.

   ```sh
   mkdir -p /tmp/published
   gh release download vX.Y.Z \
     --repo advitiyavashist/atman \
     --pattern 'atman-X.Y.Z.tar.gz' \
     --dir /tmp/published
   PUBLISHED_SHA=$(python3 -c "import hashlib, pathlib; print(hashlib.sha256(pathlib.Path('/tmp/published/atman-X.Y.Z.tar.gz').read_bytes()).hexdigest())")
   test "$LOCAL_SHA" = "$PUBLISHED_SHA" || {
     echo "ERROR: published asset sha256 ($PUBLISHED_SHA) != local build ($LOCAL_SHA)" >&2
     exit 1
   }
   echo "published asset matches local build: $PUBLISHED_SHA"
   ```

5. Update **both** formulas with the tag URL and the published-asset sha256
   (`$PUBLISHED_SHA`):

   - `advitiyavashist/homebrew-tap` `Formula/atman.rb` (what `brew` installs)
   - `packaging/homebrew/atman.rb` in this repo (the in-tree copy)

6. Prove the published bytes, then the tap:

   ```sh
   tar -xzf /tmp/published/atman-X.Y.Z.tar.gz -C /tmp/atman-extract
   python3 /tmp/atman-extract/atman-X.Y.Z/tickets.py --version   # verified release
   brew install advitiyavashist/homebrew-tap/atman
   brew test atman
   ```

`.github/workflows/release-homebrew.yml` automates steps 2-5 on tag push.
It still needs a `HOMEBREW_TAP_TOKEN` repo secret (a PAT with push access to
the tap) for the bump-PR step; without it the release and tarball still
publish and the published-vs-local hash check still runs.

## What was actually proven locally (no tap, no CLT upgrade)

`brew install` on this dev machine refused to proceed past its Command Line
Tools version check (a host-level gate unrelated to this formula, and not
something this worker should push through on a shared machine). Instead the
exact sequence the `test do` block runs was executed directly against a
built tarball:

```sh
python3 scripts/build_release_tarball.py --ref <sha> --outdir /tmp/atman-release
tar -xzf /tmp/atman-release/atman-<version>.tar.gz -C /tmp/atman-extract
python3 /tmp/atman-extract/atman-<version>/tickets.py --version   # verified release
python3 /tmp/atman-extract/atman-<version>/tickets.py join brew-test-seat --roles backend
python3 /tmp/atman-extract/atman-<version>/tickets.py ui --port 18765 &  # then GET /board.json
```

All three passed against `atman-identity-cursor-0912@3a585b6`. `tests/
test_t865_homebrew_release.py` exercises the same chain (build, extract,
`--version`, `join`, `ui` health). `tests/test_t1108_reproducible_release.py`
locks byte-reproducible archives, published-vs-build equality in the
workflow, and the in-repo formula matching the tap's v0.3.0 pin.
