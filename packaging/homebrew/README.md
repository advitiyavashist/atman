# Homebrew distribution (T-865)

`Formula/atman.rb` installs `atm` (primary) and `tickets` (compatibility
alias) from a pinned, sha256-verified release tarball — the same bundle
`scripts/install_live.py` already exports and smoke-tests (root `tickets.py`
+ its siblings + `src/ticket_board/`). It does not add a second installer:
`install.sh` stays the documented path for development machines; this
formula is the pinned-release path for a fresh macOS user.

## Building a release tarball

```sh
python3 scripts/build_release_tarball.py --ref <reviewed-sha-or-tag> --outdir dist
# prints: commit, version, tarball path, sha256
```

The tarball contains a `release.json` manifest hashing every shipped file.
`atm --version` reads that manifest at runtime and reports
`tickets commit <sha> (verified release)` only if every byte still matches —
the same drift check `tickets self` and the release launcher shim rely on.
That is what the formula's `test do` block asserts; it is not a weaker
"binary exists" check.

## Publishing a release (once T-809 is merged)

1. Tag the merged commit on `origin/main` (not the pre-merge branch tip this
   formula currently points at — `pyproject.toml`'s `version` plus a `vX.Y.Z`
   git tag is the pinned pair).
2. Run `build_release_tarball.py --ref <tag>`, upload the resulting
   `dist/atman-<version>.tar.gz` as a GitHub Release asset on that tag.
3. Update `Formula/atman.rb`'s `url` (tag) and `sha256` (script output) and
   push that formula to `advitiyavashist/homebrew-tap` (`Formula/atman.rb`).
4. `brew install advitiyavashist/homebrew-tap/atman`, `brew test atman`.

`.github/workflows/release-homebrew.yml` automates steps 2-3 on tag push, but
still needs two things this worker cannot provision on its own:
- the `advitiyavashist/homebrew-tap` repository to exist (`brew tap-new
  advitiyavashist/homebrew-tap` from a machine with push access, or
  `gh repo create`);
- a `HOMEBREW_TAP_TOKEN` repo secret (a PAT with push access to that tap
  repo) for the bump-PR step.

Both are one-time, account-level setup with real external side effects
(a new public repo, a stored credential), so they are left for the operator
or CEO to approve explicitly rather than done unilaterally from this ticket.

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
`--version`, `join`, `ui` health) so CI keeps proving it without needing
`brew` at all.
